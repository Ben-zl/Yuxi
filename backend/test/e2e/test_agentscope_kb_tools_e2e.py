"""内置 + 知识库工具纵切 e2e（迁移工单 07）。

真实链路：模型按用户意图发起 list_kbs 工具调用 → agentscope 在
extra_agent_tools 工厂装配的 KB 工具上执行（可见性走 yuxi 权限）→
工具事件经网关协议转换写入 Run 事件流 → 模型汇总回复。
"""

import json
import os
import uuid

import pytest

from test.e2e.agentscope_e2e_fixtures import (
    PROVIDER_ID,
    cleanup_fixture_agents,
    cleanup_test_users,
    seed_test_users,
    upsert_mock_provider,
)
from yuxi.agentscope.client import AgentScopeServiceClient
from yuxi.agentscope.gateway import stream_round_to_run_events
from yuxi.agentscope.runner import ensure_thread_session
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import Agent
from yuxi.storage.redis.manager import close_async_redis_client, get_async_redis_client

AGENTSCOPE_BASE_URL = os.getenv("AGENTSCOPE_BASE_URL", "http://agentscope:8100")
CHATBOT_SLUG = "e2e-kbtools-chatbot"
USER_ID = "e2e-kbtools"

pytestmark = pytest.mark.e2e


@pytest.fixture
async def db_session():
    pg_manager.initialize()
    await pg_manager.ensure_business_schema()
    async with pg_manager.get_async_session_context() as session:
        await cleanup_fixture_agents(session, CHATBOT_SLUG)
        await seed_test_users(session, USER_ID)
        session.add(
            Agent(
                slug=CHATBOT_SLUG,
                name="工具测试智能体",
                backend_id="ChatbotAgent",
                config_json={
                    "context": {
                        "model": f"{PROVIDER_ID}:mock-chat-model",
                        "mcps": [],
                        "system_prompt": "你是工具测试助手。",
                    }
                },
                share_config={},
            )
        )
        await upsert_mock_provider(session)
        yield session
        await cleanup_fixture_agents(session, CHATBOT_SLUG)
        await cleanup_test_users(session, USER_ID)
    await close_async_redis_client()
    await pg_manager.close()
    pg_manager._initialized = False


async def test_kb_tool_round_executes_and_streams(db_session):
    uid = USER_ID
    run_id = f"e2e-run-{uuid.uuid4().hex[:12]}"
    request_id = f"e2e-req-{uuid.uuid4().hex[:8]}"
    thread_id = f"e2e-thread-{uuid.uuid4().hex[:12]}"

    client = AgentScopeServiceClient(AGENTSCOPE_BASE_URL)
    mapping = await ensure_thread_session(db_session, client, uid=uid, thread_id=thread_id, agent_slug=CHATBOT_SLUG)
    result = await stream_round_to_run_events(
        client,
        uid=uid,
        agent_id=mapping.agentscope_agent_id,
        session_id=mapping.agentscope_session_id,
        text="请列出知识库",
        run_id=run_id,
        request_id=request_id,
        thread_id=thread_id,
        read_timeout=300.0,
    )
    assert result.run_status == "completed"

    # 工具调用在事件流中呈现（TOOL_CALL/TOOL_RESULT）
    redis_client = await get_async_redis_client()
    stream_key = f"run:events:{run_id}"
    try:
        frames = await redis_client.xrange(stream_key)
        all_chunks = []
        for _, entry in frames:
            if entry["event_type"] == "messages":
                all_chunks.extend(json.loads(entry["payload"])["payload"]["items"])

        tool_call_chunks = [c for c in all_chunks if c["status"] == "loading" and c["msg"].get("tool_call_chunks")]
        assert tool_call_chunks, "应有工具调用增量 chunk"
        assert any(frag["name"] == "list_kbs" for c in tool_call_chunks for frag in c["msg"]["tool_call_chunks"])

        tool_finished = [c for c in all_chunks if c["status"] == "stream_event"]
        assert tool_finished, "应有工具完成 stream_event chunk"
        assert tool_finished[0]["event"]["data"]["event"] == "tool-finished"
    finally:
        await redis_client.delete(stream_key)
        await close_async_redis_client()
