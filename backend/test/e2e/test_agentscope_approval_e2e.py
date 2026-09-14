"""审批与中断 e2e（迁移工单 10）。

真实链路：敏感工具触发审批挂起（REQUIRE_USER_CONFIRM）→ 前端契约的
human_approval_required chunk 落 Run 事件流 → 批准后经 resume 续跑到
终态；拒绝后运行结束；挂起态可取消。
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
from yuxi.agentscope.runner import ensure_thread_session
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import Agent
from yuxi.storage.redis.manager import close_async_redis_client

AGENTSCOPE_BASE_URL = os.getenv("AGENTSCOPE_BASE_URL", "http://agentscope:8100")
CHATBOT_SLUG = "e2e-approval-chatbot"
TEST_UIDS = ("e2e-approval", "e2e-approval-reject", "e2e-approval-cancel")

pytestmark = pytest.mark.e2e


@pytest.fixture
async def db_session():
    pg_manager.initialize()
    await pg_manager.ensure_business_schema()
    async with pg_manager.get_async_session_context() as session:
        await cleanup_fixture_agents(session, CHATBOT_SLUG)
        await seed_test_users(session, *TEST_UIDS)
        session.add(
            Agent(
                slug=CHATBOT_SLUG,
                name="审批测试智能体",
                backend_id="ChatbotAgent",
                config_json={
                    "context": {
                        "model": f"{PROVIDER_RESOURCE_ID}:mock-chat-model",
                        "mcps": [],
                        "tools": [],
                        "skills": [],
                        "subagents": [],
                        "system_prompt": "你是审批测试助手。",
                    }
                },
                share_config={},
            )
        )
        await upsert_mock_provider(session)
        yield session
        await cleanup_fixture_agents(session, CHATBOT_SLUG)
        await cleanup_test_users(session, *TEST_UIDS)
    await close_async_redis_client()
    await pg_manager.close()
    pg_manager._initialized = False


def _confirm_event(result) -> dict:
    """从挂起轮次结果中取出 REQUIRE_USER_CONFIRM 事件。"""
    for event in reversed(result.events):
        if str(event.get("type", "")).upper() == "REQUIRE_USER_CONFIRM":
            return event
    pytest.fail(f"未收到审批挂起事件: {[e.get('type') for e in result.events]}")


async def _resume_and_collect(client, mapping, uid, confirm_event, confirmed):
    """订阅在先、恢复在后，收集到 REPLY_END 返回 (事件类型列表, 结束原因)。"""
    import asyncio

    queue = asyncio.Queue()

    async def _pump():
        async for event in client.stream_events(
            uid,
            mapping.agentscope_agent_id,
            mapping.agentscope_session_id,
            read_timeout=120.0,
        ):
            await queue.put(event)

    pump = asyncio.create_task(_pump())
    try:
        await asyncio.sleep(0.5)
        await client.resume_confirm(
            uid,
            mapping.agentscope_agent_id,
            mapping.agentscope_session_id,
            reply_id=confirm_event["reply_id"],
            tool_calls=confirm_event["tool_calls"],
            confirmed=confirmed,
        )
        types = []
        while True:
            event = await asyncio.wait_for(queue.get(), timeout=120.0)
            event_type = str(event.get("type", "")).upper()
            types.append(event_type)
            if event_type == "REPLY_END":
                return types, str(event.get("finished_reason", "")).lower()
    finally:
        pump.cancel()


async def _park_round(client, headers, uid):
    """通过统一 Run 提交触发 Write 审批挂起。"""
    thread_response = await client.post(
        "/api/chat/thread",
        json={"agent_id": CHATBOT_SLUG, "title": f"approval-{uuid.uuid4().hex[:8]}"},
        headers=headers,
    )
    assert thread_response.status_code == 200, thread_response.text
    thread_id = str(thread_response.json().get("thread_id") or thread_response.json()["id"])
    run_response = await client.post(
        "/api/agent/runs",
        json={
            "query": "请写文件到 outputs 目录",
            "agent_slug": CHATBOT_SLUG,
            "thread_id": thread_id,
            "meta": {"request_id": f"e2e-approval-{uuid.uuid4().hex[:8]}"},
        },
        headers=headers,
    )
    assert run_response.status_code == 200, run_response.text
    run_id = str(run_response.json()["run_id"])
    event_counts = await consume_events(client, headers, run_id)
    assert event_counts.get("end", 0) == 1, event_counts
    run = await wait_for_run(client, headers, run_id)
    assert run["status"] == "interrupted", run
    assert run["error_type"] == "human_approval_required", run
    return thread_id, run_id


async def _new_thread(db_session, client, uid):
    thread_id = f"e2e-thread-{uuid.uuid4().hex[:12]}"
    mapping = await ensure_thread_session(db_session, client, uid=uid, thread_id=thread_id, agent_slug=CHATBOT_SLUG)
    return mapping


async def test_sensitive_tool_parks_and_resume_approves(db_session):
    client, headers = await open_fixture_http_client("e2e-approval")
    try:
        thread_id, run_id = await _park_round(client, headers, "e2e-approval")
        resume_response = await client.post(
            "/api/agent/runs",
            json={
                "agent_slug": CHATBOT_SLUG,
                "thread_id": thread_id,
                "resume": {"decisions": [{"type": "approve"}]},
                "created_by_run_id": run_id,
                "meta": {"request_id": f"e2e-approval-resume-{uuid.uuid4().hex[:8]}"},
            },
            headers=headers,
        )
        assert resume_response.status_code == 200, resume_response.text
        resumed_run_id = str(resume_response.json()["run_id"])
        events = await consume_events(client, headers, resumed_run_id)
        assert events.get("end", 0) == 1, events
        resumed = await wait_for_run(client, headers, resumed_run_id)
        assert resumed["status"] == "completed", resumed
    finally:
        await client.aclose()


async def test_reject_ends_round(db_session):
    client, headers = await open_fixture_http_client("e2e-approval-reject")
    try:
        thread_id, run_id = await _park_round(client, headers, "e2e-approval-reject")
        resume_response = await client.post(
            "/api/agent/runs",
            json={
                "agent_slug": CHATBOT_SLUG,
                "thread_id": thread_id,
                "resume": {"decisions": [{"type": "reject"}]},
                "created_by_run_id": run_id,
                "meta": {"request_id": f"e2e-approval-reject-resume-{uuid.uuid4().hex[:8]}"},
            },
            headers=headers,
        )
        assert resume_response.status_code == 200, resume_response.text
        resumed_run_id = str(resume_response.json()["run_id"])
        await consume_events(client, headers, resumed_run_id)
        resumed = await wait_for_run(client, headers, resumed_run_id)
        assert resumed["status"] == "completed", resumed
    finally:
        await client.aclose()


async def test_parked_session_can_be_cancelled(db_session):
    client, headers = await open_fixture_http_client("e2e-approval-cancel")
    try:
        _thread_id, run_id = await _park_round(client, headers, "e2e-approval-cancel")
        cancel_response = await client.post(f"/api/agent/runs/{run_id}/cancel", headers=headers)
        assert cancel_response.status_code < 500, cancel_response.text
        cancelled = await wait_for_run(client, headers, run_id)
        assert cancelled["status"] == "cancelled", cancelled
    finally:
        await client.aclose()
