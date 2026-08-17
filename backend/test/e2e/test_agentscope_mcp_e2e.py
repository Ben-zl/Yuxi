"""MCP 接入 e2e（迁移工单 08）。

真实链路：yuxi 的 MCP 服务器配置在 agentscope 服务进程按轮装配 →
AgentScope MCPClient 真实连接 mock MCP 服务器（streamable-http）→
模型发起 MCP 工具调用 → 经 MCP 协议执行 → 工具事件进入 Run 事件流。
"""

import json
import os
import uuid

import httpx
import pytest
from sqlalchemy import delete, select

from test.e2e.agentscope_e2e_fixtures import (
    PROVIDER_ID,
    cleanup_fixture_agents,
    upsert_mock_provider,
)
from yuxi.agentscope.client import AgentScopeServiceClient
from yuxi.agentscope.gateway import stream_round_to_run_events
from yuxi.agentscope.runner import ensure_thread_session
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import (
    Agent,
    AgentScopeThreadSession,
    MCPServer,
    User,
)
from yuxi.storage.redis.manager import close_async_redis_client, get_async_redis_client

AGENTSCOPE_BASE_URL = os.getenv("AGENTSCOPE_BASE_URL", "http://agentscope:8100")
CHATBOT_SLUG = "e2e-mcp-chatbot"
MCP_SLUG = "e2e-echo-mcp"
MCP_TOOL_NAME = "mcp__e2e-echo-mcp__echo"
USER_ID = "e2e-mcp"
HEADER_SENTINEL = "mcp-header-must-stay-in-service"

pytestmark = pytest.mark.e2e


@pytest.fixture
async def db_session():
    pg_manager.initialize()
    await pg_manager.ensure_business_schema()
    async with pg_manager.get_async_session_context() as session:
        await cleanup_fixture_agents(session, CHATBOT_SLUG)
        await session.execute(delete(AgentScopeThreadSession).where(AgentScopeThreadSession.uid == USER_ID))
        await session.execute(delete(User).where(User.uid == USER_ID))
        await session.execute(
            delete(MCPServer).where(MCPServer.slug.in_([MCP_SLUG, "e2e-stdio-mcp", "e2e-disabled-mcp"]))
        )
        await session.commit()
        session.add_all(
            [
                User(
                    uid=USER_ID,
                    username=USER_ID,
                    password_hash="test-only",
                    role="superadmin",
                ),
                Agent(
                    slug=CHATBOT_SLUG,
                    name="MCP 测试智能体",
                    backend_id="ChatbotAgent",
                    config_json={
                        "context": {
                            "model": f"{PROVIDER_ID}:mock-chat-model",
                            "system_prompt": "你是 MCP 测试助手。",
                            "mcps": [MCP_SLUG],
                        }
                    },
                    share_config={},
                ),
                MCPServer(
                    slug=MCP_SLUG,
                    name="回声 MCP",
                    description="e2e 回声工具",
                    transport="streamable_http",
                    url=os.getenv("MCP_MOCK_URL", "http://mcp-mock:9000/mcp"),
                    headers={"X-E2E-MCP": HEADER_SENTINEL},
                    enabled=1,
                    disabled_tools=[],
                    created_by="e2e",
                    updated_by="e2e",
                ),
            ]
        )
        await upsert_mock_provider(session)
        yield session
        await session.execute(delete(AgentScopeThreadSession).where(AgentScopeThreadSession.uid == USER_ID))
        await cleanup_fixture_agents(session, CHATBOT_SLUG)
        await session.execute(delete(MCPServer).where(MCPServer.slug == MCP_SLUG))
        await session.execute(delete(User).where(User.uid == USER_ID))
        await session.commit()
    await close_async_redis_client()
    await pg_manager.close()
    pg_manager._initialized = False


async def test_mcp_tool_round_executes_over_streamable_http(db_session):
    uid = USER_ID
    run_id = f"e2e-run-{uuid.uuid4().hex[:12]}"
    request_id = f"e2e-req-{uuid.uuid4().hex[:8]}"
    thread_id = f"e2e-thread-{uuid.uuid4().hex[:12]}"

    client = AgentScopeServiceClient(AGENTSCOPE_BASE_URL)
    mapping = None
    redis_client = None
    stream_key = f"run:events:{run_id}"
    try:
        mapping = await ensure_thread_session(db_session, client, uid=uid, thread_id=thread_id, agent_slug=CHATBOT_SLUG)
        # MCP 工具默认走审批门控（与旧栈审批语义一致，链路验证在工单 10）；此处放行
        await client.set_permission_mode(uid, mapping.agentscope_agent_id, mapping.agentscope_session_id, "bypass")
        result = await stream_round_to_run_events(
            client,
            uid=uid,
            agent_id=mapping.agentscope_agent_id,
            session_id=mapping.agentscope_session_id,
            text="请调用回声工具",
            run_id=run_id,
            request_id=request_id,
            thread_id=thread_id,
            read_timeout=300.0,
        )
        assert result.run_status == "completed"

        redis_client = await get_async_redis_client()
        frames = await redis_client.xrange(stream_key)
        all_chunks = []
        for _, entry in frames:
            if entry["event_type"] == "messages":
                all_chunks.extend(json.loads(entry["payload"])["payload"]["items"])

        called_names = [
            frag["name"]
            for c in all_chunks
            if c["status"] == "loading" and c["msg"].get("tool_call_chunks")
            for frag in c["msg"]["tool_call_chunks"]
        ]
        assert MCP_TOOL_NAME in called_names, called_names

        finished = [c for c in all_chunks if c["status"] == "stream_event"]
        assert any("echo:" in c["event"]["data"]["output"]["content"] for c in finished)

        await _assert_mcp_not_projected_to_workspace(uid, mapping)
    finally:
        if redis_client is not None:
            await redis_client.delete(stream_key)
            await close_async_redis_client()
        if mapping is not None:
            await client.delete_session(uid, mapping.agentscope_agent_id, mapping.agentscope_session_id)
            await client.delete_agent(uid, mapping.agentscope_agent_id)
            await client.delete_credential(uid, mapping.agentscope_credential_id)


async def _assert_mcp_not_projected_to_workspace(uid: str, mapping) -> None:
    """确认 MCP 地址与 Header 仅留在服务进程，不进入 Docker workspace。"""
    async with httpx.AsyncClient(
        base_url=AGENTSCOPE_BASE_URL,
        headers={"X-User-ID": uid},
        timeout=60.0,
    ) as http:
        response = await http.get("/sessions/", params={"agent_id": mapping.agentscope_agent_id})
        response.raise_for_status()
    views = response.json()["sessions"]
    session = next(item["session"] for item in views if item["session"]["id"] == mapping.agentscope_session_id)
    workspace_id = session["config"]["workspace_id"]

    transport = httpx.AsyncHTTPTransport(uds="/var/run/docker.sock")
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://docker",
        timeout=60.0,
    ) as docker_api:
        container_name = f"as_ws_{workspace_id}"
        info_response = await docker_api.get(f"/containers/{container_name}/json")
        info_response.raise_for_status()
        info = info_response.json()
        serialized = json.dumps(info, ensure_ascii=True)
        assert os.getenv("MCP_MOCK_URL", "http://mcp-mock:9000/mcp") not in serialized
        assert HEADER_SENTINEL not in serialized
        assert "yuxi-know_app-network" not in info["NetworkSettings"]["Networks"]

        mcp_directory = await docker_api.get(
            f"/containers/{container_name}/archive",
            params={"path": "/workspace/.mcp"},
        )
        assert mcp_directory.status_code == 404


async def test_mcp_config_changes_apply_on_next_tool_build(db_session):
    """真实 MCP 上验证禁用、配置更新、删除均不依赖重启。"""
    from yuxi.agentscope.config_projection import project_runtime
    from yuxi.agentscope.tools import build_mcp_tools

    async def project_tools():
        projection = await project_runtime(db_session, uid=USER_ID, agent_slug=CHATBOT_SLUG)
        return await build_mcp_tools(mcp_servers=projection.mcp_servers)

    server = await db_session.scalar(select(MCPServer).where(MCPServer.slug == MCP_SLUG))
    assert server is not None
    tools = await project_tools()
    assert [tool.name for tool in tools] == [MCP_TOOL_NAME]

    server.disabled_tools = ["echo"]
    await db_session.commit()
    assert await project_tools() == []

    server.disabled_tools = []
    server.url = "http://127.0.0.1:9/mcp"
    await db_session.commit()
    with pytest.raises(Exception):
        await project_tools()

    await db_session.delete(server)
    await db_session.commit()
    with pytest.raises(ValueError, match="不存在、未启用"):
        await project_tools()


async def test_stdio_and_disabled_mcps_not_exposed(db_session):
    """约束保留：stdio 与未启用的 MCP 不暴露为运行工具。"""

    from yuxi.agentscope.config_projection import project_runtime

    async with pg_manager.get_async_session_context() as session:
        session.add_all(
            [
                MCPServer(
                    slug="e2e-stdio-mcp",
                    name="stdio 不允许",
                    transport="stdio",
                    command="echo",
                    enabled=1,
                    disabled_tools=[],
                    created_by="e2e",
                    updated_by="e2e",
                ),
                MCPServer(
                    slug="e2e-disabled-mcp",
                    name="已禁用",
                    transport="streamable_http",
                    url=os.getenv("MCP_MOCK_URL", "http://mcp-mock:9000/mcp"),
                    enabled=0,
                    disabled_tools=[],
                    created_by="e2e",
                    updated_by="e2e",
                ),
            ]
        )
        await session.commit()

        agent = await session.scalar(select(Agent).where(Agent.slug == CHATBOT_SLUG))
        agent.config_json["context"]["mcps"] = ["e2e-stdio-mcp", "e2e-disabled-mcp"]
        await session.commit()
        with pytest.raises(ValueError, match="不存在、未启用"):
            await project_runtime(session, uid=USER_ID, agent_slug=CHATBOT_SLUG)

        await session.execute(delete(MCPServer).where(MCPServer.slug.in_(["e2e-stdio-mcp", "e2e-disabled-mcp"])))
        await session.commit()
        await pg_manager.close()
        pg_manager._initialized = False
