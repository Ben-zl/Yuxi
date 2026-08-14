"""审批与中断 e2e（迁移工单 10）。

真实链路：敏感工具触发审批挂起（REQUIRE_USER_CONFIRM）→ 前端契约的
human_approval_required chunk 落 Run 事件流 → 批准后经 resume 续跑到
终态；拒绝后运行结束；挂起态可取消。
"""

import os
import uuid

import pytest

from test.e2e.agentscope_e2e_fixtures import (
    PROVIDER_ID,
    cleanup_fixture_agents,
    upsert_mock_provider,
)
from yuxi.agentscope.client import AgentScopeServiceClient
from yuxi.agentscope.runner import collect_chat_round, ensure_thread_session
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import Agent

AGENTSCOPE_BASE_URL = os.getenv("AGENTSCOPE_BASE_URL", "http://agentscope:8100")
CHATBOT_SLUG = "e2e-approval-chatbot"

pytestmark = pytest.mark.e2e


@pytest.fixture
async def db_session():
    pg_manager.initialize()
    await pg_manager.ensure_business_schema()
    async with pg_manager.get_async_session_context() as session:
        await cleanup_fixture_agents(session, CHATBOT_SLUG)
        session.add(
            Agent(
                slug=CHATBOT_SLUG,
                name="审批测试智能体",
                backend_id="ChatbotAgent",
                config_json={
                    "context": {
                        "model": f"{PROVIDER_ID}:mock-chat-model",
                        "system_prompt": "你是审批测试助手。",
                    }
                },
                share_config={},
            )
        )
        await upsert_mock_provider(session)
        yield session
        await cleanup_fixture_agents(session, CHATBOT_SLUG)
    await pg_manager.close()
    pg_manager._initialized = False


def _confirm_event(result) -> dict:
    """从挂起轮次结果中取出 REQUIRE_USER_CONFIRM 事件。"""
    for event in reversed(result.events):
        if str(event.get("type", "")).upper() == "REQUIRE_USER_CONFIRM":
            return event
    pytest.fail(f"未收到审批挂起事件: {[e.get('type') for e in result.events]}")


async def test_sensitive_tool_parks_and_resume_approves(db_session):
    uid = "e2e-approval"
    thread_id = f"e2e-thread-{uuid.uuid4().hex[:12]}"
    client = AgentScopeServiceClient(AGENTSCOPE_BASE_URL)

    # 默认权限模式：Write 触发审批挂起
    mapping = await ensure_thread_session(
        db_session, client, uid=uid, thread_id=thread_id, agent_slug=CHATBOT_SLUG
    )
    park_round = await collect_chat_round(
        client,
        uid=uid,
        agent_id=mapping.agentscope_agent_id,
        session_id=mapping.agentscope_session_id,
        text="请写文件到 outputs 目录",
    )
    assert park_round.parked == "permission"

    confirm_event = _confirm_event(park_round)
    assert confirm_event["tool_calls"][0]["name"] == "Write"

    # 批准后 resume 续跑到终态，Write 真实执行
    import asyncio

    queue = asyncio.Queue()

    async def _pump():
        async for event in client.stream_events(
            uid, mapping.agentscope_agent_id, mapping.agentscope_session_id, read_timeout=120.0
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
            confirmed=True,
        )
        types = []
        while True:
            event = await asyncio.wait_for(queue.get(), timeout=120.0)
            event_type = str(event.get("type", "")).upper()
            types.append(event_type)
            if event_type == "REPLY_END":
                assert str(event.get("finished_reason", "")).lower() == "completed"
                break
        assert "TOOL_RESULT_END" in types, types
    finally:
        pump.cancel()


async def test_reject_ends_round(db_session):
    uid = "e2e-approval-reject"
    thread_id = f"e2e-thread-{uuid.uuid4().hex[:12]}"
    client = AgentScopeServiceClient(AGENTSCOPE_BASE_URL)
    mapping = await ensure_thread_session(
        db_session, client, uid=uid, thread_id=thread_id, agent_slug=CHATBOT_SLUG
    )
    park_round = await collect_chat_round(
        client,
        uid=uid,
        agent_id=mapping.agentscope_agent_id,
        session_id=mapping.agentscope_session_id,
        text="请写文件到 outputs 目录",
    )
    assert park_round.parked == "permission"
    confirm_event = _confirm_event(park_round)

    import asyncio

    queue = asyncio.Queue()

    async def _pump():
        async for event in client.stream_events(
            uid, mapping.agentscope_agent_id, mapping.agentscope_session_id, read_timeout=120.0
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
            confirmed=False,
        )
        while True:
            event = await asyncio.wait_for(queue.get(), timeout=120.0)
            if str(event.get("type", "")).upper() == "REPLY_END":
                # 拒绝后 agentscope 原生行为：模型收到拒绝说明并结束本轮
                assert str(event.get("finished_reason", "")).lower() == "completed"
                break
    finally:
        pump.cancel()


async def test_parked_session_can_be_cancelled(db_session):
    uid = "e2e-approval-cancel"
    thread_id = f"e2e-thread-{uuid.uuid4().hex[:12]}"
    client = AgentScopeServiceClient(AGENTSCOPE_BASE_URL)
    mapping = await ensure_thread_session(
        db_session, client, uid=uid, thread_id=thread_id, agent_slug=CHATBOT_SLUG
    )
    park_round = await collect_chat_round(
        client,
        uid=uid,
        agent_id=mapping.agentscope_agent_id,
        session_id=mapping.agentscope_session_id,
        text="请写文件到 outputs 目录",
    )
    assert park_round.parked == "permission"

    import asyncio

    queue = asyncio.Queue()

    async def _pump():
        async for event in client.stream_events(
            uid, mapping.agentscope_agent_id, mapping.agentscope_session_id, read_timeout=60.0
        ):
            await queue.put(event)

    pump = asyncio.create_task(_pump())
    try:
        await asyncio.sleep(0.5)
        await client.interrupt_session(
            uid, mapping.agentscope_agent_id, mapping.agentscope_session_id
        )
        while True:
            event = await asyncio.wait_for(queue.get(), timeout=60.0)
            if str(event.get("type", "")).upper() == "REPLY_END":
                assert str(event.get("finished_reason", "")).lower() == "interrupted"
                break
    finally:
        pump.cancel()
