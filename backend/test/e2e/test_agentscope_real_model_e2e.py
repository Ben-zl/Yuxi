"""真实付费模型 e2e（迁移门禁 ③）。

使用 MiniMax-M3（Anthropic 兼容端点）验证全链路：统一投影
（anthropic_credential + base_url）→ credential/session → 流式事件
（真实思考/文本增量）→ 真实 token 用量归集。
未配置 MINIMAX_API_KEY 时跳过（密钥仅经环境变量注入，不入库不入测试）。
"""

import os
import uuid

import pytest
from sqlalchemy import delete

from test.e2e.agentscope_e2e_fixtures import cleanup_fixture_agents
from yuxi.agentscope.client import AgentScopeServiceClient
from yuxi.agentscope.gateway import stream_round_to_run_events
from yuxi.agentscope.runner import ensure_thread_session
from yuxi.storage.postgres.manager import pg_manager
from yuxi.repositories.agent_repository import DEFAULT_SHARE_CONFIG
from yuxi.storage.postgres.models_business import (
    Agent,
    AgentScopeThreadSession,
    ModelProvider,
    User,
)
from yuxi.storage.redis.manager import close_async_redis_client

AGENTSCOPE_BASE_URL = os.getenv("AGENTSCOPE_BASE_URL", "http://agentscope:8100")
CHATBOT_SLUG = "e2e-realmodel-chatbot"
PROVIDER_ID = "e2e-minimax-real"
MODEL_SPEC = f"{PROVIDER_ID}:MiniMax-M3"
REAL_UID = "e2e-realmodel"

pytestmark = pytest.mark.e2e

requires_minimax_key = pytest.mark.skipif(
    not os.getenv("MINIMAX_API_KEY"), reason="未配置 MINIMAX_API_KEY，跳过真实模型验证"
)


@pytest.fixture
async def db_session():
    pg_manager.initialize()
    await pg_manager.ensure_business_schema()
    async with pg_manager.get_async_session_context() as session:
        await cleanup_fixture_agents(session, CHATBOT_SLUG)
        await session.execute(delete(AgentScopeThreadSession).where(AgentScopeThreadSession.uid == REAL_UID))
        await session.execute(delete(ModelProvider).where(ModelProvider.provider_id == PROVIDER_ID))
        await session.execute(delete(User).where(User.uid == REAL_UID))
        session.add_all(
            [
                User(
                    uid=REAL_UID,
                    username=REAL_UID,
                    password_hash="test-only",
                    role="superadmin",
                ),
                ModelProvider(
                    provider_id=PROVIDER_ID,
                    display_name="MiniMax real model e2e",
                    provider_type="anthropic",
                    base_url="https://api.minimaxi.com/anthropic",
                    api_key_env="MINIMAX_API_KEY",
                    capabilities=["chat"],
                    enabled_models=[{"id": "MiniMax-M3", "type": "chat"}],
                    is_enabled=True,
                ),
                Agent(
                    slug=CHATBOT_SLUG,
                    name="真实模型测试智能体",
                    backend_id="ChatbotAgent",
                    config_json={
                        "context": {
                            "model": MODEL_SPEC,
                            "mcps": [],
                            "system_prompt": "你是真实模型验证助手，请用一句话回答。",
                        }
                    },
                    share_config=DEFAULT_SHARE_CONFIG,
                ),
            ]
        )
        await session.commit()
        yield session
        await session.execute(delete(AgentScopeThreadSession).where(AgentScopeThreadSession.uid == REAL_UID))
        await cleanup_fixture_agents(session, CHATBOT_SLUG)
        await session.execute(delete(ModelProvider).where(ModelProvider.provider_id == PROVIDER_ID))
        await session.execute(delete(User).where(User.uid == REAL_UID))
        await session.commit()
    await close_async_redis_client()
    await pg_manager.close()
    pg_manager._initialized = False


@requires_minimax_key
async def test_real_model_roundtrip_with_usage(db_session):
    uid = REAL_UID
    run_id = f"e2e-run-{uuid.uuid4().hex[:12]}"
    request_id = f"e2e-req-{uuid.uuid4().hex[:8]}"
    thread_id = f"e2e-thread-{uuid.uuid4().hex[:12]}"

    client = AgentScopeServiceClient(AGENTSCOPE_BASE_URL)
    mapping = None
    try:
        mapping = await ensure_thread_session(db_session, client, uid=uid, thread_id=thread_id, agent_slug=CHATBOT_SLUG)
        assert mapping.model_spec == MODEL_SPEC

        result = await stream_round_to_run_events(
            client,
            uid=uid,
            agent_id=mapping.agentscope_agent_id,
            session_id=mapping.agentscope_session_id,
            text="用一句话介绍你自己",
            run_id=run_id,
            request_id=request_id,
            thread_id=thread_id,
            read_timeout=180.0,
        )

        assert result.run_status == "completed"
        assert len(result.text.strip()) > 0, "真实模型应返回非空回复"
        assert result.usage and result.usage["total_tokens"] > 0, result.usage

        messages = await client.list_messages(uid, mapping.agentscope_agent_id, mapping.agentscope_session_id)
        assistant_texts = [
            block.get("text", "")
            for msg in messages
            for block in msg.get("content", [])
            if block.get("type") == "text" and msg.get("role") == "assistant"
        ]
        assert any(text.strip() for text in assistant_texts)
    finally:
        if mapping is not None:
            await client.delete_session(uid, mapping.agentscope_agent_id, mapping.agentscope_session_id)
            await client.delete_agent(uid, mapping.agentscope_agent_id)
            await client.delete_credential(uid, mapping.agentscope_credential_id)
