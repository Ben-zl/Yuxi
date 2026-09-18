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


@pytest.mark.asyncio
async def test_team_worker_run_inherits_parent_department(db_session):
    """Team 父子部门固化：超管切到部门 A 后提交团队任务，worker Run 继承 A 的固化部门。

    覆盖计划欠账「Team 父子部门 E2E」：team_lifecycle 创建 worker Run/线程关系时
    必须携带父 Run 的 department_id，而不是读取当时的会话活动部门。
    """
    suffix = uuid.uuid4().hex[:8]
    department_name = f"团队部门-{suffix}"
    client, headers = await open_fixture_http_client(USER_ID)
    department_id = None
    import asyncpg

    from test.e2e.agentscope_e2e_fixtures import FIXTURE_PASSWORD

    conn = await asyncpg.connect(
        os.getenv("POSTGRES_URL", "postgresql+asyncpg://postgres:postgres@postgres:5432/yuxi").replace("+asyncpg", "")
    )
    try:
        # 超管建部门并切换活动部门到 A（超管无需成员关系）
        created = await client.post(
            "/api/departments",
            headers=headers,
            json={
                "name": department_name,
                "description": "team dept e2e",
                "admin_uid": f"teamdepta{suffix}",
                "admin_password": FIXTURE_PASSWORD,
                "admin_phone": None,
            },
        )
        assert created.status_code in (200, 201), created.text
        department_id = created.json()["id"]
        me = await client.get("/api/auth/me", headers=headers)
        assert me.status_code == 200, me.text
        switched = await client.post(
            "/api/auth/department-context",
            headers=headers,
            json={"department_id": department_id, "expected_revision": me.json()["context_revision"]},
        )
        assert switched.status_code == 200, switched.text

        thread_response = await client.post(
            "/api/chat/thread",
            json={"agent_id": CHATBOT_SLUG, "title": f"team-dept-{suffix}"},
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
                "meta": {"request_id": f"team-dept-{uuid.uuid4().hex[:8]}"},
            },
            headers=headers,
        )
        assert run_response.status_code == 200, run_response.text
        run_id = str(run_response.json()["run_id"])
        run = await wait_for_run(client, headers, run_id)
        assert run["status"] == "completed", run

        # 父 Run 与 worker Run 的固化部门都必须是 A
        parent_dept = await conn.fetchval(
            "SELECT department_id FROM agent_runs WHERE id = $1", run_id
        )
        assert parent_dept == department_id, (run_id, parent_dept, department_id)
        worker_rows = await conn.fetch(
            "SELECT r.id, r.department_id FROM agent_runs r "
            "JOIN subagent_threads s ON s.child_thread_id = r.conversation_thread_id "
            "WHERE s.uid = $1 AND r.agent_slug = $2 ORDER BY r.created_at DESC LIMIT 3",
            USER_ID,
            SUBAGENT_SLUG,
        )
        assert worker_rows, "team worker run 未落库"
        for row in worker_rows:
            assert row["department_id"] == department_id, dict(row)
    finally:
        await client.aclose()
        if department_id is not None:
            # 清理顺序：先解引用 Run→线程关系，再删 Run，最后删绑定与线程关系
            await conn.execute(
                "UPDATE agent_runs SET subagent_thread_relation_id = NULL "
                "WHERE subagent_thread_relation_id IN (SELECT id FROM subagent_threads WHERE uid = $1)",
                USER_ID,
            )
            await conn.execute(
                "DELETE FROM tool_calls WHERE message_id IN (SELECT id FROM messages WHERE run_id IN "
                "(SELECT id FROM agent_runs WHERE uid = $1 AND (department_id = $2 OR agent_slug = $3)))",
                USER_ID,
                department_id,
                SUBAGENT_SLUG,
            )
            await conn.execute(
                "DELETE FROM agent_run_requests WHERE uid = $1 AND department_id = $2",
                USER_ID,
                department_id,
            )
            await conn.execute(
                "DELETE FROM messages WHERE run_id IN "
                "(SELECT id FROM agent_runs WHERE uid = $1 AND (department_id = $2 OR agent_slug = $3))",
                USER_ID,
                department_id,
                SUBAGENT_SLUG,
            )
            await conn.execute(
                "DELETE FROM agent_runs WHERE uid = $1 AND (department_id = $2 OR agent_slug = $3)",
                USER_ID,
                department_id,
                SUBAGENT_SLUG,
            )
            await conn.execute(
                "DELETE FROM agentscope_team_worker_bindings WHERE subagent_thread_relation_id IN "
                "(SELECT id FROM subagent_threads WHERE uid = $1)",
                USER_ID,
            )
            await conn.execute("DELETE FROM subagent_threads WHERE uid = $1", USER_ID)
            await conn.execute("DELETE FROM department_memberships WHERE department_id = $1", department_id)
            await conn.execute(
                "UPDATE auth_sessions SET active_department_id = NULL WHERE active_department_id = $1",
                department_id,
            )
            await conn.execute("DELETE FROM departments WHERE id = $1", department_id)
        await conn.close()
