"""在隔离 PostgreSQL schema 验证旧 ORM 的 MCP 索引升级。"""

import os
from uuid import uuid4

import pytest
from sqlalchemy import Column, Integer, JSON, String, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import declarative_base

from yuxi.storage.postgres.manager import PostgresManager
from yuxi.storage.postgres.models_business import Base

LegacyBase = declarative_base()


class LegacyMCPServer(LegacyBase):
    """复现旧 ORM 的唯一 slug 索引及历史凭据列。"""

    __tablename__ = "mcp_servers"
    id = Column(Integer, primary_key=True)
    slug = Column(String(100), unique=True, index=True, nullable=False)
    headers = Column(JSON)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_old_orm_upgrade_preserves_rows_and_allows_duplicate_slugs():
    """完整升级只操作随机 schema，并证明重跑幂等及真实建表。"""
    schema = "test_mcp_upgrade_" + uuid4().hex
    engine = create_async_engine(os.environ["POSTGRES_URL"])
    isolated = create_async_engine(
        os.environ["POSTGRES_URL"], connect_args={"server_settings": {"search_path": schema}}
    )
    manager = object.__new__(PostgresManager)
    manager._initialized = True
    manager.async_engine = isolated
    try:
        async with engine.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        async with isolated.begin() as conn:
            tables = [t for t in Base.metadata.sorted_tables if t.name not in {"mcp_servers", "run_resource_snapshots"}]
            await conn.run_sync(lambda sync: Base.metadata.create_all(sync, tables=tables))
            await conn.run_sync(LegacyBase.metadata.create_all)
            await conn.execute(
                LegacyMCPServer.__table__.insert().values(id=1, slug="same", headers={"Auth": "fixture"})
            )
            assert (
                await conn.scalar(
                    text(
                        "SELECT indisunique FROM pg_index JOIN pg_class ON pg_class.oid=indexrelid "
                        "WHERE relname='ix_mcp_servers_slug' AND relnamespace=current_schema()::regnamespace"
                    )
                )
                is True
            )
        await manager.ensure_business_schema()
        async with isolated.begin() as conn:
            before = (await conn.execute(text("SELECT * FROM mcp_servers WHERE id=1"))).mappings().one()
            assert before["headers"] == {"Auth": "fixture"}
            assert before["resource_id"]
            assert before["share_config"]["read_scope"]["access_level"] == "global"
            assert (
                await conn.scalar(
                    text(
                        "SELECT indisunique FROM pg_index JOIN pg_class ON pg_class.oid=indexrelid "
                        "WHERE relname='ix_mcp_servers_slug' AND relnamespace=current_schema()::regnamespace"
                    )
                )
                is False
            )
            await conn.execute(
                text(
                    "INSERT INTO mcp_servers(id,slug,resource_id,share_config) "
                    "SELECT 2,slug,'second-resource',share_config FROM mcp_servers WHERE id=1"
                )
            )
        await manager.ensure_business_schema()
        async with isolated.begin() as conn:
            after = (await conn.execute(text("SELECT * FROM mcp_servers WHERE id=1"))).mappings().one()
            assert dict(after) == dict(before)
            assert await conn.scalar(text("SELECT count(*) FROM mcp_servers WHERE slug='same'")) == 2
            columns = set(
                (
                    await conn.execute(
                        text(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE table_schema=current_schema() AND table_name='run_resource_snapshots'"
                        )
                    )
                ).scalars()
            )
            assert {"id", "uid", "encrypted_payload", "fingerprint", "created_at"} <= columns
    finally:
        await isolated.dispose()
        async with engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await engine.dispose()
