from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from yuxi.agents.mcp import service as mcp_service
from yuxi.storage.postgres import manager as postgres_manager
from yuxi.storage.postgres.models_business import MCPServer


class _AsyncSessionContext:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *_args):
        return False


class _FailingSessionContext:
    async def __aenter__(self):
        raise RuntimeError("database-secret-must-not-be-swallowed")

    async def __aexit__(self, *_args):
        return False


@pytest_asyncio.fixture
async def mcp_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(MCPServer.__table__.create)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await engine.dispose()


class _FakeClient:
    def __init__(self, tools):
        self._tools = tools
        self.connect_count = 0
        self.close_count = 0

    async def connect(self):
        self.connect_count += 1

    async def close(self):
        self.close_count += 1

    async def list_tools(self):
        return self._tools


def _superadmin():
    return SimpleNamespace(uid="root", role="superadmin", department_id=None)


async def test_sanitized_read_keeps_credentials_editable(monkeypatch, mcp_session):
    """脱敏读取不改凭据，省略更新保留，显式更新可持久化。"""
    server = MCPServer(
        slug="editable",
        name="Editable",
        transport="streamable_http",
        url="https://example.test/mcp",
        headers={"Authorization": "old-fixture"},
        created_by="admin",
        updated_by="admin",
        enabled=1,
    )
    mcp_session.add(server)
    await mcp_session.commit()
    assert "headers" not in server.to_dict(sanitize=True)
    await mcp_service.update_mcp_server(mcp_session, slug=server.resource_id, name="Renamed", operator=_superadmin())
    await mcp_session.refresh(server)
    assert server.headers == {"Authorization": "old-fixture"}
    await mcp_service.update_mcp_server(
        mcp_session,
        slug=server.resource_id,
        headers={"Authorization": "new-fixture"},
        operator=_superadmin(),
    )
    await mcp_session.refresh(server)
    assert server.headers == {"Authorization": "new-fixture"}
    assert "headers" not in server.to_dict(sanitize=True)


async def test_ensure_builtin_mcp_servers_removes_retired_system_server(monkeypatch, mcp_session):
    retired_server = MCPServer(
        slug="sequentialthinking",
        name="sequentialthinking",
        description="old builtin",
        transport="streamable_http",
        url="https://remote.mcpservers.org/sequentialthinking/mcp",
        enabled=1,
        created_by="system",
        updated_by="system",
    )
    mcp_session.add(retired_server)
    await mcp_session.commit()

    monkeypatch.setattr(
        postgres_manager.pg_manager,
        "get_async_session_context",
        lambda: _AsyncSessionContext(mcp_session),
    )

    await mcp_service.ensure_builtin_mcp_servers_in_db()

    retired = await mcp_session.scalar(select(MCPServer).where(MCPServer.slug == "sequentialthinking"))
    chart = await mcp_session.scalar(select(MCPServer).where(MCPServer.slug == "mcp-server-chart"))
    assert retired is None
    assert chart is not None


async def test_ensure_builtin_mcp_servers_preserves_user_server_with_retired_slug(monkeypatch, mcp_session):
    user_server = MCPServer(
        slug="sequentialthinking",
        name="用户自定义 MCP",
        description="user managed",
        transport="streamable_http",
        url="https://example.com/mcp",
        enabled=1,
        created_by="admin",
        updated_by="admin",
    )
    mcp_session.add(user_server)
    await mcp_session.commit()

    monkeypatch.setattr(
        postgres_manager.pg_manager,
        "get_async_session_context",
        lambda: _AsyncSessionContext(mcp_session),
    )

    await mcp_service.ensure_builtin_mcp_servers_in_db()

    server = await mcp_session.scalar(select(MCPServer).where(MCPServer.slug == "sequentialthinking"))
    assert server is not None
    assert server.created_by == "admin"


async def test_ensure_builtin_mcp_servers_disables_legacy_user_stdio(monkeypatch, mcp_session):
    legacy_server = MCPServer(
        slug="legacy-stdio",
        name="历史 stdio",
        transport="stdio",
        command="python3",
        enabled=1,
        created_by="system",
        updated_by="system",
    )
    mcp_session.add(legacy_server)
    await mcp_session.commit()

    monkeypatch.setattr(
        postgres_manager.pg_manager,
        "get_async_session_context",
        lambda: _AsyncSessionContext(mcp_session),
    )

    await mcp_service.ensure_builtin_mcp_servers_in_db()

    await mcp_session.refresh(legacy_server)
    assert legacy_server.enabled == 0


async def test_builtin_mcp_initialization_propagates_failure_to_entrypoint(monkeypatch):
    monkeypatch.setattr(
        postgres_manager.pg_manager,
        "get_async_session_context",
        lambda: _FailingSessionContext(),
    )

    with pytest.raises(RuntimeError, match="must-not-be-swallowed"):
        await mcp_service.ensure_builtin_mcp_servers_in_db()


async def test_runtime_configs_exclude_user_created_stdio_servers(mcp_session):
    mcp_session.add_all(
        [
            MCPServer(
                slug="mcp-server-chart",
                name="内置 stdio",
                transport="stdio",
                command="tampered-command",
                args=["--unsafe"],
                enabled=1,
                created_by="system",
                updated_by="system",
            ),
            MCPServer(
                slug="user-stdio",
                name="用户 stdio",
                transport="stdio",
                command="python3",
                args=["-c", "print('unsafe')"],
                enabled=1,
                created_by="admin",
                updated_by="admin",
            ),
            MCPServer(
                slug="forged-system-stdio",
                name="伪造系统 stdio",
                transport="stdio",
                command="python3",
                args=["-c", "print('unsafe')"],
                enabled=1,
                created_by="system",
                updated_by="system",
            ),
            MCPServer(
                slug="remote-http",
                name="远程 HTTP",
                transport="streamable_http",
                url="https://example.com/mcp",
                command="python3",
                args=["-c", "print('stale')"],
                enabled=1,
                created_by="admin",
                updated_by="admin",
            ),
        ]
    )
    await mcp_session.commit()

    user = SimpleNamespace(uid="root", role="superadmin", department_id=None)
    configs = await mcp_service.load_enabled_mcp_server_configs(db=mcp_session, user=user)
    slugs = await mcp_service.get_enabled_mcp_server_slugs(db=mcp_session, user=user)

    assert set(configs) == {"mcp-server-chart", "remote-http"}
    assert set(slugs) == {"mcp-server-chart", "remote-http"}
    assert configs["mcp-server-chart"]["command"] == "npx"
    assert configs["mcp-server-chart"]["args"] == ["-y", "@antv/mcp-server-chart"]
    assert "command" not in configs["remote-http"]
    assert "args" not in configs["remote-http"]


@pytest.mark.parametrize(
    "loader",
    [mcp_service.load_enabled_mcp_server_configs, mcp_service.get_enabled_mcp_server_slugs],
)
async def test_authorized_mcp_loaders_require_user_context(loader, mcp_session):
    """运行时与选择器入口缺少用户时必须 fail closed。"""
    with pytest.raises(PermissionError, match="当前用户"):
        await loader(db=mcp_session)


async def test_mcp_management_list_requires_user_context(mcp_session):
    with pytest.raises(PermissionError, match="当前用户"):
        await mcp_service.get_all_mcp_servers(mcp_session)


async def test_create_mcp_server_rejects_user_created_stdio(mcp_session):
    with pytest.raises(ValueError, match="stdio"):
        await mcp_service.create_mcp_server(
            mcp_session,
            slug="unsafe-mcp",
            name="Unsafe MCP",
            transport="stdio",
            created_by="admin",
            operator=_superadmin(),
        )

    server = await mcp_session.scalar(select(MCPServer).where(MCPServer.slug == "unsafe-mcp"))
    assert server is None


async def test_create_mcp_server_rejects_builtin_slug(mcp_session):
    with pytest.raises(ValueError, match="slug"):
        await mcp_service.create_mcp_server(
            mcp_session,
            slug="mcp-server-chart",
            name="伪造内置 MCP",
            transport="streamable_http",
            url="https://example.com/mcp",
            created_by="admin",
            operator=_superadmin(),
        )


async def test_update_builtin_mcp_server_rejects_connection_changes(mcp_session):
    server = MCPServer(
        slug="mcp-server-chart",
        name="内置 stdio",
        transport="stdio",
        command="trusted-command",
        enabled=1,
        created_by="system",
        updated_by="system",
    )
    mcp_session.add(server)
    await mcp_session.commit()

    with pytest.raises(PermissionError, match="系统内置"):
        await mcp_service.update_mcp_server(
            mcp_session,
            slug="mcp-server-chart",
            transport="streamable_http",
            url="https://example.com/mcp",
            updated_by="admin",
            operator=_superadmin(),
        )

    await mcp_session.refresh(server)
    assert server.transport == "stdio"
    assert server.command == "trusted-command"


async def test_update_legacy_stdio_requires_remote_url(mcp_session):
    legacy_server = MCPServer(
        slug="legacy-stdio",
        name="历史 stdio",
        transport="stdio",
        command="python3",
        enabled=0,
        created_by="admin",
        updated_by="admin",
    )
    mcp_session.add(legacy_server)
    await mcp_session.commit()

    with pytest.raises(ValueError, match="url 必填"):
        await mcp_service.update_mcp_server(
            mcp_session,
            slug="legacy-stdio",
            transport="streamable_http",
            updated_by="admin",
            operator=_superadmin(),
        )

    await mcp_session.refresh(legacy_server)
    assert legacy_server.transport == "stdio"
    assert legacy_server.command == "python3"


@pytest.mark.parametrize(
    "operation",
    [
        lambda db: mcp_service.create_mcp_server(
            db, slug="missing-operator", name="Missing", transport="streamable_http", url="https://example.test"
        ),
        lambda db: mcp_service.update_mcp_server(db, slug="missing-operator", name="Missing"),
        lambda db: mcp_service.delete_mcp_server(db, slug="missing-operator"),
        lambda db: mcp_service.set_server_enabled(db, "missing-operator", True),
        lambda db: mcp_service.toggle_tool_enabled(db, "missing-operator", "tool"),
    ],
)
async def test_mcp_mutations_require_operator_before_database_access(mcp_session, operation):
    with pytest.raises(PermissionError, match="当前操作者"):
        await operation(mcp_session)


async def test_get_enabled_mcp_tools_loads_latest_config_from_db(monkeypatch):
    captured: list[dict] = []
    user = SimpleNamespace(uid="user-1", role="user")

    async def fake_get_enabled_mcp_server_config(server_name: str, db=None, user=None):
        del db
        assert server_name == "demo"
        assert user is not None
        return {"resource_id": "mcp-demo", "transport": "stdio", "command": "demo", "disabled_tools": ["tool_b"]}

    async def fake_get_mcp_tools(server_name: str, additional_servers=None, disabled_tools=None, **kwargs):
        del kwargs
        captured.append(
            {
                "server_name": server_name,
                "additional_servers": additional_servers,
                "disabled_tools": list(disabled_tools or []),
            }
        )
        return ["tool-a"]

    monkeypatch.setattr(mcp_service, "get_enabled_mcp_server_config", fake_get_enabled_mcp_server_config)
    monkeypatch.setattr(mcp_service, "get_mcp_tools", fake_get_mcp_tools)

    tools = await mcp_service.get_enabled_mcp_tools("demo", user=user)

    assert tools == ["tool-a"]
    assert captured == [
        {
            "server_name": "demo",
            "additional_servers": {
                "demo": {
                    "resource_id": "mcp-demo",
                    "transport": "stdio",
                    "command": "demo",
                    "disabled_tools": ["tool_b"],
                }
            },
            "disabled_tools": ["tool_b"],
        }
    ]


async def test_get_mcp_tools_rebuilds_cache_when_config_hash_changes(monkeypatch):
    mcp_service.clear_mcp_cache()

    configs = [
        {"resource_id": "mcp-demo", "transport": "streamable_http", "url": "http://demo-v1/mcp", "disabled_tools": []},
        {"resource_id": "mcp-demo", "transport": "streamable_http", "url": "http://demo-v2/mcp", "disabled_tools": []},
    ]
    build_calls: list[str] = []

    async def fake_get_enabled_mcp_server_config(server_name: str, db=None, user=None):
        del db, user
        assert server_name == "demo"
        return configs[0]

    async def fake_get_mcp_client(server_configs):
        config = server_configs["mcp-demo"]
        version = config["url"].split("//", maxsplit=1)[1].split("/", maxsplit=1)[0]
        build_calls.append(version)
        return _FakeClient([SimpleNamespace(name=f"tool_for_{version}", metadata={})])

    monkeypatch.setattr(mcp_service, "get_enabled_mcp_server_config", fake_get_enabled_mcp_server_config)
    monkeypatch.setattr(mcp_service, "get_mcp_client", fake_get_mcp_client)

    user = SimpleNamespace(uid="user-1", role="user")
    tools_v1_first = await mcp_service.get_mcp_tools("demo", user=user)
    tools_v1_second = await mcp_service.get_mcp_tools("demo", user=user)

    configs[0] = configs[1]
    tools_v2 = await mcp_service.get_mcp_tools("demo", user=user)

    assert [tool.name for tool in tools_v1_first] == ["tool_for_demo-v1"]
    assert [tool.name for tool in tools_v1_second] == ["tool_for_demo-v1"]
    assert [tool.name for tool in tools_v2] == ["tool_for_demo-v2"]
    assert build_calls == ["demo-v1", "demo-v2"]

    mcp_service.clear_mcp_cache()


async def test_get_tools_from_all_servers_loads_names_from_db_once(monkeypatch):
    server_configs = {
        "alpha": {"resource_id": "mcp-alpha", "transport": "stdio", "command": "cmd-a", "disabled_tools": []},
        "beta": {"resource_id": "mcp-beta", "transport": "stdio", "command": "cmd-b", "disabled_tools": []},
    }
    calls: list[tuple[str, dict[str, dict]]] = []

    async def fake_load_enabled_mcp_server_configs(*, names=None, db=None, user=None, use_resource_ids=False):
        del names, db, user, use_resource_ids
        return server_configs

    async def fake_get_mcp_tools(server_name: str, additional_servers=None, **kwargs):
        del kwargs
        calls.append((server_name, additional_servers or {}))
        return [server_name]

    monkeypatch.setattr(mcp_service, "load_enabled_mcp_server_configs", fake_load_enabled_mcp_server_configs)
    monkeypatch.setattr(mcp_service, "get_mcp_tools", fake_get_mcp_tools)

    tools = await mcp_service.get_tools_from_all_servers(user=SimpleNamespace(uid="user-1", role="user"))

    assert tools == ["alpha", "beta"]
    assert calls == [
        ("alpha", server_configs),
        ("beta", server_configs),
    ]


async def test_get_mcp_tools_sets_stable_management_id(monkeypatch):
    mcp_service.clear_mcp_cache()

    config = {
        "resource_id": "mcp-demo",
        "transport": "streamable_http",
        "url": "http://demo-tool/mcp",
        "disabled_tools": [],
    }

    async def fake_get_enabled_mcp_server_config(server_name: str, db=None, user=None):
        del server_name, db, user
        return config

    async def fake_get_mcp_client(_server_configs):
        tool = SimpleNamespace(name="demo_tool", metadata={})
        return _FakeClient([tool])

    monkeypatch.setattr(mcp_service, "get_enabled_mcp_server_config", fake_get_enabled_mcp_server_config)
    monkeypatch.setattr(mcp_service, "get_mcp_client", fake_get_mcp_client)

    tools = await mcp_service.get_mcp_tools("demo", user=SimpleNamespace(uid="user-1", role="user"))
    assert len(tools) == 1
    assert tools[0].metadata["id"] == "mcp__mcpDemo__demoTool"
    assert tools[0].metadata["mcp_tool_name"] == "demo_tool"

    mcp_service.clear_mcp_cache()


async def test_get_mcp_tools_filters_by_original_remote_name(monkeypatch):
    """禁用列表使用 MCP 原始名，不受运行时命名空间改写影响。"""
    mcp_service.clear_mcp_cache()

    async def fake_get_mcp_client(_server_configs):
        return _FakeClient([SimpleNamespace(name="mcp__mcp-demo__echo", metadata={})])

    monkeypatch.setattr(mcp_service, "get_mcp_client", fake_get_mcp_client)

    tools = await mcp_service.get_mcp_tools(
        "demo",
        additional_servers={
            "demo": {
                "transport": "streamable_http",
                "resource_id": "mcp-demo",
                "url": "http://demo-tool/mcp",
                "disabled_tools": ["echo"],
            }
        },
        disabled_tools=["echo"],
    )

    assert tools == []
    mcp_service.clear_mcp_cache()


async def test_get_mcp_tools_does_not_connect_stateless_http_client(monkeypatch):
    """HTTP MCP 每次工具调用自行建连，不保持服务级 stateful 连接。"""
    mcp_service.clear_mcp_cache()
    client = _FakeClient([SimpleNamespace(name="echo", metadata={})])

    async def fake_get_enabled_mcp_server_config(server_name: str, db=None, user=None):
        del server_name, db, user
        return {"resource_id": "mcp-remote", "transport": "streamable_http", "url": "http://mcp.example/mcp"}

    async def fake_get_mcp_client(_server_configs):
        return client

    monkeypatch.setattr(mcp_service, "get_enabled_mcp_server_config", fake_get_enabled_mcp_server_config)
    monkeypatch.setattr(mcp_service, "get_mcp_client", fake_get_mcp_client)

    await mcp_service.get_mcp_tools("remote", user=SimpleNamespace(uid="user-1", role="user"))
    assert client.connect_count == 0

    mcp_service.clear_mcp_cache()
    assert client.close_count == 0


@pytest.mark.asyncio
async def test_public_mcp_tool_entrypoint_requires_user_context():
    with pytest.raises(PermissionError, match="用户授权上下文"):
        await mcp_service.get_mcp_tools("mcp-resource")


@pytest.mark.asyncio
async def test_mcp_tool_cache_requires_immutable_resource_id(monkeypatch):
    with pytest.raises(PermissionError, match="resource_id"):
        await mcp_service.get_mcp_tools(
            "legacy-slug",
            additional_servers={"legacy-slug": {"transport": "streamable_http", "url": "http://example.test/mcp"}},
            user=SimpleNamespace(uid="user-1", role="user"),
        )


async def test_builtin_stdio_tools_open_and_close_within_discovery_and_call(monkeypatch):
    """stdio 发现和调用各自使用同一 task 内的短连接。"""
    lifecycle: list[str] = []

    @asynccontextmanager
    async def fake_stdio_client(_params):
        lifecycle.append("transport-enter")
        try:
            yield (object(), object())
        finally:
            lifecycle.append("transport-exit")

    class FakeSession:
        def __init__(self, *_args):
            pass

        async def __aenter__(self):
            lifecycle.append("session-enter")
            return self

        async def __aexit__(self, *_args):
            lifecycle.append("session-exit")

        async def initialize(self):
            lifecycle.append("initialize")

        async def list_tools(self):
            return SimpleNamespace(
                tools=[
                    SimpleNamespace(
                        name="generate_chart",
                        description="Generate a chart",
                        inputSchema={"type": "object", "properties": {}},
                        annotations=None,
                    )
                ]
            )

        async def call_tool(self, name, arguments, read_timeout_seconds):
            del read_timeout_seconds
            lifecycle.append(f"call:{name}:{arguments['title']}")
            return SimpleNamespace(content=[], isError=False)

    monkeypatch.setattr(mcp_service, "stdio_client", fake_stdio_client)
    monkeypatch.setattr(mcp_service, "ClientSession", FakeSession)
    monkeypatch.setattr("agentscope.tool._adapters.ClientSession", FakeSession)

    tools = await mcp_service._build_stdio_mcp_tools(
        "mcp-server-chart",
        {
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "@antv/mcp-server-chart"],
        },
    )
    await tools[0].call(title="demo")

    assert lifecycle == [
        "transport-enter",
        "session-enter",
        "initialize",
        "session-exit",
        "transport-exit",
        "transport-enter",
        "session-enter",
        "initialize",
        "call:generate_chart:demo",
        "session-exit",
        "transport-exit",
    ]


async def test_get_mcp_client_uses_stateful_transport_only_for_builtin_stdio(monkeypatch):
    """AgentScope 要求 stdio 有进程生命周期，HTTP 继续保持无状态。"""
    created = []

    def fake_client(**kwargs):
        created.append(kwargs)
        return SimpleNamespace()

    monkeypatch.setattr(mcp_service, "MCPClient", fake_client)

    await mcp_service.get_mcp_client(
        {
            "mcp-server-chart": {
                "transport": "stdio",
                "command": "npx",
                "args": ["-y", "@antv/mcp-server-chart"],
            }
        }
    )
    await mcp_service.get_mcp_client(
        {
            "remote": {
                "transport": "streamable_http",
                "url": "http://mcp.example/mcp",
            }
        }
    )

    assert created[0]["is_stateful"] is True
    assert created[1]["is_stateful"] is False
