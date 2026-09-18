"""
Integration tests for authentication-related API routes.
"""

from __future__ import annotations

import os
import uuid
from datetime import timedelta

import pytest
import pytest_asyncio
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.services import login_rate_limit_service as login_limiter
from yuxi.storage.postgres.models_business import User as UserModel
from yuxi.storage.redis import close_async_redis_client, create_async_redis_client, get_async_redis_client
from yuxi.utils.datetime_utils import utc_now_naive

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest.fixture()
async def isolated_redis_client():
    """把进程内共享 Redis 客户端绑定到当前用例的 loop，结束后关闭。

    集成套件跨用例共享 pytest 进程内的 Redis 单例；若其在上一个用例的 loop
    中创建、本用例 loop 中复用会报 "attached to a different loop"。先关闭再
    重建，保证本用例拿到绑定当前 loop 的新客户端。
    """
    await close_async_redis_client()
    client = await get_async_redis_client()
    yield client
    await close_async_redis_client()


async def _expire_login_lock(user_id: int) -> None:
    """用一次性引擎把用户锁定截止时间改到过去。

    不复用 pg_manager 的共享引擎：其连接池绑定在别的 loop 上，
    跨用例事件循环复用会报 "attached to a different loop"。
    """
    engine = create_async_engine(os.environ["POSTGRES_URL"])
    try:
        session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        async with session_factory() as session:
            await session.execute(
                update(UserModel)
                .where(UserModel.id == user_id)
                .values(login_locked_until=utc_now_naive() - timedelta(seconds=1))
            )
            await session.commit()
    finally:
        await engine.dispose()


async def _clear_login_failure_keys():
    # 每个用例的事件循环不同，不复用共享单例客户端
    redis = await create_async_redis_client()
    try:
        keys = [key async for key in redis.scan_iter(match="yuxi:login-failure:*")]
        if keys:
            await redis.delete(*keys)
    finally:
        await redis.aclose()


@pytest_asyncio.fixture(autouse=True)
async def _reset_login_rate_limits(test_client):
    """清理 Redis 登录失败计数，避免用例间通过共享 Redis 相互影响。"""
    await _clear_login_failure_keys()
    yield
    await _clear_login_failure_keys()
    # 一次成功登录会清空内存中间件在同一 IP 上的滑动窗口计数，
    # 避免失败密集的用例把同 IP 的后续用例顶到中间件限速。
    username = os.getenv("TEST_USERNAME")
    password = os.getenv("TEST_PASSWORD")
    if username and password:
        await test_client.post("/api/auth/token", data={"username": username, "password": password})


async def _require_superadmin(test_client, headers):
    response = await test_client.get("/api/auth/me", headers=headers)
    assert response.status_code == 200, response.text
    if response.json()["role"] != "superadmin":
        pytest.fail("This test requires TEST_USERNAME to be a superadmin account.")


async def _create_department_with_admin(test_client, headers, label: str) -> dict:
    suffix = uuid.uuid4().hex[:8]
    admin_uid = f"adm{label}_{suffix}"
    admin_password = f"Pw!{suffix}"
    response = await test_client.post(
        "/api/departments",
        json={
            "name": f"pytest_{label}_{suffix}",
            "description": "pytest managed department",
            "admin_uid": admin_uid,
            "admin_password": admin_password,
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text

    login_response = await test_client.post(
        "/api/auth/token",
        data={"username": admin_uid, "password": admin_password},
    )
    assert login_response.status_code == 200, login_response.text

    login_payload = login_response.json()
    return {
        "department": response.json(),
        "admin_id": login_payload["user_id"],
        "admin_headers": {"Authorization": f"Bearer {login_payload['access_token']}"},
    }


async def _create_user(test_client, headers, label: str, role: str = "user", department_id: int | None = None) -> dict:
    suffix = uuid.uuid4().hex[:8]
    payload = {
        "username": f"u{label}_{suffix}",
        "password": f"Pw!{suffix}",
        "role": role,
    }
    if department_id is not None:
        payload["department_id"] = department_id

    response = await test_client.post("/api/auth/users", json=payload, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


async def _cleanup_user(test_client, headers, user_id: int) -> None:
    response = await test_client.delete(f"/api/auth/users/{user_id}", headers=headers)
    assert response.status_code in {200, 404}, response.text


async def _cleanup_department(test_client, headers, department_id: int) -> None:
    response = await test_client.delete(f"/api/departments/{department_id}", headers=headers)
    assert response.status_code in {204, 404}, response.text


async def test_login_with_invalid_credentials(test_client):
    response = await test_client.post("/api/auth/token", data={"username": "invalid", "password": "invalid"})
    assert response.status_code == 401
    assert "detail" in response.json()


async def test_user_is_locked_after_repeated_failed_logins(test_client, standard_user):
    uid = standard_user["user"]["uid"]

    for attempt in range(1, 5):
        response = await test_client.post("/api/auth/token", data={"username": uid, "password": "wrong-password"})
        assert response.status_code == 401, response.text
        assert response.json()["detail"] == "用户名或密码错误"

    locked_response = await test_client.post("/api/auth/token", data={"username": uid, "password": "wrong-password"})
    assert locked_response.status_code == 423, locked_response.text
    assert "X-Lock-Remaining" in locked_response.headers
    assert "账户已被锁定" in locked_response.json()["detail"]

    still_locked_response = await test_client.post(
        "/api/auth/token",
        data={"username": uid, "password": standard_user["password"]},
    )
    assert still_locked_response.status_code == 423, still_locked_response.text
    assert "X-Lock-Remaining" in still_locked_response.headers
    assert "登录被锁定" in still_locked_response.json()["detail"]


async def test_login_rate_limit_blocks_repeated_failures_per_ip_and_account(test_client, isolated_redis_client):
    # 测试进程连 localhost:5050，服务端看到的来源 IP 是 127.0.0.1
    identifier = f"nouser_{uuid.uuid4().hex[:8]}"
    for _ in range(login_limiter.LOGIN_FAILURE_IP_ACCOUNT_MAX - 1):
        await login_limiter.record_login_failure("127.0.0.1", identifier)

    failure_response = await test_client.post(
        "/api/auth/token",
        data={"username": identifier, "password": "wrong-password"},
    )
    assert failure_response.status_code == 401, failure_response.text

    # 第 10 次失败后，同 IP+账号组合进入滑动窗口限速
    blocked_response = await test_client.post(
        "/api/auth/token",
        data={"username": identifier, "password": "wrong-password"},
    )
    assert blocked_response.status_code == 429, blocked_response.text
    assert int(blocked_response.headers["Retry-After"]) >= 1
    assert "过于频繁" in blocked_response.json()["detail"]


async def test_expired_lock_resets_failure_count_before_next_failure(test_client, standard_user):
    uid = standard_user["user"]["uid"]

    for _ in range(4):
        response = await test_client.post("/api/auth/token", data={"username": uid, "password": "wrong-password"})
        assert response.status_code == 401, response.text
    # 第 5 次失败触发账号锁定
    locked_response = await test_client.post("/api/auth/token", data={"username": uid, "password": "wrong-password"})
    assert locked_response.status_code == 423, locked_response.text

    # 把锁定截止时间改到过去，模拟锁定到期
    await _expire_login_lock(standard_user["user"]["id"])

    # 锁定过期后首次失败应是普通 401，而不是失败计数残留导致的立即再锁定
    wrong_response = await test_client.post("/api/auth/token", data={"username": uid, "password": "wrong-password"})
    assert wrong_response.status_code == 401, wrong_response.text

    # 正确密码可正常登录
    success_response = await test_client.post(
        "/api/auth/token",
        data={"username": uid, "password": standard_user["password"]},
    )
    assert success_response.status_code == 200, success_response.text


async def test_admin_can_login_and_fetch_profile(test_client, admin_headers):
    profile_response = await test_client.get("/api/auth/me", headers=admin_headers)
    assert profile_response.status_code == 200
    data = profile_response.json()
    assert data["role"] in {"admin", "superadmin"}
    assert data["username"]
    assert data["id"]


async def test_profile_requires_authentication(test_client):
    response = await test_client.get("/api/auth/me")
    assert response.status_code == 401
    assert response.json()["detail"] == "请登录后再访问"


async def test_admin_can_create_and_delete_user(test_client, admin_headers):
    suffix = uuid.uuid4().hex[:8]
    payload = {
        "username": f"rtu_{suffix}",
        "password": "routerTest123!",
    }
    create_response = await test_client.post("/api/auth/users", json=payload, headers=admin_headers)
    assert create_response.status_code == 200, create_response.text

    created_user = create_response.json()
    assert created_user["username"] == payload["username"]
    assert created_user["role"] == "user"

    delete_response = await test_client.delete(f"/api/auth/users/{created_user['id']}", headers=admin_headers)
    assert delete_response.status_code == 200, delete_response.text
    delete_payload = delete_response.json()
    assert delete_payload["success"] is True
    assert delete_payload["message"] == "用户已删除"

    list_response = await test_client.get("/api/auth/users?limit=1000", headers=admin_headers)
    assert list_response.status_code == 200, list_response.text
    assert created_user["id"] not in {user["id"] for user in list_response.json()}


async def test_admin_user_page_filters_before_pagination_and_excludes_deleted(test_client, admin_headers):
    suffix = uuid.uuid4().hex[:8]
    created_users = []
    try:
        for index in range(3):
            response = await test_client.post(
                "/api/auth/users",
                json={
                    "username": f"paged_{suffix}_{index}",
                    "password": "routerTest123!",
                },
                headers=admin_headers,
            )
            assert response.status_code == 200, response.text
            created_users.append(response.json())

        delete_response = await test_client.delete(f"/api/auth/users/{created_users[1]['id']}", headers=admin_headers)
        assert delete_response.status_code == 200, delete_response.text

        page_response = await test_client.get(
            "/api/auth/users/page",
            params={"search": f"paged_{suffix}", "offset": 1, "limit": 1},
            headers=admin_headers,
        )
        assert page_response.status_code == 200, page_response.text
        page = page_response.json()
        assert page["total"] == 2
        assert page["limit"] == 1
        assert page["offset"] == 1
        assert [item["id"] for item in page["items"]] == [created_users[2]["id"]]
        assert created_users[1]["id"] not in {item["id"] for item in page["items"]}
    finally:
        for user in created_users:
            await test_client.delete(f"/api/auth/users/{user['id']}", headers=admin_headers)


async def test_admin_password_mutations_reject_passwords_shorter_than_eight_characters(
    test_client, admin_headers, standard_user
):
    create_response = await test_client.post(
        "/api/auth/users",
        json={"username": f"weak_{uuid.uuid4().hex[:8]}", "password": "short"},
        headers=admin_headers,
    )
    assert create_response.status_code == 422, create_response.text
    assert create_response.json()["detail"][0]["loc"] == ["body", "password"]

    update_response = await test_client.put(
        f"/api/auth/users/{standard_user['user']['id']}",
        json={"password": "short"},
        headers=admin_headers,
    )
    assert update_response.status_code == 422, update_response.text
    assert update_response.json()["detail"][0]["loc"] == ["body", "password"]


async def test_department_admin_is_rejected_from_account_management(test_client, admin_headers):
    """账号管理为超管专属：部门管理员对账号 CRUD 全部 403。"""
    await _require_superadmin(test_client, admin_headers)

    user_ids: list[int] = []
    admin_ids: list[int] = []

    try:
        dept_a = await _create_department_with_admin(test_client, admin_headers, "a")
        department_a = dept_a["department"]
        admin_ids.append(dept_a["admin_id"])
        admin_headers_a = dept_a["admin_headers"]

        created = await test_client.post(
            "/api/auth/users",
            headers=admin_headers,
            json={"username": f"acct_{uuid.uuid4().hex[:8]}", "password": "routerTest123!"},
        )
        assert created.status_code == 200, created.text
        user_id = created.json()["id"]
        user_ids.append(user_id)

        forbidden_create = await test_client.post(
            "/api/auth/users",
            json={"username": f"ux_{uuid.uuid4().hex[:8]}", "password": "routerTest123!"},
            headers=admin_headers_a,
        )
        assert forbidden_create.status_code == 403, forbidden_create.text

        for method, url in (
            ("get", "/api/auth/users"),
            ("get", "/api/auth/users/page"),
            ("get", f"/api/auth/users/{user_id}"),
        ):
            response = await getattr(test_client, method)(url, headers=admin_headers_a)
            assert response.status_code == 403, response.text

        forbidden_update = await test_client.put(
            f"/api/auth/users/{user_id}",
            json={"username": f"ub_{uuid.uuid4().hex[:8]}"},
            headers=admin_headers_a,
        )
        assert forbidden_update.status_code == 403, forbidden_update.text

        forbidden_delete = await test_client.delete(f"/api/auth/users/{user_id}", headers=admin_headers_a)
        assert forbidden_delete.status_code == 403, forbidden_delete.text

        # 超管仍可完整管理
        superadmin_list = await test_client.get("/api/auth/users?limit=1000", headers=admin_headers)
        assert superadmin_list.status_code == 200
        assert user_id in {user["id"] for user in superadmin_list.json()}
    finally:
        for uid in user_ids + admin_ids:
            await _cleanup_user(test_client, admin_headers, uid)
        # 清除成员关系、会话活动部门引用后删除部门
        if dept_a["department"]["id"]:
            import asyncpg

            department_id = dept_a["department"]["id"]
            me = await test_client.get("/api/auth/me", headers=admin_headers)
            rev = {"X-Department-Revision": str(me.json()["context_revision"])}
            if dept_a["admin_id"]:
                await test_client.delete(
                    f"/api/departments/{department_id}/members/{dept_a['admin_id']}",
                    headers={**admin_headers, **rev},
                )
                dsn = os.getenv("POSTGRES_URL", "postgresql+asyncpg://postgres:postgres@postgres:5432/yuxi").replace(
                    "postgresql+asyncpg://", "postgresql://"
                )
                conn = await asyncpg.connect(dsn)
                try:
                    await conn.execute("DELETE FROM auth_sessions WHERE active_department_id = $1", department_id)
                    await conn.execute("UPDATE users SET department_id = NULL WHERE department_id = $1", department_id)
                finally:
                    await conn.close()
            deletion = await test_client.delete(f"/api/departments/{department_id}", headers=admin_headers)
            assert deletion.status_code == 204, deletion.text


async def test_invalid_token_is_rejected(test_client):
    headers = {"Authorization": "Bearer not-a-real-token"}
    response = await test_client.get("/api/auth/me", headers=headers)
    assert response.status_code == 401


async def test_deleted_user_token_is_rejected(test_client, admin_headers, standard_user):
    user_id = standard_user["user"]["id"]

    delete_response = await test_client.delete(f"/api/auth/users/{user_id}", headers=admin_headers)
    assert delete_response.status_code == 200, delete_response.text

    profile_response = await test_client.get("/api/auth/me", headers=standard_user["headers"])
    assert profile_response.status_code == 401


async def test_locked_user_token_is_rejected(test_client, standard_user):
    uid = standard_user["user"]["uid"]

    for _ in range(5):
        await test_client.post("/api/auth/token", data={"username": uid, "password": "wrong-password"})

    profile_response = await test_client.get("/api/auth/me", headers=standard_user["headers"])
    assert profile_response.status_code == 423
    assert "X-Lock-Remaining" in profile_response.headers
