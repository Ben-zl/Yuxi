"""Yuxi Schema 版本事实在真实 PostgreSQL 上的集成测试。"""

from __future__ import annotations

import asyncio
import os
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi import storage_migration
from yuxi.storage.postgres.manager import BUSINESS_SCHEMA_VERSION, PostgresManager

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest.fixture(scope="session", autouse=True)
def ensure_live_api_schema():
    """本文件自行创建隔离 Schema，不依赖运行中的 API。"""


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_knowledge_resources():
    """隔离 Schema 测试没有 HTTP 资源需要清理。"""
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


async def test_schema_migration_lock_serializes_real_postgres_sessions() -> None:
    """两个 migrator 竞争同一 advisory lock 时只允许一个进入临界区。"""
    engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    manager = _scoped_manager(engine)
    first_entered = asyncio.Event()
    release_first = asyncio.Event()
    second_entered = asyncio.Event()

    async def first_migrator() -> None:
        async with manager.schema_migration_lock():
            first_entered.set()
            await release_first.wait()

    async def second_migrator() -> None:
        await first_entered.wait()
        async with manager.schema_migration_lock():
            second_entered.set()

    first_task = asyncio.create_task(first_migrator())
    second_task = asyncio.create_task(second_migrator())
    try:
        await asyncio.wait_for(first_entered.wait(), timeout=2)
        await asyncio.sleep(0.1)
        assert second_entered.is_set() is False
        release_first.set()
        await asyncio.wait_for(asyncio.gather(first_task, second_task), timeout=2)
        assert second_entered.is_set() is True
    finally:
        release_first.set()
        for task in (first_task, second_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(first_task, second_task, return_exceptions=True)
        await engine.dispose()


async def test_schema_version_is_persisted_and_runtime_validation_fails_closed() -> None:
    """版本表缺失、错误和正确三种状态必须形成精确启动结论。"""
    schema = f"pytest_schema_version_{uuid.uuid4().hex[:16]}"
    admin_engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    scoped_engine = None

    try:
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))

        scoped_engine = create_async_engine(
            os.environ["POSTGRES_URL"],
            pool_pre_ping=True,
            connect_args={"server_settings": {"search_path": schema}},
        )
        manager = _scoped_manager(scoped_engine)

        with pytest.raises(RuntimeError, match="business=missing"):
            await manager.require_current_schema(include_knowledge=False)

        await manager.create_schema_version_table()
        await manager.record_schema_version("business", BUSINESS_SCHEMA_VERSION + 1)
        with pytest.raises(RuntimeError, match=f"business={BUSINESS_SCHEMA_VERSION + 1}"):
            await manager.require_current_schema(include_knowledge=False)

        await manager.record_schema_version("business", BUSINESS_SCHEMA_VERSION)
        await manager.require_current_schema(include_knowledge=False)
        assert await manager.get_schema_versions() == {"business": BUSINESS_SCHEMA_VERSION}
    finally:
        if scoped_engine is not None:
            await scoped_engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin_engine.dispose()


async def test_business_schema_v3_upgrades_to_v4_idempotently_on_real_postgres(monkeypatch) -> None:
    """真实迁移入口在 PostgreSQL 上将 v3 幂等升级到 v4。"""
    schema = f"pytest_business_v4_{uuid.uuid4().hex[:16]}"
    admin_engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    scoped_engine = None

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
        await manager.create_business_tables()
        await manager.create_schema_version_table()

        async with scoped_engine.begin() as connection:
            await connection.execute(text("DROP INDEX IF EXISTS ix_agents_deletion_pending_at"))
            await connection.execute(text("ALTER TABLE agents DROP COLUMN IF EXISTS deletion_pending_at"))
        await manager.record_schema_version("business", 3)

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
            column_exists = await connection.scalar(
                text(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM information_schema.columns
                        WHERE table_schema = :schema
                          AND table_name = 'agents'
                          AND column_name = 'deletion_pending_at'
                    )
                    """
                ),
                {"schema": schema},
            )
            index_exists = await connection.scalar(
                text("SELECT to_regclass(:index_name) IS NOT NULL"),
                {"index_name": f"{schema}.ix_agents_deletion_pending_at"},
            )

        assert column_exists is True
        assert index_exists is True
        assert await manager.get_schema_versions() == {"business": BUSINESS_SCHEMA_VERSION}
        await manager.require_current_schema(include_knowledge=False)
    finally:
        if scoped_engine is not None:
            await scoped_engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin_engine.dispose()
