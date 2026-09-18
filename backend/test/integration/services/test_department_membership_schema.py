"""部门成员关系、登录会话与运行部门字段在真实 PostgreSQL 上的集成测试。"""

from __future__ import annotations

import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi import storage_migration
from yuxi.repositories.auth_session_repository import AuthSessionRepository
from yuxi.repositories.department_membership_repository import DepartmentMembershipRepository
from yuxi.repositories.department_repository import DepartmentDeletionConflict, DepartmentRepository
from yuxi.storage.postgres.manager import BUSINESS_SCHEMA_VERSION, PostgresManager
from yuxi.storage.postgres.models_business import (
    AuthSession,
    Department,
    DepartmentMembership,
    User,
)

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


@asynccontextmanager
async def _rollback_tx(session):
    """测试事务：正常退出统一回滚，不向隔离 schema 提交数据。"""
    transaction = await session.begin()
    try:
        yield transaction
    finally:
        await transaction.rollback()


async def _create_scoped_schema() -> tuple[create_async_engine, create_async_engine, str]:
    """创建隔离 schema 并返回 (admin_engine, scoped_engine, schema)。"""
    schema = f"pytest_dept_membership_{uuid.uuid4().hex[:12]}"
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
    return admin_engine, scoped_engine, schema


async def _drop_scoped_schema(admin_engine, schema: str) -> None:
    async with admin_engine.begin() as connection:
        await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))


async def test_membership_independent_roles_per_department() -> None:
    """同一账号在两个部门拥有独立角色，账号本身保持无单部门归属。"""
    admin_engine, scoped_engine, schema = await _create_scoped_schema()
    try:
        factory = async_sessionmaker(scoped_engine, expire_on_commit=False)
        async with factory() as session:
            async with _rollback_tx(session):
                dept_a = Department(name="dept-A")
                dept_b = Department(name="dept-B")
                user = User(username="成员甲", uid="member_a", password_hash="hash", role="user")
                session.add_all([dept_a, dept_b, user])
                await session.flush()

                repo = DepartmentMembershipRepository(session)
                await repo.add(user.id, dept_a.id, role="admin")
                await repo.add(user.id, dept_b.id, role="user")
                await session.flush()

                rows = await repo.list_for_user(user.id)
                assert [(row.department_id, row.role) for row in rows] == [
                    (dept_a.id, "admin"),
                    (dept_b.id, "user"),
                ]
                assert user.role == "user"
                assert user.department_id is None
        # 事务回滚后不残留成员关系
        async with factory() as session:
            remaining = await session.execute(DepartmentMembership.__table__.select())
            assert remaining.rowcount == 0
    finally:
        await scoped_engine.dispose()
        await _drop_scoped_schema(admin_engine, schema)


async def test_duplicate_membership_rejected_by_unique_constraint() -> None:
    """相同 (user_id, department_id) 的重复关系被数据库唯一约束拒绝。"""
    admin_engine, scoped_engine, schema = await _create_scoped_schema()
    try:
        factory = async_sessionmaker(scoped_engine, expire_on_commit=False)
        async with factory() as session:
            async with _rollback_tx(session):
                dept = Department(name="dept-dup")
                user = User(username="成员乙", uid="member_b", password_hash="hash", role="user")
                session.add_all([dept, user])
                await session.flush()

                repo = DepartmentMembershipRepository(session)
                await repo.add(user.id, dept.id, role="user")
                await session.flush()

                async with session.begin_nested():
                    with pytest.raises(IntegrityError):
                        await repo.add(user.id, dept.id, role="admin")
                        await session.flush()
    finally:
        await scoped_engine.dispose()
        await _drop_scoped_schema(admin_engine, schema)


async def test_invalid_membership_role_rejected_by_check_constraint() -> None:
    """成员角色仅允许 admin/user，非法值被 check 约束拒绝。"""
    admin_engine, scoped_engine, schema = await _create_scoped_schema()
    try:
        factory = async_sessionmaker(scoped_engine, expire_on_commit=False)
        async with factory() as session:
            async with _rollback_tx(session):
                dept = Department(name="dept-bad-role")
                user = User(username="成员丙", uid="member_c", password_hash="hash", role="user")
                session.add_all([dept, user])
                await session.flush()

                async with session.begin_nested():
                    with pytest.raises(IntegrityError):
                        session.add(DepartmentMembership(user_id=user.id, department_id=dept.id, role="owner"))
                        await session.flush()
    finally:
        await scoped_engine.dispose()
        await _drop_scoped_schema(admin_engine, schema)


async def test_add_rejects_missing_department_and_serializes_with_deletion() -> None:
    """add 先锁部门行：部门不存在时拒绝；与删除并发时按共享锁序串行。"""
    admin_engine, scoped_engine, schema = await _create_scoped_schema()
    try:
        factory = async_sessionmaker(scoped_engine, expire_on_commit=False)
        async with factory() as session:
            async with _rollback_tx(session):
                dept = Department(name="dept-race")
                session.add(dept)
                await session.flush()
                repo = DepartmentMembershipRepository(session)
                with pytest.raises(ValueError, match="部门不存在"):
                    await repo.add(1, 999999)

        # 并发场景使用真实提交：delete 持锁期间 add 必须等待，部门删除后 add 被拒绝
        async with factory() as session:
            async with session.begin():
                dept = Department(name="dept-race-live")
                session.add(dept)
                await session.flush()
                live_dept_id = dept.id
                session.add(Department(id=9999, name="默认部门"))

        async def run_add() -> None:
            async with factory() as session:
                async with session.begin():
                    await DepartmentMembershipRepository(session).add(1, live_dept_id)

        async with factory() as session_a:
            tx = await session_a.begin()
            await session_a.execute(select(Department.id).where(Department.id == live_dept_id).with_for_update())
            add_task = asyncio.create_task(run_add())
            await asyncio.sleep(0.5)
            assert not add_task.done(), "add 未按共享锁序等待部门删除事务"
            await DepartmentRepository(session_a).delete_empty_department(live_dept_id)
            await tx.commit()
            with pytest.raises(ValueError, match="部门不存在"):
                await add_task
    finally:
        await scoped_engine.dispose()
        await _drop_scoped_schema(admin_engine, schema)


async def test_auth_session_repository_creates_session_with_uuid_id() -> None:
    """登录会话 repository 创建 UUID 标识的会话行，初始 revision 为 0。"""
    admin_engine, scoped_engine, schema = await _create_scoped_schema()
    try:
        factory = async_sessionmaker(scoped_engine, expire_on_commit=False)
        async with factory() as session:
            async with _rollback_tx(session):
                dept = Department(name="dept-session")
                user = User(username="成员丁", uid="member_d", password_hash="hash", role="user")
                session.add_all([dept, user])
                await session.flush()

                repo = AuthSessionRepository(session)
                created = await repo.create(
                    user_id=user.id,
                    active_department_id=dept.id,
                    expires_at=datetime.now() + timedelta(hours=1),
                )
                fetched = await repo.get(created.id)
                assert fetched is not None
                assert fetched.user_id == user.id
                assert fetched.active_department_id == dept.id
                assert fetched.revision == 0
                assert fetched.revoked_at is None
                assert len(created.id) == 36  # UUID 字符串

                no_dept = await repo.create(user_id=user.id, active_department_id=None, expires_at=datetime.now())
                assert no_dept.active_department_id is None
    finally:
        await scoped_engine.dispose()
        await _drop_scoped_schema(admin_engine, schema)


async def test_delete_boundary_removes_membership_but_retains_account() -> None:
    """删除边界：成员关系随部门删除，账号保留且不迁入默认部门。"""
    admin_engine, scoped_engine, schema = await _create_scoped_schema()
    try:
        factory = async_sessionmaker(scoped_engine, expire_on_commit=False)
        async with factory() as session:
            async with _rollback_tx(session):
                dept_a = Department(name="dept-del-a")
                user = User(username="成员戊", uid="member_e", password_hash="hash", role="user")
                session.add_all([dept_a, user])
                await session.flush()
                membership_repo = DepartmentMembershipRepository(session)
                await membership_repo.add(user.id, dept_a.id, role="user")
                await session.flush()

                await DepartmentRepository(session).delete_empty_department(dept_a.id)
                await session.flush()

                membership = await session.scalar(
                    select(DepartmentMembership).where(DepartmentMembership.user_id == user.id)
                )
                assert membership is None
                assert user.is_deleted == 0
                assert user.department_id is None
                assert await session.get(Department, dept_a.id) is None
    finally:
        await scoped_engine.dispose()
        await _drop_scoped_schema(admin_engine, schema)


async def test_business_schema_v13_upgrades_to_v14_idempotently(monkeypatch) -> None:
    """v13 库升级到 v14 后新列/新表存在，旧行保留，重复执行幂等。"""
    admin_engine, scoped_engine, schema = await _create_scoped_schema()
    try:
        manager = _scoped_manager(scoped_engine)
        manager.AsyncSession = async_sessionmaker(scoped_engine, expire_on_commit=False)

        # 模拟 v13 形态：移除本版本新增的列与表，并保留一行历史账号数据
        async with scoped_engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO users (username, uid, password_hash, role, is_deleted, "
                    "login_failed_count, created_at) "
                    "VALUES ('旧管理员', 'legacy_admin', 'hash', 'admin', 0, 0, NOW())"
                )
            )
            for table in (
                "agent_run_requests",
                "agent_runs",
                "agent_tasks",
                "task_executions",
                "agentscope_channel_bindings",
            ):
                await connection.execute(text(f"ALTER TABLE {table} DROP COLUMN IF EXISTS department_id"))
            await connection.execute(text("ALTER TABLE cli_auth_sessions DROP COLUMN IF EXISTS approved_department_id"))
            await connection.execute(
                text("ALTER TABLE agent_memory_scopes DROP COLUMN IF EXISTS maintenance_department_id")
            )
            await connection.execute(text("DROP TABLE IF EXISTS auth_sessions"))
            await connection.execute(text("DROP TABLE IF EXISTS department_memberships"))
        await manager.create_schema_version_table()
        await manager.record_schema_version("business", BUSINESS_SCHEMA_VERSION - 1)

        async def no_op_async(*args, **kwargs) -> None:
            return None

        async def no_workdir_plan(*args, **kwargs):
            return SimpleNamespace(requires_cutover=False, workdirs=[], conversations=[])

        monkeypatch.setattr(storage_migration, "pg_manager", manager)
        monkeypatch.setattr(manager, "initialize", lambda: None)
        monkeypatch.setattr(manager, "close", no_op_async)
        monkeypatch.setattr(storage_migration, "read_v071_workdir_plan", no_workdir_plan)
        monkeypatch.setattr(storage_migration, "_legacy_skill_roots_exist", lambda: False)
        monkeypatch.setattr(storage_migration, "_legacy_system_config_exists", lambda: False)
        monkeypatch.setattr(storage_migration, "runtime_storage_requires_quiescence", lambda: False)
        monkeypatch.setattr(storage_migration, "lite_mode_enabled", lambda: True)
        monkeypatch.setattr(storage_migration, "_converge_database_state", no_op_async)
        monkeypatch.setattr(storage_migration, "migrate_shared_skills", no_op_async)
        monkeypatch.setattr(storage_migration, "mark_v071_skills_migrated", lambda: None)
        monkeypatch.setattr(storage_migration, "migrate_runtime_storage_identity", lambda: None)

        await storage_migration.main()
        await storage_migration.main()

        async with scoped_engine.connect() as connection:
            legacy_user = await connection.scalar(text("SELECT role FROM users WHERE uid = 'legacy_admin'"))
            assert legacy_user == "admin"
            legacy_memberships = await connection.scalar(text("SELECT count(*) FROM department_memberships"))
            assert legacy_memberships == 0
            for table in (
                "agent_run_requests",
                "agent_runs",
                "agent_tasks",
                "task_executions",
                "agentscope_channel_bindings",
            ):
                column_exists = await connection.scalar(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
                        "WHERE table_schema = :schema AND table_name = :table "
                        "AND column_name = 'department_id')"
                    ),
                    {"schema": schema, "table": table},
                )
                assert column_exists is True, table
            for table, column in (
                ("cli_auth_sessions", "approved_department_id"),
                ("agent_memory_scopes", "maintenance_department_id"),
            ):
                column_exists = await connection.scalar(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
                        "WHERE table_schema = :schema AND table_name = :table "
                        "AND column_name = :column)"
                    ),
                    {"schema": schema, "table": table, "column": column},
                )
                assert column_exists is True, table
            for table in ("auth_sessions", "department_memberships"):
                table_exists = await connection.scalar(
                    text("SELECT to_regclass(:name) IS NOT NULL"),
                    {"name": f"{schema}.{table}"},
                )
                assert table_exists is True, table
            role_check = await connection.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM information_schema.table_constraints "
                    "WHERE constraint_schema = :schema AND table_name = 'department_memberships' "
                    "AND constraint_name = 'ck_department_membership_role')"
                ),
                {"schema": schema},
            )
            assert role_check is True
            # 两条建表路径必须产生同一外键语义：user_id 引用 users，department_id 引用 departments
            fk_target = await connection.scalar(
                text(
                    "SELECT ccu.table_name FROM information_schema.table_constraints tc "
                    "JOIN information_schema.key_column_usage kcu ON tc.constraint_name = kcu.constraint_name "
                    "JOIN information_schema.constraint_column_usage ccu "
                    "ON tc.constraint_name = ccu.constraint_name "
                    "WHERE tc.constraint_schema = :schema AND tc.table_name = 'department_memberships' "
                    "AND tc.constraint_type = 'FOREIGN KEY' AND kcu.column_name = 'user_id'"
                ),
                {"schema": schema},
            )
            assert fk_target == "users"

        # DDL 建出的表必须拒绝不存在的 user_id（防外键指向漂移回归）
        factory = async_sessionmaker(scoped_engine, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                session.add(Department(name="dept-fk-guard"))
                await session.flush()
                async with session.begin_nested():
                    with pytest.raises(IntegrityError, match="users"):
                        session.add(DepartmentMembership(user_id=987654, department_id=1, role="user"))
                        await session.flush()

        assert await manager.get_schema_versions() == {"business": BUSINESS_SCHEMA_VERSION}
        await manager.require_current_schema(include_knowledge=False)
    finally:
        await scoped_engine.dispose()
        await _drop_scoped_schema(admin_engine, schema)


async def test_empty_database_initializes_to_current_business_schema(monkeypatch) -> None:
    """空库（未记录版本）初始化后直接到达当前 business schema 版本。"""
    admin_engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    schema = f"pytest_dept_empty_{uuid.uuid4().hex[:12]}"
    try:
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        scoped_engine = create_async_engine(
            os.environ["POSTGRES_URL"],
            pool_pre_ping=True,
            connect_args={"server_settings": {"search_path": schema}},
        )
        manager = _scoped_manager(scoped_engine)
        manager.AsyncSession = async_sessionmaker(scoped_engine, expire_on_commit=False)
        await manager.create_schema_version_table()

        async def no_op_async(*args, **kwargs) -> None:
            return None

        async def no_workdir_plan(*args, **kwargs):
            return SimpleNamespace(requires_cutover=False, workdirs=[], conversations=[])

        monkeypatch.setattr(storage_migration, "pg_manager", manager)
        monkeypatch.setattr(manager, "initialize", lambda: None)
        monkeypatch.setattr(manager, "close", no_op_async)
        monkeypatch.setattr(storage_migration, "read_v071_workdir_plan", no_workdir_plan)
        monkeypatch.setattr(storage_migration, "_legacy_skill_roots_exist", lambda: False)
        monkeypatch.setattr(storage_migration, "_legacy_system_config_exists", lambda: False)
        monkeypatch.setattr(storage_migration, "runtime_storage_requires_quiescence", lambda: False)
        monkeypatch.setattr(storage_migration, "lite_mode_enabled", lambda: True)
        monkeypatch.setattr(storage_migration, "_converge_database_state", no_op_async)
        monkeypatch.setattr(storage_migration, "migrate_shared_skills", no_op_async)
        monkeypatch.setattr(storage_migration, "mark_v071_skills_migrated", lambda: None)
        monkeypatch.setattr(storage_migration, "migrate_runtime_storage_identity", lambda: None)

        await storage_migration.main()

        assert await manager.get_schema_versions() == {"business": BUSINESS_SCHEMA_VERSION}
        async with scoped_engine.connect() as connection:
            membership_table = await connection.scalar(
                text("SELECT to_regclass(:name) IS NOT NULL"), {"name": f"{schema}.department_memberships"}
            )
            assert membership_table is True
        await scoped_engine.dispose()
    finally:
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin_engine.dispose()


async def test_delete_http_removes_membership_but_retains_account(test_client, admin_headers) -> None:
    """删除路由按边界清理成员关系：204 且账号与其他事实不变，而非外键 500。"""
    suffix = uuid.uuid4().hex[:8]
    engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    department_id = None
    try:
        async with engine.begin() as connection:
            row = await connection.execute(
                text("INSERT INTO departments (name, created_at) VALUES (:name, NOW()) RETURNING id"),
                {"name": f"dept-409-{suffix}"},
            )
            department_id = row.scalar_one()
            admin_row = await connection.execute(
                text("SELECT id, role, department_id, is_deleted FROM users WHERE is_deleted = 0 ORDER BY id LIMIT 1")
            )
            admin = admin_row.one()
            admin_id = admin.id
            key_state_before = await connection.scalar(
                text("SELECT count(*) FROM api_keys WHERE department_id = :id AND revoked_at IS NULL"),
                {"id": department_id},
            )
            await connection.execute(
                text(
                    "INSERT INTO department_memberships (user_id, department_id, role, created_at) "
                    "VALUES (:user_id, :department_id, 'user', NOW())"
                ),
                {"user_id": admin_id, "department_id": department_id},
            )

        response = await test_client.delete(
            f"/api/departments/{department_id}",
            headers=admin_headers,
        )
        assert response.status_code == 204

        async with engine.begin() as connection:
            still_there = await connection.scalar(
                text("SELECT COUNT(*) FROM department_memberships WHERE department_id = :id"),
                {"id": department_id},
            )
            assert still_there == 0
            dept_gone = await connection.scalar(
                text("SELECT COUNT(*) FROM departments WHERE id = :id"),
                {"id": department_id},
            )
            assert dept_gone == 0
            admin_after_result = await connection.execute(
                text("SELECT role, department_id, is_deleted FROM users WHERE id = :id"), {"id": admin_id}
            )
            admin_after = admin_after_result.one()
            assert tuple(admin_after) == (admin.role, admin.department_id, admin.is_deleted)
            key_state_after = await connection.scalar(
                text("SELECT count(*) FROM api_keys WHERE department_id = :id AND revoked_at IS NULL"),
                {"id": department_id},
            )
            assert key_state_after == key_state_before
    finally:
        if department_id is not None:
            async with engine.begin() as connection:
                await connection.execute(
                    text("DELETE FROM department_memberships WHERE department_id = :id"),
                    {"id": department_id},
                )
                await connection.execute(text("DELETE FROM departments WHERE id = :id"), {"id": department_id})
        await engine.dispose()


def test_auth_session_model_fields() -> None:
    """AuthSession 模型声明与会话生命周期相关的基础字段。"""
    columns = {c.name for c in AuthSession.__table__.columns}
    assert {
        "id",
        "user_id",
        "active_department_id",
        "revision",
        "expires_at",
        "revoked_at",
        "created_at",
    } <= columns


def test_department_membership_composite_key() -> None:
    """DepartmentMembership 以 (user_id, department_id) 作为组合主键。"""
    pk = {c.name for c in DepartmentMembership.__table__.primary_key.columns}
    assert pk == {"user_id", "department_id"}
