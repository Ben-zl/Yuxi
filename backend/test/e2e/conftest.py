from __future__ import annotations

import os
import sys
from collections.abc import AsyncGenerator
from pathlib import Path
from uuid import uuid4

import anyio
import httpx
import pytest
import pytest_asyncio
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from test.live_api_cleanup import (  # noqa: E402
    cleanup_orphaned_static_test_departments,
    cleanup_provisioned_sandboxes,
    cleanup_orphaned_static_test_user_workspace_roots,
    cleanup_static_test_user_resources,
    cleanup_test_chat_resources,
    cleanup_pytest_knowledge_resources,
    ensure_static_test_admin_identity,
    is_static_test_uid,
    list_test_sandbox_ids,
    list_static_test_user_uids,
)
from test.e2e.agentscope_e2e_fixtures import run_e2e_cleanup_steps  # noqa: E402
from yuxi.config.runtime import lite_mode_enabled  # noqa: E402

load_dotenv(PROJECT_ROOT / ".env", override=False)
load_dotenv(PROJECT_ROOT / "test/.env.test", override=False)

E2E_BASE_URL = os.getenv("TEST_BASE_URL", os.getenv("API_BASE_URL", "http://localhost:5050")).rstrip("/")
E2E_USERNAME = os.getenv("E2E_USERNAME") or os.getenv("TEST_USERNAME")
E2E_PASSWORD = os.getenv("E2E_PASSWORD") or os.getenv("TEST_PASSWORD")
CLEANUP_USERNAME = E2E_USERNAME or os.getenv("TEST_USERNAME")
CLEANUP_PASSWORD = E2E_PASSWORD or os.getenv("TEST_PASSWORD")
E2E_TIMEOUT = httpx.Timeout(300.0, connect=10.0)
LITE_MODE = lite_mode_enabled()
SANDBOX_PROVISIONER_URL = os.getenv("SANDBOX_PROVISIONER_URL", "http://sandbox-provisioner:8002").rstrip("/")
SANDBOX_PROVISIONER_TOKEN = os.getenv("SANDBOX_PROVISIONER_TOKEN", "")


def _require_e2e_credentials() -> tuple[str, str]:
    if not E2E_USERNAME or not E2E_PASSWORD:
        pytest.skip(
            "E2E credentials are not configured via E2E_USERNAME / E2E_PASSWORD or TEST_USERNAME / TEST_PASSWORD."
        )
    return E2E_USERNAME, E2E_PASSWORD


def _static_test_user_cleanup_targets(discovered_uids: list[str], *, cleanup_uid: str) -> list[str]:
    """排除当前认证身份，返回可物理删除的静态测试用户。"""
    return [uid for uid in discovered_uids if uid != cleanup_uid]


def _final_static_test_user_cleanup_target(
    discovered_uids: list[str],
    *,
    cleanup_uid: str,
    final_teardown: bool,
) -> str | None:
    """只在会话最终清理时返回当前认证的静态测试身份。"""
    if final_teardown and cleanup_uid in discovered_uids:
        return cleanup_uid
    return None


async def _cleanup_e2e_resources(
    *,
    client,
    headers: dict[str, str],
    cleanup_uid: str,
    final_teardown: bool,
    primary_error: BaseException | None,
) -> None:
    """通过 cleanup fence 清理完整 E2E 会话资源。"""

    sandbox_ids: set[str] | None = None
    static_targets: list[str] | None = None
    chat_cleaned = False
    static_cleaned = False

    async def capture_sandbox_ids() -> None:
        nonlocal sandbox_ids, static_targets
        discovered = await list_static_test_user_uids()
        static_targets = _static_test_user_cleanup_targets(discovered, cleanup_uid=cleanup_uid)
        sandbox_ids = set()
        for owner_uid in [cleanup_uid, *static_targets]:
            sandbox_ids.update(await list_test_sandbox_ids(owner_uid, include_all_owned=owner_uid != cleanup_uid))

    async def cleanup_chat() -> None:
        nonlocal chat_cleaned
        await cleanup_test_chat_resources(client, headers, owner_uid=cleanup_uid)
        chat_cleaned = True

    async def cleanup_other_static_users() -> None:
        nonlocal static_cleaned
        targets = static_targets
        if targets is None:
            discovered = await list_static_test_user_uids()
            targets = _static_test_user_cleanup_targets(discovered, cleanup_uid=cleanup_uid)
        await run_e2e_cleanup_steps(
            [
                (
                    f"E2E static user {static_uid}",
                    lambda static_uid=static_uid: cleanup_static_test_user_resources(static_uid),
                )
                for static_uid in targets
            ],
            primary_error=None,
        )
        static_cleaned = True

    async def cleanup_sandboxes() -> None:
        if sandbox_ids is None or not chat_cleaned or not static_cleaned:
            raise RuntimeError("E2E Sandbox cleanup requires captured IDs and completed Run cleanup")
        if not sandbox_ids:
            return
        if not SANDBOX_PROVISIONER_TOKEN:
            raise RuntimeError("SANDBOX_PROVISIONER_TOKEN is required for owned E2E sandbox cleanup")
        async with httpx.AsyncClient(
            base_url=SANDBOX_PROVISIONER_URL,
            timeout=E2E_TIMEOUT,
            follow_redirects=True,
        ) as provisioner_client:
            await cleanup_provisioned_sandboxes(
                provisioner_client,
                {"Authorization": f"Bearer {SANDBOX_PROVISIONER_TOKEN}"},
                sandbox_ids,
            )

    async def cleanup_knowledge() -> None:
        if not LITE_MODE:
            await cleanup_pytest_knowledge_resources(client, headers)

    await run_e2e_cleanup_steps(
        [
            ("E2E sandbox identities", capture_sandbox_ids),
            ("E2E current user chat resources", cleanup_chat),
            ("E2E static users", cleanup_other_static_users),
            ("E2E orphan departments", cleanup_orphaned_static_test_departments),
            ("E2E orphan user workspaces", cleanup_orphaned_static_test_user_workspace_roots),
            ("E2E sandboxes", cleanup_sandboxes),
            ("E2E knowledge resources", cleanup_knowledge),
        ],
        primary_error=primary_error,
    )


async def _run_authenticated_e2e_cleanup(
    *,
    final_teardown: bool,
    primary_error: BaseException | None,
) -> None:
    """登录临时管理员并执行需要 HTTP 授权的会话清理。"""
    async with httpx.AsyncClient(
        base_url=E2E_BASE_URL,
        timeout=E2E_TIMEOUT,
        follow_redirects=True,
    ) as client:
        response = await client.post(
            "/api/auth/token",
            data={"username": CLEANUP_USERNAME, "password": CLEANUP_PASSWORD},
        )
        if response.status_code != 200:
            raise RuntimeError(f"E2E cleanup login failed (status={response.status_code}): {response.text}")

        access_token = response.json().get("access_token")
        if not access_token:
            raise RuntimeError("E2E cleanup login succeeded but no access token was returned")

        headers = {"Authorization": f"Bearer {access_token}"}
        current_user = await client.get("/api/auth/me", headers=headers)
        if current_user.status_code != 200:
            raise RuntimeError(f"E2E cleanup failed to read current user: {current_user.text}")
        if current_user.json().get("role") not in {"admin", "superadmin"}:
            raise RuntimeError("E2E cleanup credentials must belong to an admin or superadmin")

        cleanup_uid = str(current_user.json().get("uid") or "")
        if not cleanup_uid:
            raise RuntimeError("E2E cleanup current user payload is missing uid")
        await _cleanup_e2e_resources(
            client=client,
            headers=headers,
            cleanup_uid=cleanup_uid,
            final_teardown=final_teardown,
            primary_error=primary_error,
        )


async def _run_e2e_cleanup_session(
    final_teardown: bool,
    primary_error: BaseException | None,
) -> None:
    """覆盖 provisioning、认证失败和 final identity 的完整会话清理边界。"""
    static_identity = is_static_test_uid(CLEANUP_USERNAME)
    failures: list[BaseException] = []
    if static_identity:
        try:
            await ensure_static_test_admin_identity(CLEANUP_USERNAME, CLEANUP_PASSWORD)
        except BaseException as exc:
            failures.append(exc)

    try:
        await _run_authenticated_e2e_cleanup(
            final_teardown=final_teardown,
            primary_error=primary_error,
        )
    except BaseException as exc:
        failures.append(exc)

    if static_identity and (final_teardown or failures):
        try:
            await cleanup_static_test_user_resources(CLEANUP_USERNAME)
        except BaseException as exc:
            failures.append(exc)

    if not failures:
        return
    if primary_error is not None:
        summary = ", ".join(type(exc).__name__ for exc in failures)
        primary_error.add_note(f"E2E session cleanup also failed: {summary}")
        return
    if len(failures) == 1:
        raise failures[0]
    raise BaseExceptionGroup("E2E session cleanup failed", failures)


@pytest.fixture(scope="session")
def e2e_base_url() -> str:
    return E2E_BASE_URL


@pytest.fixture(scope="session", autouse=True)
def cleanup_e2e_test_resources(e2e_base_url: str):
    """在 E2E 会话前后清理测试对话、临时智能体和知识库。"""

    if not CLEANUP_USERNAME or not CLEANUP_PASSWORD:
        yield
        return

    del e2e_base_url
    anyio.run(_run_e2e_cleanup_session, False, None)
    try:
        yield
    finally:
        anyio.run(_run_e2e_cleanup_session, True, None)


@pytest_asyncio.fixture(scope="function")
async def e2e_client(e2e_base_url: str) -> AsyncGenerator[httpx.AsyncClient, None]:
    async with httpx.AsyncClient(base_url=e2e_base_url, timeout=E2E_TIMEOUT, follow_redirects=True) as client:
        yield client


@pytest_asyncio.fixture(scope="function")
async def e2e_headers(e2e_client: httpx.AsyncClient) -> dict[str, str]:
    username, password = _require_e2e_credentials()
    response = await e2e_client.post("/api/auth/token", data={"username": username, "password": password})
    if response.status_code != 200:
        pytest.fail(f"E2E login failed (status={response.status_code}): {response.text}")

    access_token = response.json().get("access_token")
    if not access_token:
        pytest.fail("E2E login succeeded but no access token was returned.")
    return {"Authorization": f"Bearer {access_token}"}


@pytest_asyncio.fixture(scope="function", autouse=True)
async def isolate_e2e_async_runtime():
    """避免测试进程内的异步数据库和 Redis 单例跨 pytest event loop 复用。"""
    from yuxi.storage.postgres.manager import pg_manager
    from yuxi.storage.redis.manager import close_async_redis_client

    if pg_manager.async_engine is not None:
        await pg_manager.close()
    pg_manager._initialized = False
    yield

    async def close_postgres() -> None:
        if pg_manager.async_engine is not None:
            await pg_manager.close()

    try:
        await run_e2e_cleanup_steps(
            [
                ("close E2E Redis client", close_async_redis_client),
                ("close E2E PostgreSQL manager", close_postgres),
            ],
            primary_error=None,
        )
    finally:
        pg_manager._initialized = False


@pytest_asyncio.fixture(scope="function")
async def e2e_mock_model_spec(
    e2e_client: httpx.AsyncClient,
    e2e_headers: dict[str, str],
) -> AsyncGenerator[str, None]:
    """通过真实管理 API 创建当前测试独占的 OpenAI-compatible mock 模型。"""
    suffix = uuid4().hex[:8]
    response = await e2e_client.post(
        "/api/system/model-providers",
        headers=e2e_headers,
        json={
            "provider_id": f"e2e-openai-mock-{suffix}",
            "display_name": f"E2E OpenAI mock {suffix}",
            "provider_type": "openai",
            "base_url": "http://openai-mock:8080/v1",
            "api_key": "e2e-mock-key",
            "capabilities": ["chat"],
            "enabled_models": [
                {
                    "id": "mock-chat-model",
                    "display_name": "Mock chat model",
                    "type": "chat",
                    "source": "manual",
                    "context_length": 32768,
                }
            ],
            "is_enabled": True,
            "share_config": {
                "version": 2,
                "read_scope": {
                    "access_level": "global",
                    "department_ids": [],
                    "user_uids": [],
                },
                "manage_scope": None,
            },
        },
    )
    assert response.status_code == 200, response.text
    resource_id = str(response.json()["data"]["resource_id"])
    try:
        yield f"{resource_id}:mock-chat-model"
    finally:
        delete_response = await e2e_client.delete(
            f"/api/system/model-providers/{resource_id}",
            headers=e2e_headers,
        )
        assert delete_response.status_code in {200, 404}, delete_response.text


@pytest_asyncio.fixture(scope="function")
async def e2e_agent_context(e2e_client: httpx.AsyncClient, e2e_headers: dict[str, str]) -> dict[str, str]:
    me_response = await e2e_client.get("/api/auth/me", headers=e2e_headers)
    if me_response.status_code != 200:
        pytest.fail(
            f"Failed to fetch current user for E2E tests (status={me_response.status_code}): {me_response.text}"
        )
    uid = me_response.json().get("uid")
    if not uid:
        pytest.fail("Current user payload missing uid field for E2E tests.")

    default_response = await e2e_client.get("/api/agent/default", headers=e2e_headers)
    if default_response.status_code == 200:
        agent = default_response.json().get("agent") or {}
    else:
        response = await e2e_client.get("/api/agent", headers=e2e_headers)
        if response.status_code != 200:
            pytest.fail(f"Failed to list agents for E2E tests (status={response.status_code}): {response.text}")
        agents = response.json().get("agents") or []
        if not agents:
            pytest.fail("No agents are available for E2E tests.")
        agent = agents[0]

    agent_slug = agent.get("slug") or agent.get("agent_id")
    if not agent_slug:
        pytest.fail(f"Agent payload missing slug/agent_id field for E2E tests: {agent}")

    return {"agent_slug": str(agent_slug), "agent_id": str(agent_slug), "uid": str(uid)}
