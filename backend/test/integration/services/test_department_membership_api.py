"""部门成员管理 HTTP 集成测试：越权矩阵、候选最小化、revision 守卫。"""

from __future__ import annotations

import os
import uuid

import asyncpg
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine

from test.integration.conftest import admin_revision_headers

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


def _pg_dsn() -> str:
    return os.getenv("POSTGRES_URL", "postgresql+asyncpg://postgres:postgres@postgres:5432/yuxi").replace(
        "postgresql+asyncpg://", "postgresql://"
    )


@pytest_asyncio.fixture
async def membership_case(test_client, admin_headers):
    """部门 + 部门管理员 + 普通成员的完整用例；teardown 记录式清理。"""
    suffix = uuid.uuid4().hex[:8]
    dept_admin_uid = f"deptadmin_{suffix}"
    dept_admin_password = "DeptAdmin-Pass-2026"
    member_password = "Member-Pass-2026"
    department_id = None
    department_admin_id = None
    member_id = None
    engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    try:
        created = await test_client.post(
            "/api/departments",
            headers=admin_headers,
            json={
                "name": f"成员测试部门-{suffix}",
                "description": "pytest membership case",
                "admin_uid": dept_admin_uid,
                "admin_password": dept_admin_password,
                "admin_phone": None,
            },
        )
        assert created.status_code == 201, created.text
        department_id = created.json()["id"]

        member_created = await test_client.post(
            "/api/auth/users",
            headers=admin_headers,
            json={"username": f"成员{suffix}", "password": member_password},
        )
        assert member_created.status_code == 200, member_created.text
        member_id = member_created.json()["id"]
        member_uid = member_created.json()["uid"]

        added = await test_client.post(
            f"/api/departments/{department_id}/members",
            headers=await admin_revision_headers(test_client, admin_headers),
            json={"user_id": member_id},
        )
        assert added.status_code == 200, added.text

        # 部门管理员与成员各自登录获取独立会话
        dept_admin_login = await test_client.post(
            "/api/auth/token",
            data={"username": dept_admin_uid, "password": dept_admin_password},
        )
        assert dept_admin_login.status_code == 200, dept_admin_login.text
        member_login = await test_client.post(
            "/api/auth/token", data={"username": member_uid, "password": member_password}
        )
        assert member_login.status_code == 200, member_login.text

        # 创建部门时管理员账号的 uid 即 admin_uid
        dept_admin_me = await test_client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {dept_admin_login.json()['access_token']}"}
        )
        department_admin_id = dept_admin_me.json()["id"]

        yield {
            "department_id": department_id,
            "admin_headers": {"Authorization": f"Bearer {dept_admin_login.json()['access_token']}"},
            "member_headers": {"Authorization": f"Bearer {member_login.json()['access_token']}"},
            "member_id": member_id,
            "department_admin_id": department_admin_id,
        }
    finally:
        # 记录式清理：超管解除成员 -> 软删测试账号 -> 直连清会话/旧部门字段 -> 旧删除接口删部门
        try:
            if department_id is not None:
                rev_headers = await admin_revision_headers(test_client, admin_headers)
                for uid in (department_admin_id, member_id):
                    if uid is not None:
                        await test_client.delete(f"/api/departments/{department_id}/members/{uid}", headers=rev_headers)
            for uid in (department_admin_id, member_id):
                if uid is not None:
                    await test_client.delete(f"/api/auth/users/{uid}", headers=admin_headers)
            conn = await asyncpg.connect(_pg_dsn())
            try:
                if department_admin_id is not None and member_id is not None:
                    await conn.execute(
                        "DELETE FROM auth_sessions WHERE user_id = ANY($1::int[])",
                        [department_admin_id, member_id],
                    )
                    await conn.execute(
                        "UPDATE users SET department_id = NULL WHERE id = ANY($1::int[])",
                        [department_admin_id, member_id],
                    )
            finally:
                await conn.close()
            if department_id is not None:
                deletion = await test_client.delete(f"/api/departments/{department_id}", headers=admin_headers)
                assert deletion.status_code == 204, deletion.text
        finally:
            await engine.dispose()


async def _dept_revision(test_client, headers) -> dict:
    me = await test_client.get("/api/auth/me", headers=headers)
    assert me.status_code == 200, me.text
    merged = dict(headers)
    merged["X-Department-Revision"] = str(me.json()["context_revision"])
    return merged


async def test_department_admin_cannot_promote(test_client, membership_case):
    case = membership_case
    response = await test_client.patch(
        f"/api/departments/{case['department_id']}/members/{case['member_id']}",
        headers=await _dept_revision(test_client, case["admin_headers"]),
        json={"role": "admin"},
    )
    assert response.status_code == 403


async def test_department_admin_manages_plain_members(test_client, membership_case):
    case = membership_case
    headers = await _dept_revision(test_client, case["admin_headers"])
    department_id = case["department_id"]

    listed = await test_client.get(f"/api/departments/{department_id}/members", headers=headers)
    assert listed.status_code == 200
    roles = {item["user_id"]: item["role"] for item in listed.json()["items"]}
    assert roles[case["department_admin_id"]] == "admin"
    assert roles[case["member_id"]] == "user"

    # 候选字段最小化：不泄漏手机号/角色
    candidates = await test_client.get(
        f"/api/departments/{department_id}/member-candidates", headers=headers, params={"search": ""}
    )
    assert candidates.status_code == 200
    for item in candidates.json()["items"]:
        assert set(item.keys()) == {"user_id", "uid", "username"}


async def test_plain_member_and_foreign_admin_rejected(test_client, membership_case, admin_headers):
    case = membership_case
    department_id = case["department_id"]
    # 普通成员不能管理
    denied = await test_client.get(
        f"/api/departments/{department_id}/members",
        headers=await _dept_revision(test_client, case["member_headers"]),
    )
    assert denied.status_code == 403

    # 超管可跨部门移除最后一位管理员
    removed = await test_client.delete(
        f"/api/departments/{department_id}/members/{case['department_admin_id']}",
        headers=await admin_revision_headers(test_client, admin_headers),
    )
    assert removed.status_code == 204
    # 恢复：重新以普通成员加入（角色由超管设置）
    re_added = await test_client.post(
        f"/api/departments/{department_id}/members",
        headers=await admin_revision_headers(test_client, admin_headers),
        json={"user_id": case["department_admin_id"]},
    )
    assert re_added.status_code == 200
    promoted = await test_client.patch(
        f"/api/departments/{department_id}/members/{case['department_admin_id']}",
        headers=await admin_revision_headers(test_client, admin_headers),
        json={"role": "admin"},
    )
    assert promoted.status_code == 200


async def test_duplicate_add_conflicts_without_downgrade(test_client, membership_case, admin_headers):
    case = membership_case
    department_id = case["department_id"]
    promoted = await test_client.patch(
        f"/api/departments/{department_id}/members/{case['member_id']}",
        headers=await admin_revision_headers(test_client, admin_headers),
        json={"role": "admin"},
    )
    assert promoted.status_code == 200
    duplicate = await test_client.post(
        f"/api/departments/{department_id}/members",
        headers=await admin_revision_headers(test_client, admin_headers),
        json={"user_id": case["member_id"]},
    )
    assert duplicate.status_code == 409
    # 重复添加不降级角色
    listed = await test_client.get(
        f"/api/departments/{department_id}/members", headers=await admin_revision_headers(test_client, admin_headers)
    )
    roles = {item["user_id"]: item["role"] for item in listed.json()["items"]}
    assert roles[case["member_id"]] == "admin"


async def test_membership_write_requires_current_revision(test_client, membership_case):
    case = membership_case
    stale = dict(case["admin_headers"])
    stale["X-Department-Revision"] = "999999"
    response = await test_client.post(
        f"/api/departments/{case['department_id']}/members",
        headers=stale,
        json={"user_id": 1},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "department_context_stale"

    missing = dict(case["admin_headers"])
    response2 = await test_client.post(
        f"/api/departments/{case['department_id']}/members",
        headers=missing,
        json={"user_id": 1},
    )
    assert response2.status_code == 422


async def test_invalid_role_and_missing_target(test_client, membership_case):
    case = membership_case
    headers = await _dept_revision(test_client, case["admin_headers"])
    department_id = case["department_id"]
    invalid = await test_client.patch(
        f"/api/departments/{department_id}/members/{case['member_id']}",
        headers=headers,
        json={"role": "owner"},
    )
    assert invalid.status_code == 422, invalid.text

    missing = await test_client.delete(f"/api/departments/{department_id}/members/987654", headers=headers)
    assert missing.status_code == 404


async def test_admin_cannot_manage_foreign_department_or_admins(test_client, membership_case, admin_headers):
    """跨部门管理员拒绝；部门管理员不能移除自己或其他管理员。"""
    case = membership_case
    department_id = case["department_id"]
    headers = await _dept_revision(test_client, case["admin_headers"])

    # 本部门管理员试图移除自己（admin）→ 403
    self_removal = await test_client.delete(
        f"/api/departments/{department_id}/members/{case['department_admin_id']}",
        headers=headers,
    )
    assert self_removal.status_code == 403

    # 提升普通成员为 admin（仅超管），部门管理员再尝试移除该 admin → 403
    promoted = await test_client.patch(
        f"/api/departments/{department_id}/members/{case['member_id']}",
        headers=await admin_revision_headers(test_client, admin_headers),
        json={"role": "admin"},
    )
    assert promoted.status_code == 200
    remove_admin = await test_client.delete(
        f"/api/departments/{department_id}/members/{case['member_id']}", headers=headers
    )
    assert remove_admin.status_code == 403
    # 还原为 user，避免影响 teardown 角色假设
    await test_client.patch(
        f"/api/departments/{department_id}/members/{case['member_id']}",
        headers=await admin_revision_headers(test_client, admin_headers),
        json={"role": "user"},
    )

    # 跨部门：默认部门超管上下文之外，构造另一部门管理员的场景由 service 矩阵覆盖；
    # 这里以普通成员上下文尝试写 → 403
    member_write = await test_client.post(
        f"/api/departments/{department_id}/members",
        headers=await _dept_revision(test_client, case["member_headers"]),
        json={"user_id": 1},
    )
    assert member_write.status_code == 403


async def test_demoted_admin_loses_management_immediately(test_client, membership_case, admin_headers):
    """超管降级管理员后，旧令牌的下一次成员管理请求立即被拒（角色按成员关系实时解析）。"""
    case = membership_case
    department_id = case["department_id"]
    demoted = await test_client.patch(
        f"/api/departments/{department_id}/members/{case['department_admin_id']}",
        headers=await admin_revision_headers(test_client, admin_headers),
        json={"role": "user"},
    )
    assert demoted.status_code == 200

    # 旧令牌（登录时的 admin 角色）现在只能读到实时 user 角色 → 拒绝
    stale_attempt = await test_client.get(
        f"/api/departments/{department_id}/members",
        headers=case["admin_headers"],
    )
    assert stale_attempt.status_code == 403

    # 恢复 admin 供 teardown
    await test_client.patch(
        f"/api/departments/{department_id}/members/{case['department_admin_id']}",
        headers=await admin_revision_headers(test_client, admin_headers),
        json={"role": "admin"},
    )
