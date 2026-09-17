"""登录会话部门上下文的 HTTP 集成测试：两会话隔离、切换、撤权、退出。"""

from __future__ import annotations

import os
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from yuxi.utils.auth_utils import AuthUtils

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


class _Case:
    """单测试用例的账号、部门与令牌集合。"""

    def __init__(self) -> None:
        suffix = uuid.uuid4().hex[:8]
        self.uid = f"deptctx_{suffix}"
        self.username = f"部门上下文测试{suffix}"
        self.password = "Deptctx-Pass-2026"
        self.department_ids: list[int] = []
        self.user_id: int | None = None
        self.session_ids: list[str] = []


async def _login(client, uid: str, password: str) -> dict:
    response = await client.post("/api/auth/token", data={"username": uid, "password": password})
    assert response.status_code == 200, response.text
    return response.json()


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def session_case(test_client, admin_headers):
    case = _Case()
    engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    try:
        # 超管创建无部门账号（uid 由服务端生成），再直连 PG 建两个部门与成员关系
        created = await test_client.post(
            "/api/auth/users",
            headers=admin_headers,
            json={"password": case.password, "username": case.username},
        )
        assert created.status_code == 200, created.text
        case.user_id = created.json()["id"]
        case.uid = created.json()["uid"]
        async with engine.begin() as conn:
            for name in ("deptctx-A", "deptctx-B"):
                row = await conn.execute(
                    text("INSERT INTO departments (name, created_at) VALUES (:name, NOW()) RETURNING id"),
                    {"name": f"{name}-{case.uid}"},
                )
                case.department_ids.append(row.scalar_one())
            await conn.execute(
                text(
                    "INSERT INTO department_memberships (user_id, department_id, role, created_at) "
                    "VALUES (:u, :a, 'admin', NOW()), (:u, :b, 'user', NOW())"
                ),
                {"u": case.user_id, "a": case.department_ids[0], "b": case.department_ids[1]},
            )
        yield case
    finally:
        # 清理：删除成员关系与会话行后走受保护的部门删除；账号软删除
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM department_memberships WHERE user_id = :u"), {"u": case.user_id})
            await conn.execute(text("DELETE FROM auth_sessions WHERE user_id = :u"), {"u": case.user_id})
        for department_id in case.department_ids:
            await test_client.delete(f"/api/departments/{department_id}", headers=admin_headers)
        await test_client.delete(f"/api/auth/users/{case.user_id}", headers=admin_headers)
        await engine.dispose()


async def test_two_sessions_keep_independent_departments(test_client, session_case):
    case = session_case
    first = await _login(test_client, case.uid, case.password)
    second = await _login(test_client, case.uid, case.password)
    case.session_ids = [first["session_id"], second["session_id"]]

    dept_a, dept_b = case.department_ids
    switch_first = await test_client.post(
        "/api/auth/department-context",
        headers=_headers(first["access_token"]),
        json={"department_id": dept_a, "expected_revision": first["context_revision"]},
    )
    assert switch_first.status_code == 200, switch_first.text
    assert switch_first.json()["department_id"] == dept_a
    assert switch_first.json()["role"] == "admin"

    switch_second = await test_client.post(
        "/api/auth/department-context",
        headers=_headers(second["access_token"]),
        json={"department_id": dept_b, "expected_revision": second["context_revision"]},
    )
    assert switch_second.status_code == 200
    assert switch_second.json()["department_id"] == dept_b

    # 交替读取：两会话各自部门互不干扰
    me_first = await test_client.get("/api/auth/me", headers=_headers(first["access_token"]))
    me_second = await test_client.get("/api/auth/me", headers=_headers(second["access_token"]))
    assert me_first.json()["department_id"] == dept_a
    assert me_second.json()["department_id"] == dept_b
    me_again = await test_client.get("/api/auth/me", headers=_headers(first["access_token"]))
    assert me_again.json()["department_id"] == dept_a


async def test_switch_rejects_conflict_missing_and_foreign(test_client, session_case):
    case = session_case
    login = await _login(test_client, case.uid, case.password)
    headers = _headers(login["access_token"])
    dept_a = case.department_ids[0]

    conflict = await test_client.post(
        "/api/auth/department-context",
        headers=headers,
        json={"department_id": dept_a, "expected_revision": login["context_revision"] + 100},
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "department_context_stale"

    missing = await test_client.post(
        "/api/auth/department-context",
        headers=headers,
        json={"department_id": 987654, "expected_revision": login["context_revision"]},
    )
    assert missing.status_code == 404

    engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    try:
        async with engine.begin() as conn:
            row = await conn.execute(
                text("INSERT INTO departments (name, created_at) VALUES (:name, NOW()) RETURNING id"),
                {"name": f"deptctx-foreign-{case.uid}"},
            )
            foreign_id = row.scalar_one()
        foreign = await test_client.post(
            "/api/auth/department-context",
            headers=headers,
            json={"department_id": foreign_id, "expected_revision": login["context_revision"]},
        )
        assert foreign.status_code == 403
    finally:
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM departments WHERE id = :id"), {"id": foreign_id})
        await engine.dispose()


async def test_membership_removal_invalidates_context_immediately(test_client, session_case):
    case = session_case
    login = await _login(test_client, case.uid, case.password)
    headers = _headers(login["access_token"])
    dept_a = case.department_ids[0]
    switched = await test_client.post(
        "/api/auth/department-context",
        headers=headers,
        json={"department_id": dept_a, "expected_revision": login["context_revision"]},
    )
    assert switched.status_code == 200

    engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM department_memberships WHERE user_id = :u AND department_id = :d"),
                {"u": case.user_id, "d": dept_a},
            )
        me = await test_client.get("/api/auth/me", headers=headers)
        assert me.status_code == 200
        assert me.json()["department_id"] is None
        assert me.json()["role"] == "user"
        # 部门级资源拒绝
        keys = await test_client.post("/api/user/apikey/", headers=headers, json={"name": "no-dept-key"})
        assert keys.status_code in (403, 404)
    finally:
        await engine.dispose()


async def test_logout_revokes_only_current_session(test_client, session_case):
    case = session_case
    first = await _login(test_client, case.uid, case.password)
    second = await _login(test_client, case.uid, case.password)

    logout = await test_client.post("/api/auth/logout", headers=_headers(first["access_token"]))
    assert logout.status_code == 204
    logout_again = await test_client.post("/api/auth/logout", headers=_headers(first["access_token"]))
    assert logout_again.status_code == 204

    me_first = await test_client.get("/api/auth/me", headers=_headers(first["access_token"]))
    assert me_first.status_code == 401
    me_second = await test_client.get("/api/auth/me", headers=_headers(second["access_token"]))
    assert me_second.status_code == 200


async def test_legacy_token_without_sid_is_rejected(test_client, session_case):
    case = session_case
    legacy = AuthUtils.create_access_token({"sub": str(case.user_id)})
    response = await test_client.get("/api/auth/me", headers=_headers(legacy))
    assert response.status_code == 401


async def test_api_key_cannot_switch_department(test_client, session_case):
    case = session_case
    login = await _login(test_client, case.uid, case.password)
    headers = _headers(login["access_token"])
    dept_b = case.department_ids[1]
    switched = await test_client.post(
        "/api/auth/department-context",
        headers=headers,
        json={"department_id": dept_b, "expected_revision": login["context_revision"]},
    )
    assert switched.status_code == 200

    created = await test_client.post(
        "/api/user/apikey/", headers=headers, json={"name": f"key-{case.uid}", "request_id": f"req-{case.uid}"}
    )
    assert created.status_code == 200, created.text
    key_token = created.json()["secret"]
    assert key_token
    denied = await test_client.post(
        "/api/auth/department-context",
        headers=_headers(key_token),
        json={"department_id": case.department_ids[0], "expected_revision": 0},
    )
    assert denied.status_code == 403


async def test_my_departments_lists_realtime_roles(test_client, session_case):
    case = session_case
    login = await _login(test_client, case.uid, case.password)
    response = await test_client.get("/api/auth/my-departments", headers=_headers(login["access_token"]))
    assert response.status_code == 200
    items = {item["id"]: item["role"] for item in response.json()["items"]}
    assert items == {case.department_ids[0]: "admin", case.department_ids[1]: "user"}


async def test_impersonation_creates_isolated_session(test_client, admin_headers, session_case):
    """模拟登录为目标账号创建独立会话；退出模拟会话不影响操作者会话。"""
    case = session_case
    impersonated = await test_client.post(f"/api/auth/impersonate/{case.user_id}", headers=admin_headers)
    assert impersonated.status_code == 200, impersonated.text
    target_token = impersonated.json()["access_token"]

    me_target = await test_client.get("/api/auth/me", headers=_headers(target_token))
    assert me_target.status_code == 200
    assert me_target.json()["uid"] == case.uid

    logout = await test_client.post("/api/auth/logout", headers=_headers(target_token))
    assert logout.status_code == 204
    # 操作者（超管）原会话不受影响
    me_admin = await test_client.get("/api/auth/me", headers=admin_headers)
    assert me_admin.status_code == 200


async def test_agent_task_endpoints_require_department_context(test_client, session_case):
    """定时任务交互入口：无登录 401；成员被移除（无部门）后 403 department_context_invalid。"""
    case = session_case
    anonymous = await test_client.get("/api/agent-tasks")
    assert anonymous.status_code == 401

    login = await _login(test_client, case.uid, case.password)
    headers = _headers(login["access_token"])
    with_dept = await test_client.get("/api/agent-tasks", headers=headers)
    assert with_dept.status_code == 200

    engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM department_memberships WHERE user_id = :u"), {"u": case.user_id})
        no_dept = await test_client.get("/api/agent-tasks", headers=headers)
        assert no_dept.status_code == 403
        assert no_dept.json()["detail"]["code"] == "department_context_invalid"
    finally:
        await engine.dispose()


async def test_stale_fold_returns_bumped_revision(test_client, session_case):
    """成员失效折叠后，/me 立即返回递增后的 revision，与后续读取一致。"""
    case = session_case
    login = await _login(test_client, case.uid, case.password)
    headers = _headers(login["access_token"])
    dept_a = case.department_ids[0]
    switched = await test_client.post(
        "/api/auth/department-context",
        headers=headers,
        json={"department_id": dept_a, "expected_revision": login["context_revision"]},
    )
    assert switched.status_code == 200
    folded_revision = switched.json()["context_revision"]

    engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM department_memberships WHERE user_id = :u"), {"u": case.user_id})
        first_me = await test_client.get("/api/auth/me", headers=headers)
        assert first_me.status_code == 200
        assert first_me.json()["department_id"] is None
        second_me = await test_client.get("/api/auth/me", headers=headers)
        assert second_me.json()["context_revision"] == first_me.json()["context_revision"]
        assert first_me.json()["context_revision"] > folded_revision

        # 无部门账号仍可自助修改个人资料
        profile = await test_client.put("/api/auth/profile", headers=headers, json={"username": f"改名{case.uid[-4:]}"})
        assert profile.status_code == 200
        assert profile.json()["department_id"] is None
    finally:
        await engine.dispose()


async def test_expired_and_locked_sessions_rejected(test_client, session_case):
    """过期会话与锁定账号分别被 401/423 拒绝。"""
    case = session_case
    login = await _login(test_client, case.uid, case.password)
    headers = _headers(login["access_token"])
    session_id = login["session_id"]

    engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE auth_sessions SET expires_at = (NOW() AT TIME ZONE 'UTC') - INTERVAL '1 hour' WHERE id = :s"
                ),
                {"s": session_id},
            )
        expired = await test_client.get("/api/auth/me", headers=headers)
        assert expired.status_code == 401

        refresh = await _login(test_client, case.uid, case.password)
        async with engine.begin() as conn:
            await conn.execute(
                text("UPDATE users SET login_locked_until = NOW() + INTERVAL '5 minutes' WHERE id = :u"),
                {"u": case.user_id},
            )
        locked = await test_client.get("/api/auth/me", headers=_headers(refresh["access_token"]))
        assert locked.status_code == 423
        async with engine.begin() as conn:
            await conn.execute(text("UPDATE users SET login_locked_until = NULL WHERE id = :u"), {"u": case.user_id})
    finally:
        await engine.dispose()
