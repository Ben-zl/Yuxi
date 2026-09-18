"""MCP 接入 e2e（迁移工单 08）。

真实链路：yuxi 的 MCP 服务器配置在 agentscope 服务进程按轮装配 →
AgentScope MCPClient 真实连接 mock MCP 服务器（streamable-http）→
模型发起 MCP 工具调用 → 经 MCP 协议执行 → 工具事件进入 Run 事件流。
"""

import json
import os

import httpx
import pytest
from sqlalchemy import delete, select

from e2e_helpers import consume_events, iter_sse, wait_for_run
from test.e2e.agentscope_e2e_fixtures import (
    PROVIDER_RESOURCE_ID,
    cleanup_fixture_agents,
    cleanup_test_users,
    open_fixture_http_client,
    upsert_mock_provider,
)
from test.live_api_cleanup import (
    make_test_conversation_metadata,
    make_test_conversation_title,
    make_test_resource_id,
)
from yuxi.repositories.agent_repository import DEFAULT_SHARE_CONFIG
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import (
    Agent,
    AgentScopeThreadSession,
    MCPServer,
    Message,
    ToolCall,
    User,
)
from yuxi.storage.redis.manager import close_async_redis_client, get_async_redis_client
from yuxi.utils.auth_utils import AuthUtils

AGENTSCOPE_BASE_URL = os.getenv("AGENTSCOPE_BASE_URL", "http://agentscope:8100")
CHATBOT_SLUG = "e2e-mcp-chatbot"
MCP_SLUG = "e2e-echo-mcp"
MCP_RESOURCE_ID = "e2e-echo-mcp-resource"
USER_ID = "e2e-mcp"
HEADER_SENTINEL = "mcp-header-must-stay-in-service"
CALL_ERROR_SENTINEL = "https://user:token@example.test/mcp?api_key=e2e-sensitive-secret"


def _mcp_tool_name(resource_id: str) -> str:
    """按 MCP 全局资源 ID构造运行时命名空间。"""
    return f"mcp__{resource_id}__echo"


pytestmark = pytest.mark.e2e


@pytest.fixture
async def db_session():
    pg_manager.initialize()
    await pg_manager.ensure_business_schema()
    async with pg_manager.get_async_session_context() as session:
        await cleanup_fixture_agents(session, CHATBOT_SLUG)
        await cleanup_test_users(session, USER_ID)
        await session.execute(
            delete(MCPServer).where(MCPServer.slug.in_([MCP_SLUG, "e2e-stdio-mcp", "e2e-disabled-mcp"]))
        )
        await session.commit()
        session.add_all(
            [
                User(
                    uid=USER_ID,
                    username=USER_ID,
                    password_hash=AuthUtils.hash_password("e2e-fixture-password"),
                    role="superadmin",
                ),
                Agent(
                    slug=CHATBOT_SLUG,
                    name="MCP 测试智能体",
                    backend_id="ChatbotAgent",
                    config_json={
                        "context": {
                            "model": f"{PROVIDER_RESOURCE_ID}:mock-chat-model",
                            "system_prompt": "你是 MCP 测试助手。",
                            "mcps": [MCP_RESOURCE_ID],
                            "tools": [],
                            "skills": [],
                            "subagents": [],
                        }
                    },
                    share_config=DEFAULT_SHARE_CONFIG,
                ),
                MCPServer(
                    resource_id=MCP_RESOURCE_ID,
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
        await cleanup_fixture_agents(session, CHATBOT_SLUG)
        await session.execute(delete(MCPServer).where(MCPServer.slug == MCP_SLUG))
        await cleanup_test_users(session, USER_ID)
    await close_async_redis_client()
    await pg_manager.close()
    pg_manager._initialized = False


async def test_mcp_tool_round_executes_over_streamable_http(db_session):
    uid = USER_ID
    request_id = make_test_resource_id("mcp-round")
    client, headers = await open_fixture_http_client(uid)
    try:
        server = await db_session.scalar(select(MCPServer).where(MCPServer.slug == MCP_SLUG))
        assert server is not None
        mcp_tool_name = _mcp_tool_name(str(server.resource_id))
        thread_response = await client.post(
            "/api/chat/thread",
            json={
                "agent_id": CHATBOT_SLUG,
                "title": make_test_conversation_title("mcp-round"),
                "metadata": make_test_conversation_metadata("mcp-round", e2e=True),
            },
            headers=headers,
        )
        assert thread_response.status_code == 200, thread_response.text
        thread_id = str(thread_response.json().get("thread_id") or thread_response.json().get("id"))
        run_response = await client.post(
            "/api/agent/runs",
            json={
                "query": "请调用回声工具",
                "agent_slug": CHATBOT_SLUG,
                "thread_id": thread_id,
                "tool_approval_mode": "always_trust",
                "meta": {"request_id": request_id},
            },
            headers=headers,
        )
        assert run_response.status_code == 200, run_response.text
        run_id = str(run_response.json()["run_id"])
        event_counts = await consume_events(client, headers, run_id)
        assert event_counts.get("end", 0) == 1, event_counts
        run = await wait_for_run(client, headers, run_id)
        assert run["status"] == "completed", run

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
            assert mcp_tool_name in called_names, called_names

            finished = [c for c in all_chunks if c["status"] == "stream_event"]
            assert any("echo:" in c["event"]["data"]["output"]["content"] for c in finished)

            mapping = await db_session.scalar(
                select(AgentScopeThreadSession).where(
                    AgentScopeThreadSession.uid == uid,
                    AgentScopeThreadSession.thread_id == thread_id,
                )
            )
            assert mapping is not None
            await _assert_mcp_not_projected_to_workspace(uid, mapping)
        finally:
            await redis_client.delete(stream_key)
            await close_async_redis_client()
    finally:
        await client.aclose()


async def test_mcp_call_error_is_redacted_from_events_history_and_result(db_session):
    """发现成功后的 MCP 调用错误不得进入 SSE、Redis、历史或结果 API。"""
    client, headers = await open_fixture_http_client(USER_ID)
    redis_client = await get_async_redis_client()
    run_id = None
    try:
        thread_response = await client.post(
            "/api/chat/thread",
            json={
                "agent_id": CHATBOT_SLUG,
                "title": make_test_conversation_title("mcp-error"),
                "metadata": make_test_conversation_metadata("mcp-error", e2e=True),
            },
            headers=headers,
        )
        assert thread_response.status_code == 200, thread_response.text
        thread_id = str(thread_response.json().get("thread_id") or thread_response.json().get("id"))
        run_response = await client.post(
            "/api/agent/runs",
            json={
                "query": "请调用失败回声",
                "agent_slug": CHATBOT_SLUG,
                "thread_id": thread_id,
                "tool_approval_mode": "always_trust",
                "meta": {"request_id": make_test_resource_id("mcp-error")},
            },
            headers=headers,
        )
        assert run_response.status_code == 200, run_response.text
        run_id = str(run_response.json()["run_id"])

        sse_payloads = []
        async for event, payload in iter_sse(client, headers, run_id):
            sse_payloads.append({"event": event, "payload": payload})
            if event == "end" or payload.get("status") in {"completed", "failed", "cancelled", "interrupted"}:
                break

        run = await wait_for_run(client, headers, run_id)
        assert run["status"] == "completed", run
        result_response = await client.get(f"/api/agent/runs/{run_id}/result", headers=headers)
        assert result_response.status_code == 200, result_response.text
        result = result_response.json()

        frames = await redis_client.xrange(f"run:events:{run_id}")
        tool_calls = (
            (
                await db_session.execute(
                    select(ToolCall).join(Message, ToolCall.message_id == Message.id).where(Message.run_id == run_id)
                )
            )
            .scalars()
            .all()
        )
        serialized = json.dumps(
            {
                "sse": sse_payloads,
                "redis": frames,
                "tool_calls": [tool_call.to_dict() for tool_call in tool_calls],
                "result": result,
            },
            ensure_ascii=False,
            default=str,
        )

        assert tool_calls
        assert any(tool_call.status == "error" for tool_call in tool_calls)
        assert "所选 MCP 暂不可用" in serialized
        assert CALL_ERROR_SENTINEL not in serialized
    finally:
        if run_id:
            await redis_client.delete(f"run:events:{run_id}")
        await close_async_redis_client()
        await client.aclose()


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

    serialized = json.dumps(session, ensure_ascii=True)
    assert os.getenv("MCP_MOCK_URL", "http://mcp-mock:9000/mcp") not in serialized
    assert HEADER_SENTINEL not in serialized
    assert workspace_id


async def test_mcp_config_changes_apply_on_next_tool_build(db_session):
    """真实 MCP 上验证禁用、配置更新、删除均不依赖重启。"""
    from yuxi.agentscope.config_projection import project_runtime
    from yuxi.agentscope.tools import build_mcp_tools

    user = await db_session.scalar(select(User).where(User.uid == USER_ID))
    assert user is not None

    async def project_tools():
        projection = await project_runtime(db_session, uid=USER_ID, agent_slug=CHATBOT_SLUG, department_id=1)
        return await build_mcp_tools(mcp_servers=projection.mcp_servers, user=user)

    server = await db_session.scalar(select(MCPServer).where(MCPServer.slug == MCP_SLUG))
    assert server is not None
    mcp_tool_name = _mcp_tool_name(str(server.resource_id))
    tools = await project_tools()
    assert [tool.name for tool in tools] == [mcp_tool_name]

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
            await project_runtime(session, uid=USER_ID, agent_slug=CHATBOT_SLUG, department_id=1)

        await session.execute(delete(MCPServer).where(MCPServer.slug.in_(["e2e-stdio-mcp", "e2e-disabled-mcp"])))
        await session.commit()
        await pg_manager.close()
        pg_manager._initialized = False
