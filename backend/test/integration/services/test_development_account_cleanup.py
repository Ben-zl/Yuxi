"""开发账号清理脚本在真实 PostgreSQL 上的集成测试：预览不写、apply 软删除、事务回滚。"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy import text

from scripts import cleanup_development_accounts as cleanup_script
from yuxi.storage.postgres.manager import PostgresManager
from yuxi.storage.postgres.models_business import (
    APIKey,
    AgentRun,
    AuthSession,
    CLIAuthSession,
    Conversation,
    Department,
    DepartmentMembership,
    Project,
    User,
)
from yuxi.utils.datetime_utils import utc_now_naive

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest.fixture(scope="session", autouse=True)
def ensure_live_api_schema():
    """本文件自行创建隔离 Schema，不依赖运行中的 API 提供 schema 事实。"""


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_knowledge_resources():
    """隔离 Schema 测试的 HTTP 用例自管清理。"""
    yield


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_sandboxes():
    """隔离 Schema 测试没有 Sandbox 资源需要清理。"""
    yield


def _scoped_manager(engine) -> PostgresManager:
    """创建不触碰进程单例的隔离 manager。"""
    manager = object.__new__(PostgresManager)
    PostgresManager.__init__(manager)
    manager.async_engine = engine
    manager._initialized = True
    return manager


async def _create_scoped_schema() -> tuple[create_async_engine, create_async_engine, str]:
    """创建隔离 schema 并返回 (admin_engine, scoped_engine, schema)。"""
    schema = f"pytest_dev_cleanup_{uuid.uuid4().hex[:12]}"
    admin_engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    async with admin_engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped_engine = create_async_engine(
        os.environ["POSTGRES_URL"],
        pool_pre_ping=True,
        connect_args={"server_settings": {"search_path": schema}},
    )
    manager = _scoped_manager(scoped_engine)
    await manager.create_business_tables()
    # 模拟 v15 前的库：旧全局 admin 行必须可存在（目标库的 CHECK 尚未约束存量）
    async with scoped_engine.begin() as connection:
        await connection.execute(text("ALTER TABLE users DROP CONSTRAINT IF EXISTS ck_users_role_global"))
    return admin_engine, scoped_engine, schema


async def _drop_scoped_schema(admin_engine, schema: str) -> None:
    async with admin_engine.begin() as connection:
        await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))


def _run_id() -> str:
    return f"run-{uuid.uuid4().hex}"


async def _seed_accounts(factory) -> SimpleNamespace:
    """提交种子数据：1 超管 + 1 旧 admin + 1 旧 user，含会话/Key/CLI 授权/成员关系/业务行。"""
    async with factory() as session:
        async with session.begin():
            department = Department(name="dept-cleanup")
            superadmin = User(username="清理超管", uid="cleanup_super", password_hash="hash", role="superadmin")
            legacy_admin = User(username="清理旧管理员", uid="cleanup_admin", password_hash="hash", role="admin")
            legacy_user = User(username="清理旧用户", uid="cleanup_user", password_hash="hash", role="user")
            session.add_all([department, superadmin, legacy_admin, legacy_user])
            await session.flush()

            session.add_all(
                [
                    DepartmentMembership(user_id=superadmin.id, department_id=department.id, role="user"),
                    DepartmentMembership(user_id=legacy_admin.id, department_id=department.id, role="admin"),
                    DepartmentMembership(user_id=legacy_user.id, department_id=department.id, role="user"),
                ]
            )
            expires_at = utc_now_naive() + timedelta(hours=1)
            session.add_all(
                [
                    AuthSession(id=str(uuid.uuid4()), user_id=superadmin.id, expires_at=expires_at),
                    AuthSession(id=str(uuid.uuid4()), user_id=legacy_admin.id, expires_at=expires_at),
                    AuthSession(id=str(uuid.uuid4()), user_id=legacy_user.id, expires_at=expires_at),
                ]
            )
            keys = {
                user.uid: APIKey(
                    key_hash=f"hash-{uuid.uuid4().hex}",
                    key_prefix=f"pk-{user.uid[-6:]}",
                    name=f"key-{user.uid}",
                    user_id=user.id,
                    is_enabled=True,
                    created_by="seed",
                )
                for user in (superadmin, legacy_admin, legacy_user)
            }
            session.add_all(keys.values())
            await session.flush()

            # 旧 admin 的 CLI 授权会话关联其 API Key：软删除后该凭证必须失效
            session.add(
                CLIAuthSession(
                    device_code_hash=f"device-{uuid.uuid4().hex}",
                    user_code=f"cli-{uuid.uuid4().hex[:8]}",
                    status="approved",
                    key_name="cli-key",
                    approved_user_id=legacy_admin.id,
                    approved_department_id=department.id,
                    api_key_id=keys["cleanup_admin"].id,
                    expires_at=expires_at,
                )
            )

            # 超管与旧 user 各一行代表性业务数据（Project → Conversation → AgentRun）
            for user in (superadmin, legacy_user):
                project = Project(
                    id=f"proj-{user.uid}",
                    uid=user.uid,
                    selection_status="implicit",
                    workdir_path=f"workdir/{user.uid}",
                    directory_mode="managed",
                )
                conversation = Conversation(
                    thread_id=f"thread-{uuid.uuid4().hex}",
                    uid=user.uid,
                    agent_id="agent-cleanup",
                    title=f"对话-{user.uid}",
                    project_id=project.id,
                )
                session.add_all([project, conversation])
                await session.flush()
                session.add(
                    AgentRun(
                        id=_run_id(),
                        conversation_thread_id=conversation.thread_id,
                        runtime_scope_id=conversation.thread_id,
                        agent_slug="agent-cleanup",
                        uid=user.uid,
                        status="completed",
                        request_id=f"req-{uuid.uuid4().hex}",
                        conversation_id=conversation.id,
                    )
                )

            return SimpleNamespace(
                superadmin_id=superadmin.id,
                legacy_admin_id=legacy_admin.id,
                legacy_user_id=legacy_user.id,
                superadmin_uid=superadmin.uid,
                legacy_user_uid=legacy_user.uid,
            )


async def _database_snapshot(session, seed: SimpleNamespace) -> dict:
    """回读库内关键状态：账号、凭证、成员关系与业务行摘要。"""
    legacy_ids = [seed.legacy_admin_id, seed.legacy_user_id]
    users = (await session.execute(select(User.uid, User.role, User.is_deleted).order_by(User.uid))).all()
    active_roles = (
        (await session.execute(select(User.role).where(User.is_deleted == 0).order_by(User.role))).scalars().all()
    )
    active_sessions = await session.scalar(
        select(func.count()).select_from(AuthSession).where(AuthSession.revoked_at.is_(None))
    )
    legacy_active_sessions = await session.scalar(
        select(func.count())
        .select_from(AuthSession)
        .where(AuthSession.user_id.in_(legacy_ids), AuthSession.revoked_at.is_(None))
    )
    legacy_active_api_keys = await session.scalar(
        select(func.count()).select_from(APIKey).where(APIKey.user_id.in_(legacy_ids), APIKey.is_enabled.is_(True))
    )
    legacy_revoked_api_keys = await session.scalar(
        select(func.count()).select_from(APIKey).where(APIKey.user_id.in_(legacy_ids), APIKey.revoked_at.is_not(None))
    )
    legacy_memberships = await session.scalar(
        select(func.count()).select_from(DepartmentMembership).where(DepartmentMembership.user_id.in_(legacy_ids))
    )
    superadmin_session_active = await session.scalar(
        select(func.count())
        .select_from(AuthSession)
        .where(AuthSession.user_id == seed.superadmin_id, AuthSession.revoked_at.is_(None))
    )
    superadmin_key_enabled = await session.scalar(
        select(func.count())
        .select_from(APIKey)
        .where(APIKey.user_id == seed.superadmin_id, APIKey.is_enabled.is_(True))
    )
    superadmin_memberships = await session.scalar(
        select(func.count()).select_from(DepartmentMembership).where(DepartmentMembership.user_id == seed.superadmin_id)
    )
    superadmin_row = (
        await session.execute(
            select(User.role, User.is_deleted).where(User.id == seed.superadmin_id)
        )
    ).one()
    business_run_ids = (await session.execute(select(AgentRun.id).order_by(AgentRun.id))).scalars().all()
    business_thread_ids = (
        (await session.execute(select(Conversation.thread_id).order_by(Conversation.thread_id))).scalars().all()
    )
    departments = await session.scalar(select(func.count()).select_from(Department))
    return {
        "users": users,
        "active_roles": active_roles,
        "active_sessions": active_sessions,
        "legacy_active_sessions": legacy_active_sessions,
        "legacy_active_api_keys": legacy_active_api_keys,
        "legacy_revoked_api_keys": legacy_revoked_api_keys,
        "legacy_memberships": legacy_memberships,
        "superadmin_session_active": superadmin_session_active,
        "superadmin_key_enabled": superadmin_key_enabled,
        "superadmin_memberships": superadmin_memberships,
        "superadmin_row": superadmin_row,
        "business_run_ids": business_run_ids,
        "business_thread_ids": business_thread_ids,
        "departments": departments,
    }


async def test_preview_then_apply_deletes_only_non_superadmin() -> None:
    """预览不写库；apply 只软删除非超管并撤销其凭证/成员关系；重复 apply 零新增。"""
    admin_engine, scoped_engine, schema = await _create_scoped_schema()
    try:
        factory = async_sessionmaker(scoped_engine, expire_on_commit=False)
        seed = await _seed_accounts(factory)

        async with factory() as session:
            before = await _database_snapshot(session, seed)
        assert before["active_roles"] == ["admin", "superadmin", "user"]
        assert before["legacy_active_sessions"] == 2
        assert before["legacy_active_api_keys"] == 2

        # 预览：只读统计，不产生任何写入
        async with factory() as session:
            preview = await cleanup_script.cleanup_accounts(session, apply=False)
        assert preview["candidate_accounts"] == 2
        assert preview["deleted_accounts"] == 0
        assert preview["revoked_credentials"] == 4  # 2 个会话 + 2 个 Key
        assert preview["removed_memberships"] == 2

        async with factory() as session:
            after_preview = await _database_snapshot(session, seed)
        assert after_preview == before

        # 执行：单事务提交
        async with factory() as session:
            async with session.begin():
                applied = await cleanup_script.cleanup_accounts(session, apply=True)
        assert applied["candidate_accounts"] == 2
        assert applied["deleted_accounts"] == 2
        assert applied["revoked_credentials"] == 4
        assert applied["removed_memberships"] == 2

        async with factory() as session:
            after = await _database_snapshot(session, seed)

        assert after["active_roles"] == ["superadmin"]
        remaining_user_states = {(uid, role, deleted) for uid, role, deleted in after["users"]}
        assert ("cleanup_super", "superadmin", 0) in remaining_user_states
        assert ("cleanup_admin", "user", 1) in remaining_user_states  # 软删除旧 admin 同轮归一为 user
        assert ("cleanup_user", "user", 1) in remaining_user_states

        # 计划验收断言：被删账号活跃会话/Key 归零，超管与业务数据不变
        assert after["legacy_active_sessions"] == 0
        assert after["legacy_active_api_keys"] == 0
        assert after["legacy_revoked_api_keys"] == 2
        assert after["legacy_memberships"] == 0
        assert after["superadmin_session_active"] == 1
        assert after["superadmin_key_enabled"] == 1
        assert after["superadmin_memberships"] == 1
        assert tuple(after["superadmin_row"]) == tuple(before["superadmin_row"])
        assert after["business_run_ids"] == before["business_run_ids"]
        assert after["business_thread_ids"] == before["business_thread_ids"]
        assert after["departments"] == before["departments"] == 1

        # 软删除账号经 CLI 授权获得的 Key 必须失效
        async with factory() as session:
            cli_key_state = (
                await session.execute(
                    select(APIKey.is_enabled, APIKey.revoked_at.is_not(None)).where(
                        APIKey.user_id == seed.legacy_admin_id
                    )
                )
            ).all()
        assert cli_key_state == [(False, True)]

        # 重复 apply：无新增删除/撤销
        async with factory() as session:
            async with session.begin():
                repeated = await cleanup_script.cleanup_accounts(session, apply=True)
        assert repeated == {
            "candidate_accounts": 0,
            "deleted_accounts": 0,
            "revoked_credentials": 0,
            "removed_memberships": 0,
            # 首轮已把软删除旧 admin 归一为 user，重复执行不再有归一
            "normalized_admin_roles": 0,
            "revoked_stale_sessions": 0,
        }

        # 正向补例：预置软删用户+未撤销会话，再次 apply 必须撤销且只撤销一次
        async with factory() as session:
            async with session.begin():
                stale_user = User(
                    username="清理历史会话", uid="cleanup_stale", password_hash="hash", role="user", is_deleted=1
                )
                session.add(stale_user)
                await session.flush()
                session.add(
                    AuthSession(
                        id=str(uuid.uuid4()),
                        user_id=stale_user.id,
                        expires_at=datetime.now() + timedelta(hours=1),
                    )
                )
        async with factory() as session:
            async with session.begin():
                forward = await cleanup_script.cleanup_accounts(session, apply=True)
        assert forward["revoked_stale_sessions"] == 1
        async with factory() as session:
            async with session.begin():
                again = await cleanup_script.cleanup_accounts(session, apply=True)
        assert again["revoked_stale_sessions"] == 0
        # 补例数据自清：恢复整库快照等价断言的前提（快照含 users 全表）
        async with factory() as session:
            async with session.begin():
                await session.execute(
                    delete(AuthSession).where(
                        AuthSession.user_id.in_(select(User.id).where(User.uid == "cleanup_stale"))
                    )
                )
                await session.execute(delete(User).where(User.uid == "cleanup_stale"))
        async with factory() as session:
            after_repeat = await _database_snapshot(session, seed)
        assert after_repeat == after
    finally:
        await scoped_engine.dispose()
        await _drop_scoped_schema(admin_engine, schema)


async def test_apply_rolls_back_entirely_on_midway_failure(monkeypatch) -> None:
    """第二个账号软删除时注入异常：整个事务回滚，库内无任何部分提交。"""
    admin_engine, scoped_engine, schema = await _create_scoped_schema()
    try:
        factory = async_sessionmaker(scoped_engine, expire_on_commit=False)
        seed = await _seed_accounts(factory)

        async with factory() as session:
            before = await _database_snapshot(session, seed)

        original = cleanup_script._soft_delete_user
        calls = 0

        async def flaky_soft_delete(db, user) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("注入的中途失败")
            await original(db, user)

        monkeypatch.setattr(cleanup_script, "_soft_delete_user", flaky_soft_delete)

        with pytest.raises(RuntimeError, match="注入的中途失败"):
            async with factory() as session:
                async with session.begin():
                    await cleanup_script.cleanup_accounts(session, apply=True)

        assert calls == 2
        async with factory() as session:
            after = await _database_snapshot(session, seed)
        assert after == before
    finally:
        await scoped_engine.dispose()
        await _drop_scoped_schema(admin_engine, schema)
