"""网关协议转换 e2e（迁移工单 04）。

真实链路验证：一轮 agentscope 对话经网关转换为 yuxi Run 事件信封写入
Redis Stream；断言帧序列与前端消费契约一致，并用 XRANGE(after_seq)
模拟断线重连续传。模型端点为 OpenAI 兼容流式 mock。
"""

import os
import uuid

import pytest
from sqlalchemy import delete

from yuxi.agentscope.client import AgentScopeServiceClient
from yuxi.agentscope.gateway import stream_round_to_run_events
from yuxi.agentscope.runner import ensure_thread_session
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import Agent, ModelProvider
from yuxi.storage.redis.manager import close_async_redis_client, get_async_redis_client

AGENTSCOPE_BASE_URL = os.getenv("AGENTSCOPE_BASE_URL", "http://agentscope:8100")
CHATBOT_SLUG = "e2e-proto-chatbot"
PROVIDER_ID = "e2e-openai-mock"

pytestmark = pytest.mark.e2e


@pytest.fixture
async def db_session():
    pg_manager.initialize()
    await pg_manager.ensure_business_schema()
    async with pg_manager.get_async_session_context() as session:
        session.add_all(
            [
                Agent(
                    slug=CHATBOT_SLUG,
                    name="协议转换智能体",
                    backend_id="ChatbotAgent",
                    config_json={
                        "context": {
                            "model": f"{PROVIDER_ID}:mock-chat-model",
                            "system_prompt": "你是协议测试助手。",
                        }
                    },
                    share_config={},
                ),
                ModelProvider(
                    provider_id=PROVIDER_ID,
                    display_name="e2e mock provider",
                    provider_type="openai",
                    base_url=os.getenv("OPENAI_MOCK_BASE_URL", "http://openai-mock:8080/v1"),
                    api_key="e2e-mock-key",
                    capabilities=["chat"],
                    enabled_models=[{"id": "mock-chat-model", "type": "chat"}],
                    is_enabled=True,
                ),
            ]
        )
        await session.commit()
        yield session
        await session.execute(delete(Agent).where(Agent.slug == CHATBOT_SLUG))
        await session.execute(
            delete(ModelProvider).where(ModelProvider.provider_id == PROVIDER_ID)
        )
        await session.commit()
    await pg_manager.close()
    pg_manager._initialized = False


async def test_round_written_as_run_events_with_reconnect(db_session):
    uid = "e2e-proto"
    run_id = f"e2e-run-{uuid.uuid4().hex[:12]}"
    request_id = f"e2e-req-{uuid.uuid4().hex[:8]}"
    thread_id = f"e2e-thread-{uuid.uuid4().hex[:12]}"

    client = AgentScopeServiceClient(AGENTSCOPE_BASE_URL)
    mapping = await ensure_thread_session(
        db_session, client, uid=uid, thread_id=thread_id, agent_slug=CHATBOT_SLUG
    )
    result = await stream_round_to_run_events(
        client,
        uid=uid,
        agent_id=mapping.agentscope_agent_id,
        session_id=mapping.agentscope_session_id,
        text="打个招呼",
        run_id=run_id,
        request_id=request_id,
        thread_id=thread_id,
    )
    assert result.run_status == "completed"
    assert result.text.startswith("你好，我是 e2e mock 模型")

    redis_client = await get_async_redis_client()
    stream_key = f"run:events:{run_id}"
    try:
        frames = await redis_client.xrange(stream_key)
        assert frames, "run 事件流不应为空"

        event_names = [entry["event_type"] for _, entry in frames]
        assert event_names[0] == "metadata"
        assert "messages" in event_names
        assert event_names[-1] == "end"

        # 终态帧：end 载荷含 run 状态与 finished chunk
        import json

        end_envelope = json.loads(frames[-1][1]["payload"])
        assert end_envelope["schema_version"] == 1
        assert end_envelope["run_id"] == run_id
        end_payload = end_envelope["payload"]
        assert end_payload["status"] == "completed"
        assert end_payload["chunk"]["status"] == "finished"

        # init chunk 携带用户消息；loading chunk 携带 AIMessageChunk 文本增量
        all_chunks = []
        for _, entry in frames:
            if entry["event_type"] != "messages":
                continue
            all_chunks.extend(json.loads(entry["payload"])["payload"]["items"])
        statuses = [c["status"] for c in all_chunks]
        assert statuses[0] == "init"
        assert all_chunks[0]["msg"]["content"] == "打个招呼"
        assert "loading" in statuses
        joined_text = "".join(
            c["msg"].get("content", "")
            for c in all_chunks
            if c["status"] == "loading" and c["msg"].get("content")
        )
        assert joined_text.startswith("你好，我是 e2e mock 模型")

        # 模拟断线重连：从序列中点之后续读，剩余帧完整可续传
        midpoint_seq = frames[len(frames) // 2][0]
        tail = await redis_client.xrange(stream_key, min=f"({midpoint_seq}", max="+")
        assert tail, "断线重连应能续读到剩余帧"
        assert tail[-1][1]["event_type"] == "end"
    finally:
        await redis_client.delete(stream_key)
        await close_async_redis_client()
