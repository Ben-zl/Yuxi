"""同账号切换部门后的资源可见性 HTTP 集成测试。"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from test.integration.api.test_knowledge_router import _create_test_database, _create_test_department
from test.integration.conftest import admin_revision_headers

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest_asyncio.fixture
async def visibility_case(test_client, admin_headers):
    """部门 A/B + 同一账号（A 管理员、B 普通成员）+ A-only/global 资源。"""
    suffix = uuid.uuid4().hex[:8]
    password = "Visibility-Pass-2026"
    engine = create_async_engine(__import__("os").environ["POSTGRES_URL"], pool_pre_ping=True)
    department_ids: list[int] = []
    user_id = None
    try:
        department_a = await _create_test_department(test_client, admin_headers, f"vis_a_{suffix}")
        department_b = await _create_test_department(test_client, admin_headers, f"vis_b_{suffix}")
        department_ids = [department_a["id"], department_b["id"]]

        created = await test_client.post(
            "/api/auth/users",
            headers=admin_headers,
            json={"username": f"可视测试{suffix}", "password": password},
        )
        assert created.status_code == 200, created.text
        user_id = created.json()["id"]
        rev = await admin_revision_headers(test_client, admin_headers)
        await test_client.post(f"/api/departments/{department_ids[0]}/members", headers=rev, json={"user_id": user_id})
        await test_client.post(f"/api/departments/{department_ids[1]}/members", headers=rev, json={"user_id": user_id})
        # 两个部门均设 admin：内置模式知识库列表有管理员门禁，避免门禁噪音掩盖部门范围差异
        for dep_id in department_ids:
            await test_client.patch(f"/api/departments/{dep_id}/members/{user_id}", headers=rev, json={"role": "admin"})

        login = await test_client.post(
            "/api/auth/token", data={"username": created.json()["uid"], "password": password}
        )
        assert login.status_code == 200, login.text

        a_only = await _create_test_database(
            test_client,
            admin_headers,
            {
                "version": 2,
                "read_scope": {
                    "access_level": "department",
                    "department_ids": [department_ids[0]],
                    "user_uids": [],
                },
                "manage_scope": None,
            },
        )
        global_kb = await _create_test_database(
            test_client,
            admin_headers,
            {
                "version": 2,
                "read_scope": {"access_level": "global", "department_ids": [], "user_uids": []},
                "manage_scope": None,
            },
        )

        yield {
            "token": login.json()["access_token"],
            "revision": login.json()["context_revision"],
            "user_id": user_id,
            "department_ids": department_ids,
            "a_only_kb": a_only["kb_id"],
            "global_kb": global_kb["kb_id"],
        }

        await test_client.delete(f"/api/knowledge/databases/{a_only['kb_id']}", headers=admin_headers)
        await test_client.delete(f"/api/knowledge/databases/{global_kb['kb_id']}", headers=admin_headers)
    finally:
        conn_cleanup = None
        try:
            async with engine.begin() as conn:
                if user_id is not None:
                    await conn.execute(text("DELETE FROM department_memberships WHERE user_id = :u"), {"u": user_id})
                    await conn.execute(text("DELETE FROM auth_sessions WHERE user_id = :u"), {"u": user_id})
                    await conn.execute(text("UPDATE users SET department_id = NULL WHERE id = :u"), {"u": user_id})
                for dep_id in department_ids:
                    await conn.execute(
                        text("DELETE FROM department_memberships WHERE department_id = :d"), {"d": dep_id}
                    )
                    await conn.execute(text("DELETE FROM auth_sessions WHERE active_department_id = :d"), {"d": dep_id})
                    await conn.execute(
                        text("UPDATE users SET department_id = NULL WHERE department_id = :d"), {"d": dep_id}
                    )
            if user_id is not None:
                await test_client.delete(f"/api/auth/users/{user_id}", headers=admin_headers)
            for dep_id in department_ids:
                await test_client.delete(f"/api/departments/{dep_id}", headers=admin_headers)
        finally:
            await engine.dispose()


async def _visible_kb_ids(test_client, token: str) -> set:
    response = await test_client.get("/api/knowledge/databases", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200, response.text
    payload = response.json()
    items = payload.get("databases", payload) if isinstance(payload, dict) else payload
    return {db["kb_id"] for db in items}


async def test_switching_department_scopes_resource_visibility(test_client, visibility_case):
    case = visibility_case
    headers = {"Authorization": f"Bearer {case['token']}"}
    dept_a, dept_b = case["department_ids"]

    # 登录后活动部门为最小 ID；切到 B 后 A-only 不可见，global 仍可见
    switched = await test_client.post(
        "/api/auth/department-context",
        headers=headers,
        json={"department_id": dept_b, "expected_revision": case["revision"]},
    )
    assert switched.status_code == 200, switched.text
    visible_in_b = await _visible_kb_ids(test_client, case["token"])
    assert case["a_only_kb"] not in visible_in_b
    assert case["global_kb"] in visible_in_b

    # 切回 A 后恢复可见
    switched_back = await test_client.post(
        "/api/auth/department-context",
        headers=headers,
        json={"department_id": dept_a, "expected_revision": switched.json()["context_revision"]},
    )
    assert switched_back.status_code == 200
    visible_in_a = await _visible_kb_ids(test_client, case["token"])
    assert case["a_only_kb"] in visible_in_a
    assert case["global_kb"] in visible_in_a


async def test_membership_removal_hides_department_resources(test_client, visibility_case):
    """成员关系撤销后，部门级资源立即不可见（旧令牌不保留权限）。"""
    import os

    case = visibility_case
    engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM department_memberships WHERE department_id = :d AND user_id = :u"),
                {"d": case["department_ids"][0], "u": case["user_id"]},
            )
        # 活动部门（A）失效被折叠为无部门；读取最新 revision 后切到仍保留成员身份的 B
        me = await test_client.get("/api/auth/me", headers={"Authorization": f"Bearer {case['token']}"})
        assert me.status_code == 200
        assert me.json()["department_id"] is None
        switched = await test_client.post(
            "/api/auth/department-context",
            headers={"Authorization": f"Bearer {case['token']}"},
            json={"department_id": case["department_ids"][1], "expected_revision": me.json()["context_revision"]},
        )
        assert switched.status_code == 200, switched.text
        visible = await _visible_kb_ids(test_client, case["token"])
        assert case["a_only_kb"] not in visible
        assert case["global_kb"] in visible
    finally:
        await engine.dispose()


async def test_direct_id_access_scopes_to_active_department(test_client, visibility_case):
    """知识库直接 ID 访问（external files）按活动部门范围判定。"""
    case = visibility_case
    headers = {"Authorization": f"Bearer {case['token']}"}
    dept_a, dept_b = case["department_ids"]

    switched = await test_client.post(
        "/api/auth/department-context",
        headers=headers,
        json={"department_id": dept_b, "expected_revision": case["revision"]},
    )
    assert switched.status_code == 200

    # B 上下文直接访问 A-only 库 → 404（不可见即不存在）
    denied = await test_client.get(f"/api/knowledge/databases/external/{case['a_only_kb']}/files", headers=headers)
    assert denied.status_code == 404

    # 切回 A 后同一 ID 可见（文件列表可能为空但不再是 404）
    switched_back = await test_client.post(
        "/api/auth/department-context",
        headers=headers,
        json={"department_id": dept_a, "expected_revision": switched.json()["context_revision"]},
    )
    assert switched_back.status_code == 200
    allowed = await test_client.get(f"/api/knowledge/databases/external/{case['a_only_kb']}/files", headers=headers)
    assert allowed.status_code == 200, allowed.text
