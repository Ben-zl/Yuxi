from types import SimpleNamespace

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.repositories.mcp_repository import list_authorizable_mcp_servers
from yuxi.storage.postgres.models_business import MCPServer


@pytest_asyncio.fixture
async def mcp_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(MCPServer.__table__.create)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


async def test_authorizable_stdio_mcp_uses_builtin_catalog(mcp_session):
    """保存期与运行时使用同一内置 slug 注册表，不信任 created_by。"""
    mcp_session.add_all(
        [
            MCPServer(
                slug="mcp-server-chart",
                name="Builtin",
                transport="stdio",
                enabled=1,
                created_by="unexpected-owner",
                updated_by="unexpected-owner",
            ),
            MCPServer(
                slug="forged-system-stdio",
                name="Forged",
                transport="stdio",
                enabled=1,
                created_by="system",
                updated_by="system",
            ),
        ]
    )
    await mcp_session.commit()

    servers = await list_authorizable_mcp_servers(
        mcp_session,
        user=SimpleNamespace(uid="root", role="superadmin", department_id=None),
    )

    assert [server.slug for server in servers] == ["mcp-server-chart"]
