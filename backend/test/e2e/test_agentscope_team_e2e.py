"""Team 子智能体 e2e（迁移工单 11）。

真实链路：管理员配置的子智能体（每轮动态投影为 worker 模板）→ 主智能体
TeamCreate 组队 → AgentCreate 以自定义模板创建 worker → worker 以模板
系统提示独立会话执行 → 结果回流 leader 汇总。
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
from yuxi.agentscope.runner import collect_chat_round, ensure_thread_session
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import Agent
from yuxi.storage.redis.manager import close_async_redis_client

AGENTSCOPE_BASE_URL = os.getenv("AGENTSCOPE_BASE_URL", "http://agentscope:8100")
CHATBOT_SLUG = "e2e-team-leader"
SUBAGENT_SLUG = "e2e-team-sub"
TEAM_DONE_TEXT = "团队任务完成"
USER_ID = "e2e-team"

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
                name="团队主智能体",
                backend_id="ChatbotAgent",
                config_json={
                    "context": {
                        "model": f"{PROVIDER_ID}:mock-chat-model",
                        "mcps": [],
                        "system_prompt": "你是团队主智能体。",
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


async def test_team_choreography_with_custom_template(db_session):
    uid = USER_ID
    thread_id = f"e2e-thread-{uuid.uuid4().hex[:12]}"
    client = AgentScopeServiceClient(AGENTSCOPE_BASE_URL)

    mapping = await ensure_thread_session(db_session, client, uid=uid, thread_id=thread_id, agent_slug=CHATBOT_SLUG)
    result = await collect_chat_round(
        client,
        uid=uid,
        agent_id=mapping.agentscope_agent_id,
        session_id=mapping.agentscope_session_id,
        text="请组建团队完成示例任务",
        read_timeout=300.0,
    )
    assert result.parked is None
    assert result.text.startswith(TEAM_DONE_TEXT)

    # TeamDelete 只执行一次并正常结束；保留模式下 worker runtime 仍存在。
    tool_starts = [ev for ev in result.events if str(ev.get("type", "")).upper() == "TOOL_CALL_START"]
    called = {ev.get("tool_call_name") for ev in tool_starts}
    assert {"TeamCreate", "AgentCreate", "TeamDelete"} <= called, called
    delete_starts = [ev for ev in tool_starts if ev.get("tool_call_name") == "TeamDelete"]
    assert len(delete_starts) == 1
    delete_call_id = delete_starts[0]["tool_call_id"]
    delete_ends = [
        ev
        for ev in result.events
        if str(ev.get("type", "")).upper() == "TOOL_RESULT_END" and ev.get("tool_call_id") == delete_call_id
    ]
    assert len(delete_ends) == 1

    # worker 以独立 team 会话落地，系统提示来自 yuxi 模板投影
    import asyncpg

    conn = await asyncpg.connect(
        os.getenv(
            "AGENTSCOPE_DATABASE_URL",
            "postgresql+asyncpg://postgres:postgres@postgres:5432/agentscope",
        ).replace("+asyncpg", "")
    )
    try:
        worker_agents = await conn.fetch("SELECT payload FROM agents WHERE source = 'team'")
        assert worker_agents, "应有 source=team 的 worker agent 记录"
        prompts = [json.loads(row["payload"])["data"]["system_prompt"] for row in worker_agents]
        assert any("e2e 团队子智能体模板提示" in p for p in prompts), prompts

        worker_sessions = await conn.fetchval(
            "SELECT count(*) FROM sessions s JOIN agents a ON s.agent_id = a.id WHERE a.source = 'team'"
        )
        assert worker_sessions >= 1, "worker 应有独立会话"
    finally:
        await conn.close()
