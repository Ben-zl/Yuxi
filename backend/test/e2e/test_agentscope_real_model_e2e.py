"""真实付费模型 e2e（迁移门禁 ③）。

使用 MiniMax-M3（Anthropic 兼容端点）验证全链路：统一投影
（anthropic_credential + base_url）→ credential/session → 流式事件
（真实思考/文本增量）→ 真实 token 用量归集。
未配置 MINIMAX_API_KEY 时跳过（密钥仅经环境变量注入，不入库不入测试）。
"""

import os
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from e2e_helpers import cancel_run, consume_events, wait_for_run
from test.e2e.agentscope_e2e_fixtures import cleanup_fixture_agents
from test.live_api_cleanup import cleanup_static_test_user_resources
from yuxi.storage.postgres.manager import pg_manager
from yuxi.repositories.agent_repository import DEFAULT_SHARE_CONFIG
from yuxi.storage.postgres.models_business import (
    Agent,
    AgentScopeThreadSession,
    Conversation,
    ConversationStats,
    ModelProvider,
    OperationLog,
    User,
)
from yuxi.storage.redis.manager import close_async_redis_client
from yuxi.utils.auth_utils import AuthUtils

AGENTSCOPE_BASE_URL = os.getenv("AGENTSCOPE_BASE_URL", "http://agentscope:8100")
CHATBOT_SLUG = "e2e-realmodel-chatbot"
PROVIDER_ID = "e2e-minimax-real"
PROVIDER_RESOURCE_ID = "e2e-minimax-real-resource"
MODEL_SPEC = f"{PROVIDER_RESOURCE_ID}:MiniMax-M3"
REAL_UID = "e2e-realmodel"

pytestmark = pytest.mark.e2e

requires_minimax_key = pytest.mark.skipif(
    not os.getenv("MINIMAX_API_KEY"), reason="未配置 MINIMAX_API_KEY，跳过真实模型验证"
)


async def _cleanup_real_model_conversations(session) -> None:
    """按现有测试清理器删除该专用用户的完整 Conversation 事实链。"""
    await session.commit()
    await cleanup_static_test_user_resources(REAL_UID)


@pytest.fixture
async def db_session():
    pg_manager.initialize()
    await pg_manager.ensure_business_schema()
    async with pg_manager.get_async_session_context() as session:
        await cleanup_fixture_agents(session, CHATBOT_SLUG)
        await session.execute(delete(AgentScopeThreadSession).where(AgentScopeThreadSession.uid == REAL_UID))
        await session.execute(
            delete(OperationLog).where(OperationLog.user_id.in_(select(User.id).where(User.uid == REAL_UID)))
        )
        await session.commit()
        await _cleanup_real_model_conversations(session)
        conversation_ids = select(Conversation.id).where(Conversation.uid == REAL_UID)
        await session.execute(delete(ConversationStats).where(ConversationStats.conversation_id.in_(conversation_ids)))
        await session.execute(delete(ModelProvider).where(ModelProvider.provider_id == PROVIDER_ID))
        await session.execute(delete(User).where(User.uid == REAL_UID))
        session.add_all(
            [
                User(
                    uid=REAL_UID,
                    username=REAL_UID,
                    password_hash=AuthUtils.hash_password("test-only"),
                    role="superadmin",
                    department_id=1,
                ),
                ModelProvider(
                    resource_id=PROVIDER_RESOURCE_ID,
                    provider_id=PROVIDER_ID,
                    display_name="MiniMax real model e2e",
                    provider_type="anthropic",
                    base_url="https://api.minimaxi.com/anthropic",
                    api_key_env="MINIMAX_API_KEY",
                    capabilities=["chat"],
                    enabled_models=[
                        {
                            "id": "MiniMax-M3",
                            "type": "chat",
                            "request_body_overrides": {
                                "enable_thinking": True,
                                "thinking_budget": 1024,
                            },
                        }
                    ],
                    is_enabled=True,
                ),
                Agent(
                    slug=CHATBOT_SLUG,
                    name="真实模型测试智能体",
                    backend_id="ChatbotAgent",
                    config_json={
                        "context": {
                            "model": MODEL_SPEC,
                            "tools": [],
                            "skills": [],
                            "mcps": [],
                            "subagents": [],
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
        await session.execute(
            delete(OperationLog).where(OperationLog.user_id.in_(select(User.id).where(User.uid == REAL_UID)))
        )
        await session.commit()
        await _cleanup_real_model_conversations(session)
        conversation_ids = select(Conversation.id).where(Conversation.uid == REAL_UID)
        await session.execute(delete(ConversationStats).where(ConversationStats.conversation_id.in_(conversation_ids)))
        await session.execute(delete(ModelProvider).where(ModelProvider.provider_id == PROVIDER_ID))
        await session.execute(delete(User).where(User.uid == REAL_UID))
        await session.commit()
    await close_async_redis_client()
    await pg_manager.close()
    pg_manager._initialized = False


@pytest_asyncio.fixture
async def real_model_headers(db_session, e2e_client):
    """使用本用例创建的专用 superadmin 登录正式 HTTP 入口。"""
    response = await e2e_client.post(
        "/api/auth/token",
        data={"username": REAL_UID, "password": "test-only"},
    )
    assert response.status_code == 200, response.text
    access_token = response.json().get("access_token")
    assert access_token, response.text
    return {"Authorization": f"Bearer {access_token}"}


@requires_minimax_key
async def test_real_model_roundtrip_with_usage(db_session, e2e_client, real_model_headers):
    run_id = None
    thread_id = None
    request_id = f"e2e-req-{uuid.uuid4().hex[:8]}"
    run_completed = False
    try:
        agent = await db_session.scalar(select(Agent).where(Agent.slug == CHATBOT_SLUG))
        assert agent is not None
        assert (agent.config_json or {}).get("context", {}).get("tools") == []
        thread_response = await e2e_client.post(
            "/api/chat/thread",
            json={
                "agent_id": CHATBOT_SLUG,
                "title": f"MiniMax real model e2e {uuid.uuid4().hex[:8]}",
                "metadata": {"_yuxi_e2e": True, "test": "agentscope-real-model"},
            },
            headers=real_model_headers,
        )
        assert thread_response.status_code == 200, thread_response.text
        thread_id = str(thread_response.json().get("thread_id") or thread_response.json().get("id"))
        assert thread_id, thread_response.text
        api_agent_response = await e2e_client.get(
            f"/api/agent/{CHATBOT_SLUG}",
            headers=real_model_headers,
        )
        assert api_agent_response.status_code == 200, api_agent_response.text
        api_agent_context = (api_agent_response.json().get("agent") or {}).get("config_json", {}).get("context", {})
        assert api_agent_context.get("tools") == [], api_agent_response.text

        run_response = await e2e_client.post(
            "/api/agent/runs",
            json={
                "query": "用一句话介绍你自己",
                "agent_slug": CHATBOT_SLUG,
                "thread_id": thread_id,
                "meta": {"request_id": request_id, "model_spec": MODEL_SPEC},
            },
            headers=real_model_headers,
        )
        assert run_response.status_code == 200, run_response.text
        run_id = str(run_response.json().get("run_id"))
        assert run_id, run_response.text

        event_counts = await consume_events(e2e_client, real_model_headers, run_id)
        assert event_counts.get("messages", 0) > 0, event_counts
        assert event_counts.get("end", 0) == 1, event_counts

        run_payload = await wait_for_run(e2e_client, real_model_headers, run_id)
        assert run_payload.get("status") == "completed", run_payload
        assert run_payload.get("request_id") == request_id, run_payload

        result_response = await e2e_client.get(f"/api/agent/runs/{run_id}/result", headers=real_model_headers)
        assert result_response.status_code == 200, result_response.text
        result = result_response.json()
        assert result.get("status") == "completed", result
        assert len(str(result.get("output") or "").strip()) > 0, result
        assert result.get("token_usage", {}).get("total_tokens", 0) > 0, result
        run_completed = True
    finally:
        if not run_completed:
            await cancel_run(e2e_client, real_model_headers, run_id)
        if thread_id:
            response = await e2e_client.delete(f"/api/chat/thread/{thread_id}", headers=real_model_headers)
            assert response.status_code in {200, 404}, response.text
