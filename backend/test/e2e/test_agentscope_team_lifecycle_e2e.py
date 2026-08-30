"""从页面 API 入口验证 AgentScope Team 的长期 child 生命周期投影。"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import httpx
import pytest
import pytest_asyncio

from test.e2e.agentscope_e2e_fixtures import PROVIDER_ID, upsert_mock_provider
from test.e2e.test_agent_async_e2e import _create_thread, _delete_agent, _wait_for_run
from test.e2e.test_agent_question_e2e import _consume_until_end
from yuxi.storage.postgres.manager import pg_manager

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e]


@pytest_asyncio.fixture(autouse=True)
async def reset_test_postgres_manager():
    """避免测试数据库连接池跨 pytest event loop 复用。"""
    await pg_manager.reset()
    yield
    await pg_manager.reset()


async def _create_agent(client, headers, *, uid: str, slug: str, subagent: bool, context: dict[str, Any]):
    response = await client.post(
        "/api/agent",
        json={
            "name": f"E2E Team {'子' if subagent else '主'}智能体 {slug[-8:]}",
            "slug": slug,
            "backend_id": "SubAgentBackend" if subagent else "ChatbotAgent",
            "description": "AgentScope Team 生命周期 E2E",
            "config_json": {"context": context},
            "share_config": {
                "version": 2,
                "read_scope": {"access_level": "user", "department_ids": [], "user_uids": [uid]},
                "manage_scope": None,
            },
            "is_subagent": subagent,
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text


async def test_team_worker_projects_child_thread_run_history_and_binding(
    e2e_client: httpx.AsyncClient,
    e2e_headers: dict[str, str],
    e2e_agent_context: dict[str, str],
):
    uid = e2e_agent_context["uid"]
    suffix = uuid.uuid4().hex[:8]
    subagent_slug = f"e2e-team-child-{suffix}"
    leader_slug = f"e2e-team-leader-{suffix}"
    thread_id = None

    pg_manager.initialize()
    async with pg_manager.get_async_session_context() as db:
        await upsert_mock_provider(db)
    refresh = await e2e_client.post(
        "/api/system/model-providers/models/cache/refresh",
        headers=e2e_headers,
    )
    assert refresh.status_code == 200, refresh.text

    base_context = {
        "model": f"{PROVIDER_ID}:mock-chat-model",
        "tools": [],
        "knowledges": [],
        "mcps": [],
        "skills": [],
    }
    try:
        await _create_agent(
            e2e_client,
            e2e_headers,
            uid=uid,
            slug=subagent_slug,
            subagent=True,
            context={**base_context, "subagents": [], "system_prompt": "完成分配的调研并回报 leader。"},
        )
        await _create_agent(
            e2e_client,
            e2e_headers,
            uid=uid,
            slug=leader_slug,
            subagent=False,
            context={
                **base_context,
                "subagents": [subagent_slug],
                "system_prompt": "需要调研时组建团队，收到 worker 回报后再总结。",
            },
        )
        thread_id = await _create_thread(e2e_client, e2e_headers, leader_slug)
        response = await e2e_client.post(
            "/api/agent/runs",
            json={
                "query": "请简短调研 AgentScope 的主要特点，并给出三点结论。",
                "agent_slug": leader_slug,
                "thread_id": thread_id,
                "meta": {"request_id": f"team-lifecycle-{uuid.uuid4()}"},
            },
            headers=e2e_headers,
        )
        assert response.status_code == 200, response.text
        parent_run_id = str(response.json()["run_id"])
        await asyncio.wait_for(
            _consume_until_end(e2e_client, e2e_headers, parent_run_id),
            timeout=120,
        )
        parent_run = await _wait_for_run(e2e_client, e2e_headers, parent_run_id)
        assert parent_run["status"] == "completed", parent_run

        state = await e2e_client.get(
            f"/api/chat/thread/{thread_id}/state",
            headers=e2e_headers,
        )
        assert state.status_code == 200, state.text
        child_runs = (state.json().get("agent_state") or {}).get("subagent_runs") or []
        assert len(child_runs) == 1, state.json()
        child = child_runs[0]
        assert child["subagent_slug"] == subagent_slug
        assert child["status"] == "completed"

        child_state = await e2e_client.get(
            f"/api/chat/thread/{child['child_thread_id']}/state?include_messages=true",
            headers=e2e_headers,
        )
        assert child_state.status_code == 200, child_state.text
        payload = child_state.json()
        assert payload["parent_thread_id"] == thread_id
        assert payload["subagent_run"]["run_id"] == child["run_id"]
        assert payload.get("messages"), payload

        async with pg_manager.get_async_session_context() as db:
            from yuxi.repositories.agentscope_team_workers import AgentScopeTeamWorkerRepository

            binding = await AgentScopeTeamWorkerRepository(db).get_by_child_run(
                uid=uid,
                run_id=child["run_id"],
            )
            assert binding is not None
            assert binding.child_thread_id == child["child_thread_id"]
            assert binding.runtime_active is True

        deleted = await e2e_client.delete(f"/api/chat/thread/{thread_id}", headers=e2e_headers)
        assert deleted.status_code == 200, deleted.text
        async with pg_manager.get_async_session_context() as db:
            from yuxi.repositories.agentscope_team_workers import AgentScopeTeamWorkerRepository

            binding = await AgentScopeTeamWorkerRepository(db).get_by_child_run(
                uid=uid,
                run_id=child["run_id"],
            )
            assert binding is not None
            assert binding.runtime_active is False
        deleted_child = await e2e_client.get(
            f"/api/chat/thread/{child['child_thread_id']}/state",
            headers=e2e_headers,
        )
        assert deleted_child.status_code == 404, deleted_child.text
        thread_id = None
    finally:
        await _delete_agent(e2e_client, e2e_headers, leader_slug)
        await _delete_agent(e2e_client, e2e_headers, subagent_slug)


async def test_two_workers_do_not_create_broadcast_acknowledgement_loop(
    e2e_client: httpx.AsyncClient,
    e2e_headers: dict[str, str],
    e2e_agent_context: dict[str, str],
):
    """两个 worker 均传 to=null 时，只向 leader 各回报一次。"""
    uid = e2e_agent_context["uid"]
    suffix = uuid.uuid4().hex[:8]
    child_slugs = [
        f"e2e-team-loop-a-{suffix}",
        f"e2e-team-loop-b-{suffix}",
    ]
    leader_slug = f"e2e-team-loop-leader-{suffix}"
    thread_id = None

    pg_manager.initialize()
    async with pg_manager.get_async_session_context() as db:
        await upsert_mock_provider(db)
    refresh = await e2e_client.post(
        "/api/system/model-providers/models/cache/refresh",
        headers=e2e_headers,
    )
    assert refresh.status_code == 200, refresh.text

    base_context = {
        "model": f"{PROVIDER_ID}:mock-chat-model",
        "tools": [],
        "knowledges": [],
        "mcps": [],
        "skills": [],
    }
    try:
        for child_slug in child_slugs:
            await _create_agent(
                e2e_client,
                e2e_headers,
                uid=uid,
                slug=child_slug,
                subagent=True,
                context={
                    **base_context,
                    "subagents": [],
                    "system_prompt": "完成分配任务后只回报 leader。",
                },
            )
        await _create_agent(
            e2e_client,
            e2e_headers,
            uid=uid,
            slug=leader_slug,
            subagent=False,
            context={
                **base_context,
                "subagents": child_slugs,
                "system_prompt": "创建两个 worker，收到两份回报后总结。",
            },
        )
        thread_id = await _create_thread(e2e_client, e2e_headers, leader_slug)
        response = await e2e_client.post(
            "/api/agent/runs",
            json={
                "query": "请执行双 worker 回环验证，并汇总两个模块结果。",
                "agent_slug": leader_slug,
                "thread_id": thread_id,
                "meta": {"request_id": f"team-loop-{uuid.uuid4()}"},
            },
            headers=e2e_headers,
        )
        assert response.status_code == 200, response.text
        parent_run_id = str(response.json()["run_id"])
        await asyncio.wait_for(
            _consume_until_end(e2e_client, e2e_headers, parent_run_id),
            timeout=120,
        )
        parent_run = await _wait_for_run(e2e_client, e2e_headers, parent_run_id)
        assert parent_run["status"] == "completed", parent_run

        state = await e2e_client.get(
            f"/api/chat/thread/{thread_id}/state",
            headers=e2e_headers,
        )
        assert state.status_code == 200, state.text
        child_runs = (state.json().get("agent_state") or {}).get("subagent_runs") or []
        assert len(child_runs) == 2, state.json()
        assert {item["subagent_slug"] for item in child_runs} == set(child_slugs)
        assert {item["status"] for item in child_runs} == {"completed"}

        async with pg_manager.get_async_session_context() as db:
            from sqlalchemy import func, select

            from yuxi.storage.postgres.models_business import (
                AgentRun,
                AgentScopeTeamWorkerBinding,
                Message,
                ToolCall,
            )

            bindings = (
                await db.scalars(
                    select(AgentScopeTeamWorkerBinding).where(
                        AgentScopeTeamWorkerBinding.parent_thread_id == thread_id,
                        AgentScopeTeamWorkerBinding.uid == uid,
                    )
                )
            ).all()
            assert len(bindings) == 2
            run_count = await db.scalar(
                select(func.count(AgentRun.id)).where(
                    AgentRun.conversation_thread_id.in_([binding.child_thread_id for binding in bindings])
                )
            )
            assert run_count == 2
            calls = (
                await db.scalars(
                    select(ToolCall)
                    .join(Message, Message.id == ToolCall.message_id)
                    .join(AgentRun, AgentRun.id == Message.run_id)
                    .where(
                        AgentRun.conversation_thread_id.in_([binding.child_thread_id for binding in bindings]),
                        ToolCall.tool_name == "TeamSay",
                    )
                )
            ).all()
            assert len(calls) == 2
            assert all((call.tool_input or {}).get("to") is None for call in calls)

        deleted = await e2e_client.delete(
            f"/api/chat/thread/{thread_id}",
            headers=e2e_headers,
        )
        assert deleted.status_code == 200, deleted.text
        thread_id = None
    finally:
        if thread_id is not None:
            await e2e_client.delete(
                f"/api/chat/thread/{thread_id}",
                headers=e2e_headers,
            )
        await _delete_agent(e2e_client, e2e_headers, leader_slug)
        for child_slug in child_slugs:
            await _delete_agent(e2e_client, e2e_headers, child_slug)
