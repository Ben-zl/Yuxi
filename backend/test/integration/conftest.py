"""
Shared pytest fixtures for integration tests that exercise the live API service.
"""

from __future__ import annotations

import os
import sys
import uuid
from collections.abc import AsyncGenerator, Callable
from pathlib import Path

import anyio
import asyncpg
import httpx
import pytest
import pytest_asyncio
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from test.live_api_cleanup import (  # noqa: E402
    cleanup_provisioned_sandboxes,
    cleanup_pytest_knowledge_resources,
    cleanup_test_chat_resources,
    list_test_sandbox_ids,
)
from yuxi.config.runtime import lite_mode_enabled  # noqa: E402

load_dotenv(PROJECT_ROOT / ".env", override=False)
load_dotenv(PROJECT_ROOT / "test/.env.test", override=False)

API_BASE_URL = os.getenv("TEST_BASE_URL", "http://localhost:5050").rstrip("/")
ADMIN_LOGIN = os.getenv("TEST_USERNAME")
ADMIN_PASSWORD = os.getenv("TEST_PASSWORD")
LITE_MODE = lite_mode_enabled()

_ADMIN_TOKEN_CACHE: str | None = None
HTTP_TIMEOUT = httpx.Timeout(60.0, connect=5.0)
SANDBOX_PROVISIONER_URL = os.getenv("SANDBOX_PROVISIONER_URL", "http://sandbox-provisioner:8002").rstrip("/")
SANDBOX_PROVISIONER_TOKEN = os.getenv("SANDBOX_PROVISIONER_TOKEN", "")


@pytest.fixture(scope="session", autouse=True)
def ensure_live_api_schema():
    if not ADMIN_LOGIN or not ADMIN_PASSWORD:
        return

    async def verify_schema_version() -> None:
        from yuxi.storage.postgres.manager import pg_manager

        pg_manager.initialize()
        await pg_manager.require_current_schema(include_knowledge=not LITE_MODE)

    anyio.run(verify_schema_version)


def _require_admin_credentials() -> tuple[str, str]:
    if not ADMIN_LOGIN or not ADMIN_PASSWORD:
        pytest.skip("Integration credentials are not configured via TEST_USERNAME / TEST_PASSWORD.")
    return ADMIN_LOGIN, ADMIN_PASSWORD


@pytest_asyncio.fixture(scope="function")
async def test_client() -> AsyncGenerator[httpx.AsyncClient, None]:
    async with httpx.AsyncClient(base_url=API_BASE_URL, timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
        yield client


@pytest_asyncio.fixture(scope="function")
async def admin_token() -> str:
    global _ADMIN_TOKEN_CACHE

    if _ADMIN_TOKEN_CACHE:
        return _ADMIN_TOKEN_CACHE

    username, password = _require_admin_credentials()

    async with httpx.AsyncClient(
        base_url=API_BASE_URL,
        timeout=HTTP_TIMEOUT,
        follow_redirects=True,
    ) as bootstrap_client:
        response = await bootstrap_client.post(
            "/api/auth/token",
            data={"username": username, "password": password},
        )

        if response.status_code == 401:
            first_run_response = await bootstrap_client.get("/api/auth/check-first-run")
            if first_run_response.status_code == 200 and first_run_response.json().get("first_run", False):
                pytest.fail(
                    "Super admin account has not been initialized. Complete `/api/auth/initialize` before "
                    "running integration tests."
                )

    if response.status_code != 200:
        pytest.fail(f"Failed to authenticate as admin (status={response.status_code}): {response.text}")

    token = response.json().get("access_token")
    if not token:
        pytest.fail("Admin authentication did not return an access token.")

    _ADMIN_TOKEN_CACHE = token
    return token


@pytest.fixture(scope="function")
def admin_headers(admin_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {admin_token}"}


@pytest_asyncio.fixture(scope="function")
async def department_headers(test_client) -> Callable[[dict[str, str]], dict[str, str]]:
    """为部门写请求附加当前会话的 X-Department-Revision。

    以传入 headers 的 Bearer 调用 /api/auth/me 取 context_revision 并复制副本返回；
    不修改共享 admin_headers。陈旧 revision 负向测试应自行保存旧副本。
    """

    async def _build(headers: dict[str, str]) -> dict[str, str]:
        me = await test_client.get("/api/auth/me", headers=headers)
        assert me.status_code == 200, me.text
        merged = dict(headers)
        merged["X-Department-Revision"] = str(me.json()["context_revision"])
        return merged

    return _build


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_knowledge_resources():
    async def run_cleanup() -> None:
        global _ADMIN_TOKEN_CACHE

        if not ADMIN_LOGIN or not ADMIN_PASSWORD:
            return

        if not _ADMIN_TOKEN_CACHE:
            async with httpx.AsyncClient(
                base_url=API_BASE_URL,
                timeout=HTTP_TIMEOUT,
                follow_redirects=True,
            ) as bootstrap_client:
                response = await bootstrap_client.post(
                    "/api/auth/token",
                    data={"username": ADMIN_LOGIN, "password": ADMIN_PASSWORD},
                )
                if response.status_code != 200:
                    raise RuntimeError(
                        f"Test resource cleanup login failed (status={response.status_code}): {response.text}"
                    )
                token = response.json().get("access_token")
                if not token:
                    raise RuntimeError("Test resource cleanup login succeeded but no access token was returned")
                _ADMIN_TOKEN_CACHE = token

        headers = {"Authorization": f"Bearer {_ADMIN_TOKEN_CACHE}"}

        async with httpx.AsyncClient(base_url=API_BASE_URL, timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
            current_user = await client.get("/api/auth/me", headers=headers)
            if current_user.status_code != 200:
                raise RuntimeError(f"Test resource cleanup failed to read current user: {current_user.text}")
            cleanup_uid = str(current_user.json().get("uid") or "")
            if not cleanup_uid:
                raise RuntimeError("Test resource cleanup current user payload is missing uid")

            sandbox_ids = await list_test_sandbox_ids(cleanup_uid)
            await cleanup_test_chat_resources(client, headers, owner_uid=cleanup_uid)
            if sandbox_ids:
                if not SANDBOX_PROVISIONER_TOKEN:
                    raise RuntimeError("SANDBOX_PROVISIONER_TOKEN is required for owned sandbox cleanup")
                provisioner_headers = {"Authorization": f"Bearer {SANDBOX_PROVISIONER_TOKEN}"}
                async with httpx.AsyncClient(
                    base_url=SANDBOX_PROVISIONER_URL,
                    timeout=HTTP_TIMEOUT,
                    follow_redirects=True,
                ) as provisioner_client:
                    await cleanup_provisioned_sandboxes(
                        provisioner_client,
                        provisioner_headers,
                        sandbox_ids,
                    )
            if not LITE_MODE:
                await cleanup_pytest_knowledge_resources(client, headers)

    anyio.run(run_cleanup)
    yield
    anyio.run(run_cleanup)


@pytest_asyncio.fixture(scope="function")
async def standard_user(test_client: httpx.AsyncClient, admin_headers: dict[str, str]) -> AsyncGenerator[dict, None]:
    username = f"pytest_user_{uuid.uuid4().hex[:8]}"
    password = f"Pw!{uuid.uuid4().hex[:8]}"

    # 用户隔离重构后所有登录用户必须绑定部门，创建时显式指定一个已存在部门
    dept_response = await test_client.get("/api/departments", headers=admin_headers)
    if dept_response.status_code != 200 or not dept_response.json():
        pytest.fail(f"No department available to bind standard user: {dept_response.text}")
    department_id = dept_response.json()[0]["id"]

    response = await test_client.post(
        "/api/auth/users",
        json={"username": username, "password": password, "role": "user", "department_id": department_id},
        headers=admin_headers,
    )
    if response.status_code != 200:
        pytest.fail(f"Failed to create standard user (status={response.status_code}): {response.text}")

    user_payload = response.json()

    # 部门权限来自成员关系：旧创建接口只写 User.department_id，这里直连 PG 补成员事实
    dsn = os.getenv("POSTGRES_URL", "postgresql+asyncpg://postgres:postgres@postgres:5432/yuxi").replace(
        "postgresql+asyncpg://", "postgresql://"
    )
    pg_conn = await asyncpg.connect(dsn)
    try:
        await pg_conn.execute(
            "INSERT INTO department_memberships (user_id, department_id, role, created_at) "
            "VALUES ($1, $2, 'user', NOW())",
            user_payload["id"],
            department_id,
        )
    finally:
        await pg_conn.close()

    login_response = await test_client.post(
        "/api/auth/token",
        data={"username": user_payload["uid"], "password": password},
    )
    if login_response.status_code != 200:
        pytest.fail(
            f"Failed to authenticate as standard user (status={login_response.status_code}): {login_response.text}"
        )

    access_token = login_response.json().get("access_token")
    if not access_token:
        pytest.fail("Standard user login succeeded but no access token was returned.")

    try:
        yield {
            "user": user_payload,
            "password": password,
            "headers": {"Authorization": f"Bearer {access_token}"},
        }
    finally:
        await cleanup_test_chat_resources(
            test_client,
            {"Authorization": f"Bearer {access_token}"},
            owner_uid=str(user_payload["uid"]),
        )
        # 成员事实先于账号软删除清除，避免残留关系阻碍部门清理
        cleanup_conn = await asyncpg.connect(dsn)
        try:
            await cleanup_conn.execute("DELETE FROM department_memberships WHERE user_id = $1", user_payload["id"])
        finally:
            await cleanup_conn.close()
        cleanup_error = None
        for _ in range(3):
            response = await test_client.delete(f"/api/auth/users/{user_payload['id']}", headers=admin_headers)
            if response.status_code in (200, 404):
                cleanup_error = None
                break
            cleanup_error = response
            await anyio.sleep(0.3)
        if cleanup_error is not None:
            assert cleanup_error.status_code == 200, (
                f"Failed to cleanup test user {user_payload['uid']}: {cleanup_error.text}"
            )


@pytest_asyncio.fixture(scope="function")
async def knowledge_database(
    test_client: httpx.AsyncClient,
    admin_headers: dict[str, str],
) -> AsyncGenerator[dict, None]:
    import time

    unique_id = uuid.uuid4().hex
    timestamp = int(time.time() * 1000000)
    db_name = f"pytest_kb_{timestamp}_{unique_id}"
    kb_id = None

    try:
        create_response = await test_client.post(
            "/api/knowledge/databases",
            json={
                "database_name": db_name,
                "description": "Pytest managed knowledge base",
                "embedding_model_spec": "siliconflow-cn:Pro/BAAI/bge-m3",
                "kb_type": "milvus",
                "additional_params": {},
            },
            headers=admin_headers,
        )

        if create_response.status_code == 200:
            db_payload = create_response.json()
            kb_id = db_payload["kb_id"]
        elif create_response.status_code == 409:
            error_detail = create_response.json().get("detail", "")
            pytest.fail(f"Knowledge database name conflict: {error_detail}. Please clean up old test databases first.")
        else:
            pytest.fail(
                f"Failed to create knowledge database (status={create_response.status_code}): {create_response.text}"
            )

        yield db_payload

    finally:
        if kb_id:
            try:
                delete_response = await test_client.delete(f"/api/knowledge/databases/{kb_id}", headers=admin_headers)
                if delete_response.status_code != 200:
                    print(f"Warning: Failed to cleanup knowledge database {kb_id}: {delete_response.text}")
            except Exception as exc:
                print(f"Warning: Exception during cleanup of {kb_id}: {exc}")
