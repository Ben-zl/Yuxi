"""删除部门边界集成测试：账号保留、引用阻断、凭证撤销与并发锁序。

规则（decision：删除部门边界）：
- 仅超级管理员可删除；默认部门不可删除；
- 有资源归属/共享引用/未终态任务时 409，数据保持不变；
- 成功时只删除部门与成员关系、撤销绑定凭证、清空会话当前部门，
  账号与其他部门关系保留，不迁移到默认部门。
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import timedelta

import asyncpg
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from test.integration.conftest import admin_revision_headers

from yuxi.repositories.department_repository import DepartmentRepository
from yuxi.services.department_context_service import DepartmentContext
from yuxi.services.department_membership_service import DepartmentMembershipService
from yuxi.storage.postgres.models_business import (
    AgentMemoryScope,
    AgentRunRequest,
    AgentRun,
    AgentScopeChannelBinding,
    AgentTask,
    APIKey,
    AuthSession,
    CLIAuthSession,
    Conversation,
    Department,
    DepartmentMembership,
    MCPServer,
    Project,
    User,
)
from yuxi.storage.postgres.models_knowledge import WeknoraDepartmentWorkspace
from yuxi.utils.datetime_utils import utc_now_naive

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


def _pg_dsn() -> str:
    return os.getenv("POSTGRES_URL", "postgresql+asyncpg://postgres:postgres@postgres:5432/yuxi").replace(
        "postgresql+asyncpg://", "postgresql://"
    )


@pytest_asyncio.fixture
async def pg_pool():
    pool = await asyncpg.create_pool(_pg_dsn(), min_size=1, max_size=2)
    try:
        yield pool
    finally:
        await pool.close()


@pytest_asyncio.fixture
async def db_engine():
    engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def boundary_case(test_client, admin_headers, db_engine, pg_pool):
    """部门 A（被删目标，管理员 uid=admin_uid）+ 部门 B（其他部门）；记录式清理。"""
    suffix = uuid.uuid4().hex[:8]
    created = {}
    for label in ("a", "b"):
        resp = await test_client.post(
            "/api/departments",
            headers=admin_headers,
            json={
                "name": f"删除边界{label}-{suffix}",
                "description": "pytest delete boundary",
                "admin_uid": f"deladmin_{label}_{suffix}",
                "admin_password": "DelAdmin-Pass-2026",
                "admin_phone": None,
            },
        )
        assert resp.status_code == 201, resp.text
        created[label] = resp.json()

    admin_user_id = await _user_id(pg_pool, f"deladmin_a_{suffix}")

    yield {
        "department_id": created["a"]["id"],
        "other_department_id": created["b"]["id"],
        "admin_uid": f"deladmin_a_{suffix}",
        "admin_user_id": admin_user_id,
        "suffix": suffix,
    }

    # teardown：直接删除残留引用与部门，避免默认清理路径对边界语义的依赖
    for label in ("a", "b"):
        dept_id = created[label]["id"]
        async with db_engine.begin() as conn:
            await conn.execute(
                AuthSession.__table__.update()
                .where(AuthSession.active_department_id == dept_id)
                .values(active_department_id=None)
            )
            await conn.execute(
                CLIAuthSession.__table__.update()
                .where(CLIAuthSession.approved_department_id == dept_id)
                .values(approved_department_id=None)
            )
            await conn.execute(
                APIKey.__table__.update().where(APIKey.department_id == dept_id).values(department_id=None)
            )
            await conn.execute(
                AgentScopeChannelBinding.__table__.delete().where(AgentScopeChannelBinding.department_id == dept_id)
            )
            await conn.execute(AgentTask.__table__.delete().where(AgentTask.department_id == dept_id))
            await conn.execute(AgentRun.__table__.delete().where(AgentRun.department_id == dept_id))
            await conn.execute(
                AgentRunRequest.__table__.delete().where(AgentRunRequest.department_id == dept_id)
            )
            await conn.execute(
                WeknoraDepartmentWorkspace.__table__.delete().where(WeknoraDepartmentWorkspace.department_id == dept_id)
            )
            await conn.execute(
                MCPServer.__table__.delete().where(MCPServer.slug.like("boundary-mcp-%"))
            )
            await conn.execute(
                AgentMemoryScope.__table__.delete().where(AgentMemoryScope.maintenance_department_id == dept_id)
            )
            await conn.execute(
                DepartmentMembership.__table__.delete().where(DepartmentMembership.department_id == dept_id)
            )
            await conn.execute(Department.__table__.delete().where(Department.id == dept_id))


async def _user_id(pool: asyncpg.Pool, uid: str) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval("SELECT id FROM users WHERE uid = $1", uid)


async def _seed_reference_rows(engine, department_id: int, uid: str, kinds: tuple[str, ...]) -> dict:
    """直接写入引用/绑定行；返回关键事实。"""
    factory = async_sessionmaker(engine, expire_on_commit=False)
    facts: dict = {}
    async with factory() as session:
        async with session.begin():
            user = (await session.execute(select(User).where(User.uid == uid))).scalar_one()
            expires_at = utc_now_naive() + timedelta(hours=1)

            if "key" in kinds:
                key = APIKey(
                    key_hash=f"hash-{uuid.uuid4().hex}",
                    key_prefix=f"pk-{uuid.uuid4().hex[:6]}",
                    name="boundary-key",
                    user_id=user.id,
                    department_id=department_id,
                    is_enabled=True,
                    created_by="seed",
                )
                session.add(key)
                await session.flush()
                facts["api_key_id"] = key.id
                if "cli" in kinds:
                    session.add(
                        CLIAuthSession(
                            device_code_hash=f"device-{uuid.uuid4().hex}",
                            user_code=f"cli-{uuid.uuid4().hex[:8]}",
                            status="approved",
                            key_name="boundary-cli",
                            approved_user_id=user.id,
                            approved_department_id=department_id,
                            api_key_id=key.id,
                            expires_at=expires_at,
                        )
                    )

            if "session" in kinds:
                facts["session_id"] = str(uuid.uuid4())
                session.add(
                    AuthSession(
                        id=facts["session_id"],
                        user_id=user.id,
                        active_department_id=department_id,
                        revision=3,
                        expires_at=expires_at,
                    )
                )

            if "memory" in kinds:
                session.add(
                    AgentMemoryScope(
                        uid=user.uid,
                        agent_slug="boundary-agent",
                        workspace_id=f"ws-{uuid.uuid4().hex[:12]}",
                        maintenance_department_id=department_id,
                    )
                )

            if set(kinds) & {"running_run", "terminal_run"}:
                status = "running" if "running_run" in kinds else "completed"
                project = Project(
                    id=f"proj-{uuid.uuid4().hex[:10]}",
                    uid=user.uid,
                    selection_status="implicit",
                    workdir_path=f"workdir/{user.uid}-{uuid.uuid4().hex[:4]}",
                    directory_mode="managed",
                )
                conversation = Conversation(
                    thread_id=f"thread-{uuid.uuid4().hex[:12]}",
                    uid=user.uid,
                    agent_id="boundary-agent",
                    title="删除边界对话",
                    project_id=project.id,
                )
                session.add_all([project, conversation])
                await session.flush()
                session.add(
                    AgentRun(
                        id=f"run-{uuid.uuid4().hex[:12]}",
                        conversation_thread_id=conversation.thread_id,
                        runtime_scope_id=conversation.thread_id,
                        agent_slug="boundary-agent",
                        uid=user.uid,
                        department_id=department_id,
                        status=status,
                        request_id=f"req-{uuid.uuid4().hex[:12]}",
                        conversation_id=conversation.id,
                    )
                )

            if set(kinds) & {"queued_request", "dispatched_request"}:
                status = "queued" if "queued_request" in kinds else "dispatched"
                project = Project(
                    id=f"proj-{uuid.uuid4().hex[:10]}",
                    uid=user.uid,
                    selection_status="implicit",
                    workdir_path=f"workdir/{user.uid}-{uuid.uuid4().hex[:4]}",
                    directory_mode="managed",
                )
                conversation = Conversation(
                    thread_id=f"thread-{uuid.uuid4().hex[:12]}",
                    uid=user.uid,
                    agent_id="boundary-agent",
                    title="删除边界请求对话",
                    project_id=project.id,
                )
                session.add_all([project, conversation])
                await session.flush()
                from yuxi.storage.postgres.models_business import Message

                message = Message(
                    conversation_id=conversation.id,
                    role="user",
                    content="seed",
                )
                session.add(message)
                await session.flush()
                session.add(
                    AgentRunRequest(
                        request_id=f"req-{uuid.uuid4().hex[:12]}",
                        uid=user.uid,
                        department_id=department_id,
                        agent_slug="boundary-agent",
                        conversation_thread_id=conversation.thread_id,
                        input_message_id=message.id,
                        status=status,
                    )
                )

            if "task" in kinds:
                session.add(
                    AgentTask(
                        id=str(uuid.uuid4()),
                        name="删除边界任务",
                        owner_uid=user.uid,
                        department_id=department_id,
                        prompt="pytest",
                        enabled=True,
                        share_config={
                            "version": 2,
                            "read_scope": {
                                "access_level": "user",
                                "department_ids": [],
                                "user_uids": [user.uid],
                            },
                            "manage_scope": None,
                        },
                    )
                )

            if "channel" in kinds:
                session.add(
                    AgentScopeChannelBinding(
                        id=str(uuid.uuid4()),
                        owner_uid=user.uid,
                        department_id=department_id,
                        agent_slug="boundary-agent",
                        name="删除边界通道",
                        channel_type="wps_xiezuo",
                        app_id=f"app-{uuid.uuid4().hex[:8]}",
                        encrypted_app_secret="cipher",
                        allow_from=[],
                        group_reply_policy="mention",
                        enabled=True,
                        sync_status="pending",
                        created_by=user.uid,
                        updated_by=user.uid,
                    )
                )

            if "share" in kinds:
                session.add(
                    MCPServer(
                        slug=f"boundary-mcp-{uuid.uuid4().hex[:8]}",
                        name="删除边界MCP",
                        transport="sse",
                        url="https://example.com/sse",
                        created_by=user.uid,
                        updated_by=user.uid,
                        share_config={
                            "version": 2,
                            "read_scope": {
                                "access_level": "department",
                                "department_ids": [department_id],
                                "user_uids": [],
                            },
                            "manage_scope": None,
                        },
                    )
                )

            if "workspace" in kinds:
                session.add(
                    WeknoraDepartmentWorkspace(
                        department_id=department_id,
                        workspace_tenant_id=f"tenant-{uuid.uuid4().hex[:10]}",
                        workspace_name="boundary-ws",
                        encrypted_api_key="cipher",
                        instance="default",
                    )
                )
    return facts


# ---------------------------------------------------------------------------
# 成功路径：账号保留、无默认部门迁移、凭证撤销
# ---------------------------------------------------------------------------


async def test_delete_success_retains_account_and_revokes_bindings(
    test_client, admin_headers, boundary_case, db_engine, pg_pool
):
    department_id = boundary_case["department_id"]
    other_department_id = boundary_case["other_department_id"]
    admin_uid = boundary_case["admin_uid"]
    admin_user_id = boundary_case["admin_user_id"]

    # A 管理员加入部门 B：删除 A 后该关系必须原样保留
    add_b = await test_client.post(
        f"/api/departments/{other_department_id}/members",
        headers=await admin_revision_headers(test_client, admin_headers),
        json={"user_id": admin_user_id},
    )
    assert add_b.status_code == 200, add_b.text
    b_role_before = await _fetch_val(
        pg_pool,
        "SELECT role FROM department_memberships WHERE user_id = $1 AND department_id = $2",
        admin_user_id,
        other_department_id,
    )
    assert b_role_before == "user"

    facts = await _seed_reference_rows(
        db_engine, department_id, admin_uid, ("key", "cli", "session", "memory")
    )

    deleted = await test_client.delete(f"/api/departments/{department_id}", headers=admin_headers)
    assert deleted.status_code == 204, deleted.text
    assert deleted.text == "" or deleted.text == "null"

    # 账号未软删/硬删；B 关系与角色不变；A 关系删除；未迁移到默认部门
    assert await _fetch_val(pg_pool, "SELECT is_deleted FROM users WHERE id = $1", admin_user_id) == 0
    assert (
        await _fetch_val(
            pg_pool,
            "SELECT role FROM department_memberships WHERE user_id = $1 AND department_id = $2",
            admin_user_id,
            other_department_id,
        )
        == "user"
    )
    assert (
        await _fetch_val(
            pg_pool,
            "SELECT count(*) FROM department_memberships WHERE user_id = $1 AND department_id = $2",
            admin_user_id,
            department_id,
        )
        == 0
    )
    assert (
        await _fetch_val(
            pg_pool,
            "SELECT count(*) FROM department_memberships WHERE user_id = $1 AND department_id = 1",
            admin_user_id,
        )
        == 0
    )
    # API Key 撤销并解绑
    key = await _fetch_one(
        pg_pool, "SELECT is_enabled, revoked_at, department_id FROM api_keys WHERE id = $1", facts["api_key_id"]
    )
    assert key["is_enabled"] is False and key["revoked_at"] is not None and key["department_id"] is None
    # CLI 批准部门清空
    assert (
        await _fetch_val(
            pg_pool,
            "SELECT approved_department_id FROM cli_auth_sessions WHERE api_key_id = $1",
            facts["api_key_id"],
        )
        is None
    )
    # Memory 维护绑定解绑
    assert (
        await _fetch_val(
            pg_pool,
            "SELECT count(*) FROM agent_memory_scopes WHERE maintenance_department_id = $1",
            department_id,
        )
        == 0
    )
    # 会话当前部门清空且 revision 递增
    sess = await _fetch_one(
        pg_pool,
        "SELECT active_department_id, revision FROM auth_sessions WHERE id = $1",
        facts["session_id"],
    )
    assert sess["active_department_id"] is None and sess["revision"] == 4
    # 部门已删除
    assert await _fetch_val(pg_pool, "SELECT count(*) FROM departments WHERE id = $1", department_id) == 0


# ---------------------------------------------------------------------------
# 阻断矩阵：409 且数据不变
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kinds,label",
    [
        (("running_run",), "未结束运行"),
        (("queued_request",), "排队中请求"),
        (("task",), "未归档任务"),
        (("channel",), "Channel"),
        (("share",), "共享"),
        (("workspace",), "工作区"),
    ],
)
async def test_delete_blocked_by_reference_keeps_data(
    test_client, admin_headers, boundary_case, db_engine, pg_pool, kinds, label
):
    department_id = boundary_case["department_id"]

    await _seed_reference_rows(db_engine, department_id, boundary_case["admin_uid"], kinds)

    deleted = await test_client.delete(f"/api/departments/{department_id}", headers=admin_headers)
    assert deleted.status_code == 409, deleted.text
    assert label in deleted.json()["detail"]

    # 部门与成员关系保持不变
    assert await _fetch_val(pg_pool, "SELECT count(*) FROM departments WHERE id = $1", department_id) == 1
    assert (
        await _fetch_val(
            pg_pool,
            "SELECT count(*) FROM department_memberships WHERE department_id = $1",
            department_id,
        )
        >= 1
    )


async def _fetch_one(pool: asyncpg.Pool, sql: str, *args):
    async with pool.acquire() as conn:
        return await conn.fetchrow(sql, *args)


async def _fetch_val(pool: asyncpg.Pool, sql: str, *args):
    async with pool.acquire() as conn:
        return await conn.fetchval(sql, *args)


async def test_delete_default_department_conflict(test_client, admin_headers):
    deleted = await test_client.delete("/api/departments/1", headers=admin_headers)
    assert deleted.status_code == 409, deleted.text


async def test_delete_requires_superadmin(test_client, admin_headers, boundary_case):
    """部门管理员（非超管）删除本部门必须 403。"""
    login = await test_client.post(
        "/api/auth/token",
        data={"username": boundary_case["admin_uid"], "password": "DelAdmin-Pass-2026"},
    )
    assert login.status_code == 200, login.text
    resp = await test_client.delete(
        f"/api/departments/{boundary_case['department_id']}",
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
    )
    assert resp.status_code == 403, resp.text


async def test_delete_unknown_department_404(test_client, admin_headers):
    resp = await test_client.delete("/api/departments/999999999", headers=admin_headers)
    assert resp.status_code == 404, resp.text


# ---------------------------------------------------------------------------
# 终态历史：运行记录部门标识保留审计语义（v15 起不阻断删除、不级联清理）
# ---------------------------------------------------------------------------


async def test_delete_department_with_terminal_run_keeps_audit_marker(
    test_client, admin_headers, boundary_case, db_engine, pg_pool
):
    department_id = boundary_case["department_id"]
    facts = await _seed_reference_rows(
        db_engine,
        department_id,
        boundary_case["admin_uid"],
        ("terminal_run", "dispatched_request"),
    )
    assert facts == {}

    deleted = await test_client.delete(f"/api/departments/{department_id}", headers=admin_headers)
    assert deleted.status_code == 204, deleted.text

    # 终态运行的 department_id 保留原值（审计），不被外键或删除流程清除
    assert (
        await _fetch_val(
            pg_pool,
            "SELECT count(*) FROM agent_runs WHERE department_id = $1",
            department_id,
        )
        == 1
    )
    # 已派发请求是历史事实：保留审计值且不阻断删除
    assert (
        await _fetch_val(
            pg_pool,
            "SELECT count(*) FROM agent_run_requests WHERE department_id = $1 AND status = 'dispatched'",
            department_id,
        )
        == 1
    )


# ---------------------------------------------------------------------------
# 并发锁序：删除持锁期间，成员/共享写入必须等待同一部门行锁
# ---------------------------------------------------------------------------


async def test_delete_holds_row_lock_against_member_writers(boundary_case, db_engine, pg_pool):
    department_id = boundary_case["department_id"]
    target_user_id = await _user_id(pg_pool, boundary_case["admin_uid"])

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as deleting_session:
        # 删除事务先锁定部门行但不提交（隐式事务，最后显式回滚）
        await DepartmentRepository(deleting_session).delete_empty_department(department_id)

        actor = DepartmentContext(
            id=1,
            uid="deptctx_admin",
            username="deptctx_admin",
            account_role="superadmin",
            department_id=1,
            department_name="默认部门",
            role="superadmin",
            session_id=None,
            revision=0,
        )
        membership_session = factory()
        add_task = asyncio.ensure_future(
            DepartmentMembershipService(membership_session).add_member(
                actor, department_id, target_user_id
            )
        )
        try:
            # 删除事务持有行锁：成员写入必须阻塞而非完成
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(asyncio.shield(add_task), timeout=0.8)
        finally:
            add_task.cancel()
            try:
                await add_task
            except (asyncio.CancelledError, Exception):
                pass
            await membership_session.close()
        await deleting_session.rollback()
    # 回滚删除事务后部门仍在
    assert await _fetch_val(pg_pool, "SELECT count(*) FROM departments WHERE id = $1", department_id) == 1


async def test_share_writer_rejects_deleted_department(test_client, admin_headers, boundary_case):
    """共享配置写入引用已删除部门时被拒绝，不产生孤儿引用。"""
    department_id = boundary_case["department_id"]
    slug = f"boundary-mcp-{boundary_case['suffix']}-a"
    created = await test_client.post(
        "/api/system/mcp-servers",
        headers=admin_headers,
        json={
            "slug": slug,
            "name": "边界MCP",
            "transport": "sse",
            "url": "https://example.com/sse",
            "share_config": {
                "version": 2,
                "read_scope": {"access_level": "global", "department_ids": [], "user_uids": []},
                "manage_scope": None,
            },
        },
    )
    assert created.status_code == 200, created.text
    resource_id = created.json()["data"]["resource_id"]

    deleted = await test_client.delete(f"/api/departments/{department_id}", headers=admin_headers)
    assert deleted.status_code == 204, deleted.text

    updated = await test_client.put(
        f"/api/system/mcp-servers/{resource_id}",
        headers=admin_headers,
        json={
            "share_config": {
                "version": 2,
                "read_scope": {
                    "access_level": "department",
                    "department_ids": [department_id],
                    "user_uids": [],
                },
                "manage_scope": None,
            }
        },
    )
    assert updated.status_code in (400, 404), updated.text
    assert "部门" in updated.json()["detail"]
