"""内置 + 知识库工具纵切 e2e（迁移工单 07）。

真实链路：模型按用户意图发起 list_kbs 工具调用 → agentscope 在
extra_agent_tools 工厂装配的 KB 工具上执行（可见性走 yuxi 权限）→
工具事件经网关协议转换写入 Run 事件流 → 模型汇总回复。
"""

import json
import os
import uuid

import pytest
from sqlalchemy import select

from test.e2e.agentscope_e2e_fixtures import (
    PROVIDER_RESOURCE_ID,
    cleanup_fixture_agents,
    cleanup_test_users,
    open_fixture_http_client,
    seed_test_users,
    upsert_mock_provider,
)
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
                        "model": f"{PROVIDER_RESOURCE_ID}:mock-chat-model",
                        "mcps": [],
                        "tools": [],
                        "skills": [],
                        "subagents": [],
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
    client, headers = await open_fixture_http_client(USER_ID)
    try:
        thread_response = await client.post(
            "/api/chat/thread",
            json={"agent_id": CHATBOT_SLUG, "title": f"kb-{uuid.uuid4().hex[:8]}"},
            headers=headers,
        )
        assert thread_response.status_code == 200, thread_response.text
        thread_id = str(thread_response.json().get("thread_id") or thread_response.json()["id"])
        run_response = await client.post(
            "/api/agent/runs",
            json={
                "query": "请列出知识库",
                "agent_slug": CHATBOT_SLUG,
                "thread_id": thread_id,
                "meta": {"request_id": f"e2e-req-{uuid.uuid4().hex[:8]}"},
            },
            headers=headers,
        )
        assert run_response.status_code == 200, run_response.text
        run_id = str(run_response.json()["run_id"])
        from e2e_helpers import consume_events, wait_for_run

        event_counts = await consume_events(client, headers, run_id)
        assert event_counts.get("end", 0) == 1, event_counts
        run = await wait_for_run(client, headers, run_id)
        assert run["status"] == "completed", run
    finally:
        await client.aclose()

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


async def test_max_iters_keeps_partial_text_and_failed_terminal(db_session):
    """真实工具轮次达到上限时，保留文本但不得把 Run 误报为成功。"""
    agent = await db_session.scalar(select(Agent).where(Agent.slug == CHATBOT_SLUG))
    assert agent is not None
    context = dict((agent.config_json or {}).get("context") or {})
    agent.config_json = {"context": {**context, "max_execution_steps": 1}}
    await db_session.commit()

    client, headers = await open_fixture_http_client(USER_ID)
    try:
        thread_response = await client.post(
            "/api/chat/thread",
            json={"agent_id": CHATBOT_SLUG, "title": f"kb-limit-{uuid.uuid4().hex[:8]}"},
            headers=headers,
        )
        assert thread_response.status_code == 200, thread_response.text
        thread_id = str(thread_response.json().get("thread_id") or thread_response.json()["id"])
        run_response = await client.post(
            "/api/agent/runs",
            json={
                "query": "请列出知识库",
                "agent_slug": CHATBOT_SLUG,
                "thread_id": thread_id,
                "meta": {"request_id": f"e2e-req-{uuid.uuid4().hex[:8]}"},
            },
            headers=headers,
        )
        assert run_response.status_code == 200, run_response.text
        run_id = str(run_response.json()["run_id"])
        from e2e_helpers import consume_events, wait_for_run

        event_counts = await consume_events(client, headers, run_id)
        assert event_counts.get("end", 0) == 1, event_counts
        result = await wait_for_run(client, headers, run_id)
        assert result["status"] == "failed", result
        assert result["error_type"] == "exceed_max_iters", result
        result_response = await client.get(f"/api/agent/runs/{run_id}/result", headers=headers)
        assert result_response.status_code == 200, result_response.text
        assert result_response.json()["output"], "达到最大迭代时应保留 AgentScope 已产生的最终文本"

        redis_client = await get_async_redis_client()
        try:
            frames = await redis_client.xrange(f"run:events:{run_id}")
            assert frames[-1][1]["event_type"] == "end"
            end_envelope = json.loads(frames[-1][1]["payload"])
            assert end_envelope["payload"]["status"] == "failed"
            assert end_envelope["payload"].get("chunk") is None
        finally:
            await redis_client.delete(f"run:events:{run_id}")
            await close_async_redis_client()
    finally:
        await client.aclose()
