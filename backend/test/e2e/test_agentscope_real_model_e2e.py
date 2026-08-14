"""真实付费模型 e2e（迁移门禁 ③）。

使用 MiniMax-M3（Anthropic 兼容端点）验证全链路：统一投影
（anthropic_credential + base_url）→ credential/session → 流式事件
（真实思考/文本增量）→ 真实 token 用量归集。
未配置 MINIMAX_API_KEY 时跳过（密钥仅经环境变量注入，不入库不入测试）。
"""

import os
import uuid

import pytest

from test.e2e.agentscope_e2e_fixtures import cleanup_fixture_agents
from yuxi.agentscope.client import AgentScopeServiceClient
from yuxi.agentscope.gateway import stream_round_to_run_events
from yuxi.agentscope.runner import ensure_thread_session
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import Agent

AGENTSCOPE_BASE_URL = os.getenv("AGENTSCOPE_BASE_URL", "http://agentscope:8100")
CHATBOT_SLUG = "e2e-realmodel-chatbot"
MODEL_SPEC = "minimax:MiniMax-M3"

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
        session.add(
            Agent(
                slug=CHATBOT_SLUG,
                name="真实模型测试智能体",
                backend_id="ChatbotAgent",
                config_json={
                    "context": {
                        "model": MODEL_SPEC,
                        "system_prompt": "你是真实模型验证助手，请用一句话回答。",
                    }
                },
                share_config={},
            )
        )
        yield session
        await cleanup_fixture_agents(session, CHATBOT_SLUG)
    await pg_manager.close()
    pg_manager._initialized = False


@requires_minimax_key
async def test_real_model_roundtrip_with_usage(db_session):
    uid = "e2e-realmodel"
    run_id = f"e2e-run-{uuid.uuid4().hex[:12]}"
    request_id = f"e2e-req-{uuid.uuid4().hex[:8]}"
    thread_id = f"e2e-thread-{uuid.uuid4().hex[:12]}"

    client = AgentScopeServiceClient(AGENTSCOPE_BASE_URL)
    mapping = await ensure_thread_session(
        db_session, client, uid=uid, thread_id=thread_id, agent_slug=CHATBOT_SLUG
    )
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
    # 真实 token 用量：MiniMax 返回 input/output usage，聚合应大于零
    assert result.usage and result.usage["total_tokens"] > 0, result.usage

    # 思考模型（M3）若输出思考增量，网关应同时聚合 reasoning
    if result.reasoning:
        assert len(result.reasoning) > 0

    # 历史持久化：读回消息包含真实回复
    messages = await client.list_messages(
        uid, mapping.agentscope_agent_id, mapping.agentscope_session_id
    )
    assistant_texts = [
        block.get("text", "")
        for msg in messages
        for block in msg.get("content", [])
        if block.get("type") == "text" and msg.get("role") == "assistant"
    ]
    assert any(text.strip() for text in assistant_texts)
