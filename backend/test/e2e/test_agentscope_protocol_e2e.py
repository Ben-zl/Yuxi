"""网关协议转换 e2e（迁移工单 04）。

真实链路验证：一轮 agentscope 对话经网关转换为 yuxi Run 事件信封写入
Redis Stream；断言帧序列与前端消费契约一致，并用 XRANGE(after_seq)
模拟断线重连续传。模型端点为 OpenAI 兼容流式 mock。
"""

import os
import uuid

import pytest
from e2e_helpers import consume_events, wait_for_run
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
CHATBOT_SLUG = "e2e-proto-chatbot"
USER_ID = "e2e-proto"

pytestmark = pytest.mark.e2e


@pytest.fixture
async def db_session():
    pg_manager.initialize()
    await pg_manager.ensure_business_schema()
    async with pg_manager.get_async_session_context() as session:
        await cleanup_fixture_agents(session, CHATBOT_SLUG)
        await seed_test_users(session, USER_ID)
        session.add_all(
            [
                Agent(
                    slug=CHATBOT_SLUG,
                    name="协议转换智能体",
                    backend_id="ChatbotAgent",
                    config_json={
                        "context": {
                            "model": f"{PROVIDER_RESOURCE_ID}:mock-chat-model",
                            "mcps": [],
                            "tools": [],
                            "skills": [],
                            "subagents": [],
                            "system_prompt": "你是协议测试助手。",
                        }
                    },
                    share_config={},
                ),
            ]
        )
        await upsert_mock_provider(session)
        await session.commit()
        yield session
        await cleanup_fixture_agents(session, CHATBOT_SLUG)
        await cleanup_test_users(session, USER_ID)
    await close_async_redis_client()
    await pg_manager.close()
    pg_manager._initialized = False


async def test_round_written_as_run_events_with_reconnect(db_session):
    uid = USER_ID
    request_id = f"e2e-req-{uuid.uuid4().hex[:8]}"
    client, headers = await open_fixture_http_client(uid)
    try:
        thread_response = await client.post(
            "/api/chat/thread",
            json={"agent_id": CHATBOT_SLUG, "title": f"protocol-{uuid.uuid4().hex[:8]}"},
            headers=headers,
        )
        assert thread_response.status_code == 200, thread_response.text
        thread_id = str(thread_response.json().get("thread_id") or thread_response.json().get("id"))
        run_response = await client.post(
            "/api/agent/runs",
            json={
                "query": "打个招呼",
                "agent_slug": CHATBOT_SLUG,
                "thread_id": thread_id,
                "meta": {"request_id": request_id},
            },
            headers=headers,
        )
        assert run_response.status_code == 200, run_response.text
        run_id = str(run_response.json()["run_id"])
        event_counts = await consume_events(client, headers, run_id)
        assert event_counts.get("messages", 0) > 0, event_counts
        assert event_counts.get("end", 0) == 1, event_counts
        run = await wait_for_run(client, headers, run_id)
        assert run["status"] == "completed", run
        result_response = await client.get(f"/api/agent/runs/{run_id}/result", headers=headers)
        assert result_response.status_code == 200, result_response.text
        assert result_response.json()["output"].startswith("你好，我是 e2e mock 模型")

        redis_client = await get_async_redis_client()
        stream_key = f"run:events:{run_id}"
        try:
            frames = await redis_client.xrange(stream_key)
            assert frames, "run 事件流不应为空"
            event_names = [entry["event_type"] for _, entry in frames]
            assert event_names[0] == "metadata"
            assert "messages" in event_names
            assert event_names[-1] == "end"

            import json

            end_envelope = json.loads(frames[-1][1]["payload"])
            assert end_envelope["schema_version"] == 1
            assert end_envelope["run_id"] == run_id
            end_payload = end_envelope["payload"]
            assert end_payload["status"] == "completed"
            assert end_payload["chunk"]["status"] == "finished"

            all_chunks = []
            for _, entry in frames:
                if entry["event_type"] == "messages":
                    all_chunks.extend(json.loads(entry["payload"])["payload"]["items"])
            statuses = [c["status"] for c in all_chunks]
            assert statuses[0] == "init"
            assert all_chunks[0]["msg"]["content"] == "打个招呼"
            assert "loading" in statuses
            joined_text = "".join(
                c["msg"].get("content", "") for c in all_chunks if c["status"] == "loading" and c["msg"].get("content")
            )
            assert joined_text.startswith("你好，我是 e2e mock 模型")

            midpoint_seq = frames[len(frames) // 2][0]
            tail = await redis_client.xrange(stream_key, min=f"({midpoint_seq}", max="+")
            assert tail, "断线重连应能续读到剩余帧"
            assert tail[-1][1]["event_type"] == "end"
        finally:
            await redis_client.delete(stream_key)
            await close_async_redis_client()
    finally:
        await client.aclose()
