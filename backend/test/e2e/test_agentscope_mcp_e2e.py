"""MCP 接入 e2e（迁移工单 08）。

真实链路：yuxi 的 MCP 服务器配置经投影绑定到会话 workspace →
agentscope MCPClient 真实连接 mock MCP 服务器（streamable-http）→
模型发起 MCP 工具调用 → 经 MCP 协议执行 → 工具事件进入 Run 事件流。
"""

import json
import os
import uuid

import pytest
from sqlalchemy import delete

from test.e2e.agentscope_e2e_fixtures import (
    PROVIDER_ID,
    cleanup_fixture_agents,
    upsert_mock_provider,
)
from yuxi.agentscope.client import AgentScopeServiceClient
from yuxi.agentscope.gateway import stream_round_to_run_events
from yuxi.agentscope.runner import ensure_thread_session
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import Agent, MCPServer
from yuxi.storage.redis.manager import close_async_redis_client, get_async_redis_client

AGENTSCOPE_BASE_URL = os.getenv("AGENTSCOPE_BASE_URL", "http://agentscope:8100")
CHATBOT_SLUG = "e2e-mcp-chatbot"
MCP_SLUG = "e2e-echo-mcp"
MCP_TOOL_NAME = "mcp__e2e-echo-mcp__echo"

pytestmark = pytest.mark.e2e


@pytest.fixture
async def db_session():
    pg_manager.initialize()
    await pg_manager.ensure_business_schema()
    async with pg_manager.get_async_session_context() as session:
        await cleanup_fixture_agents(session, CHATBOT_SLUG)
        await session.execute(
            delete(MCPServer).where(
                MCPServer.slug.in_([MCP_SLUG, "e2e-stdio-mcp", "e2e-disabled-mcp"])
            )
        )
        await session.commit()
        session.add_all(
            [
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
                    enabled=1,
                    disabled_tools=[],
                    created_by="e2e",
                    updated_by="e2e",
                ),
            ]
        )
        await upsert_mock_provider(session)
        yield session
        await cleanup_fixture_agents(session, CHATBOT_SLUG)
        await session.execute(delete(MCPServer).where(MCPServer.slug == MCP_SLUG))
        await session.commit()
    await pg_manager.close()
    pg_manager._initialized = False


async def test_mcp_tool_round_executes_over_streamable_http(db_session):
    uid = "e2e-mcp"
    run_id = f"e2e-run-{uuid.uuid4().hex[:12]}"
    request_id = f"e2e-req-{uuid.uuid4().hex[:8]}"
    thread_id = f"e2e-thread-{uuid.uuid4().hex[:12]}"

    client = AgentScopeServiceClient(AGENTSCOPE_BASE_URL)
    mapping = await ensure_thread_session(
        db_session, client, uid=uid, thread_id=thread_id, agent_slug=CHATBOT_SLUG
    )
    # MCP 工具默认走审批门控（与旧栈审批语义一致，链路验证在工单 10）；此处放行
    await client.set_permission_mode(
        uid, mapping.agentscope_agent_id, mapping.agentscope_session_id, "bypass"
    )
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
    stream_key = f"run:events:{run_id}"
    try:
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
    finally:
        await redis_client.delete(stream_key)
        await close_async_redis_client()


async def test_stdio_and_disabled_mcps_not_bound(db_session):
    """约束保留：stdio 与未启用的 MCP 不投影绑定。"""

    from yuxi.agentscope.tools import bind_thread_mcps

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

        client = AgentScopeServiceClient(AGENTSCOPE_BASE_URL)
        uid = "e2e-mcp-guard"
        thread_id = f"e2e-thread-{uuid.uuid4().hex[:10]}"
        mapping = await ensure_thread_session(
            session, client, uid=uid, thread_id=thread_id, agent_slug=CHATBOT_SLUG
        )
        bound = await bind_thread_mcps(
            session,
            client,
            uid=uid,
            mcp_server_names=["e2e-stdio-mcp", "e2e-disabled-mcp"],
            agent_id=mapping.agentscope_agent_id,
            session_id=mapping.agentscope_session_id,
        )
        assert bound == 0

        await session.execute(delete(MCPServer).where(MCPServer.slug.in_(["e2e-stdio-mcp", "e2e-disabled-mcp"])))
        await session.commit()
        await pg_manager.close()
        pg_manager._initialized = False
