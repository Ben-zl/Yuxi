"""Team 子智能体 e2e（迁移工单 11）。

真实链路：管理员配置的子智能体（每轮动态投影为 worker 模板）→ 主智能体
TeamCreate 组队 → AgentCreate 以自定义模板创建 worker → worker 以模板
系统提示独立会话执行 → 结果回流 leader 汇总。
"""

import json
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
        await cleanup_fixture_agents(session, CHATBOT_SLUG, SUBAGENT_SLUG)
        await seed_test_users(session, USER_ID)
        session.add(
            Agent(
                slug=SUBAGENT_SLUG,
                name="团队子智能体",
                backend_id="SubAgentBackend",
                is_subagent=True,
                config_json={
                    "context": {
                        "model": f"{PROVIDER_RESOURCE_ID}:mock-chat-model",
                        "tools": [],
                        "skills": [],
                        "subagents": [],
                        "system_prompt": "e2e 团队子智能体模板提示",
                    }
                },
                share_config={},
            )
        )
        session.add(
            Agent(
                slug=CHATBOT_SLUG,
                name="团队主智能体",
                backend_id="ChatbotAgent",
                config_json={
                    "context": {
                        "model": f"{PROVIDER_RESOURCE_ID}:mock-chat-model",
                        "mcps": [],
                        "tools": [],
                        "skills": [],
                        "subagents": [SUBAGENT_SLUG],
                        "system_prompt": "你是团队主智能体。",
                    }
                },
                share_config={},
            )
        )
        await upsert_mock_provider(session)
        yield session
        await cleanup_fixture_agents(session, CHATBOT_SLUG, SUBAGENT_SLUG)
        await cleanup_test_users(session, USER_ID)
    await close_async_redis_client()
    await pg_manager.close()
    pg_manager._initialized = False


async def test_team_choreography_with_custom_template(db_session):
    uid = USER_ID
    client, headers = await open_fixture_http_client(uid)
    try:
        thread_response = await client.post(
            "/api/chat/thread",
            json={"agent_id": CHATBOT_SLUG, "title": f"team-{uuid.uuid4().hex[:8]}"},
            headers=headers,
        )
        assert thread_response.status_code == 200, thread_response.text
        thread_id = str(thread_response.json().get("thread_id") or thread_response.json().get("id"))
        run_response = await client.post(
            "/api/agent/runs",
            json={
                "query": "请组建团队完成示例任务",
                "agent_slug": CHATBOT_SLUG,
                "thread_id": thread_id,
                "tool_approval_mode": "always_trust",
                "meta": {"request_id": f"team-{uuid.uuid4().hex[:8]}"},
            },
            headers=headers,
        )
        assert run_response.status_code == 200, run_response.text
        run_id = str(run_response.json()["run_id"])
        event_counts = await consume_events(client, headers, run_id)
        assert event_counts.get("end", 0) == 1, event_counts
        run = await wait_for_run(client, headers, run_id)
        assert run["status"] == "completed", run
        result_response = await client.get(f"/api/agent/runs/{run_id}/result", headers=headers)
        assert result_response.status_code == 200, result_response.text
        result = result_response.json()
        assert str(result.get("output") or "").startswith(TEAM_DONE_TEXT), result
    finally:
        await client.aclose()

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
