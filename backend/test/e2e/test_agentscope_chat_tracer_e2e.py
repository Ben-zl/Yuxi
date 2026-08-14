"""最小对话纵切 e2e（迁移工单 02/03）。

验证新链路的完整回路：统一配置投影（yuxi 库夹具）→ 映射保障
（Thread↔Session）→ credential/agent/session 创建 → 触发 chat →
事件流收集文本 → 历史持久化 → 用户隔离。模型端点使用 OpenAI 兼容
流式 mock（真实 HTTP 链路，确定性回复），付费模型验证在切换门禁执行。
"""

import os
import uuid

import pytest
from sqlalchemy import delete
from test.e2e.agentscope_e2e_fixtures import PROVIDER_ID, upsert_mock_provider

from yuxi.agentscope.client import AgentScopeServiceClient, AgentScopeServiceError
from yuxi.agentscope.runner import collect_chat_round, ensure_thread_session
from yuxi.repositories.agentscope_thread_sessions import get_thread_session
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import Agent

AGENTSCOPE_BASE_URL = os.getenv("AGENTSCOPE_BASE_URL", "http://agentscope:8100")
CHATBOT_SLUG = "e2e-tracer-chatbot"
MOCK_REPLY_TEXT = "你好，我是 e2e mock 模型"

pytestmark = pytest.mark.e2e


@pytest.fixture
async def db_session():
    pg_manager.initialize()
    await pg_manager.ensure_business_schema()
    async with pg_manager.get_async_session_context() as session:
        await session.execute(delete(Agent).where(Agent.slug.like("e2e-tracer-%")))
        await session.commit()
        # 投影夹具：智能体、子智能体与 OpenAI 兼容供应商（命名空间隔离，用毕清理）
        session.add_all(
            [
                Agent(
                    slug=CHATBOT_SLUG,
                    name="e2e 纵切智能体",
                    backend_id="ChatbotAgent",
                    config_json={
                        "context": {
                            "model": f"{PROVIDER_ID}:mock-chat-model",
                            "system_prompt": "你是 e2e 测试助手。",
                        }
                    },
                    share_config={},
                ),
                Agent(
                    slug="e2e-tracer-subagent",
                    name="e2e 子智能体",
                    description="e2e 子智能体描述",
                    backend_id="SubAgentBackend",
                    is_subagent=True,
                    config_json={"context": {}},
                    share_config={},
                ),
            ]
        )
        await upsert_mock_provider(session)
        await session.commit()
        yield session
        # 智能体行按前缀清理（含夹具创建的子智能体）；共用供应商由 upsert 维护
        await session.execute(delete(Agent).where(Agent.slug.like("e2e-tracer-%")))
        await session.commit()
    # pytest-asyncio 每个测试使用独立事件循环，连接池不能跨循环复用：
    # 用毕释放并重置单例，让下一个测试在自己的循环上重新初始化
    await pg_manager.close()
    pg_manager._initialized = False


@pytest.fixture
async def client():
    yield AgentScopeServiceClient(AGENTSCOPE_BASE_URL)


async def test_minimal_chat_roundtrip(db_session, client):
    uid = "e2e-tracer-a"
    thread_id = f"e2e-tracer-{uuid.uuid4().hex[:12]}"

    mapping = await ensure_thread_session(
        db_session, client, uid=uid, thread_id=thread_id, agent_slug=CHATBOT_SLUG
    )
    assert mapping.agentscope_session_id
    assert mapping.model_spec == f"{PROVIDER_ID}:mock-chat-model"

    result = await collect_chat_round(
        client,
        uid=uid,
        agent_id=mapping.agentscope_agent_id,
        session_id=mapping.agentscope_session_id,
        text="打个招呼",
    )
    assert result.text.startswith(MOCK_REPLY_TEXT)
    types = [ev["type"] for ev in result.events]
    assert "REPLY_START" in types and "TEXT_BLOCK_DELTA" in types
    assert types[-1] == "REPLY_END"

    messages = await client.list_messages(
        uid, mapping.agentscope_agent_id, mapping.agentscope_session_id
    )
    texts = [
        block.get("text", "")
        for msg in messages
        for block in msg.get("content", [])
        if block.get("type") == "text"
    ]
    assert any(text.startswith(MOCK_REPLY_TEXT) for text in texts)

    stored = await get_thread_session(db_session, uid=uid, thread_id=thread_id)
    assert stored is not None and stored.agentscope_session_id == mapping.agentscope_session_id


async def test_thread_session_isolated_per_user(db_session, client):
    uid = "e2e-tracer-a"
    other_uid = "e2e-tracer-b"
    thread_id = f"e2e-tracer-{uuid.uuid4().hex[:12]}"

    mapping = await ensure_thread_session(
        db_session, client, uid=uid, thread_id=thread_id, agent_slug=CHATBOT_SLUG
    )

    # 其他用户读取同一线程映射：yuxi 侧按 uid 隔离，查不到记录
    other_mapping = await get_thread_session(db_session, uid=other_uid, thread_id=thread_id)
    assert other_mapping is None

    # 其他用户访问 agentscope 会话：404 不可见
    with pytest.raises(AgentScopeServiceError):
        await client.list_messages(
            other_uid, mapping.agentscope_agent_id, mapping.agentscope_session_id
        )
