"""网关翻转与守卫集成测试（迁移工单 14 · ①②⑥）。

对真实 PG/agentscope 服务验证：存量线程拒发、队列任务全链路（intake →
arq 执行体 → yuxi 消息落库 → 终态派发）、审批挂起 → resume 决定映射。
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime
from unittest.mock import MagicMock

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.agentscope.client import AgentScopeServiceClient
from yuxi.agentscope.runner import ensure_thread_session
from yuxi.agentscope.thread_guard import (
    ensure_thread_eligible,
    is_legacy_thread,
)
from yuxi.agentscope.worker_job import execute_agent_run_job
from yuxi.repositories.agent_run_repository import AgentRunRepository
from yuxi.services import agent_request_queue_service
from yuxi.services.input_message_service import build_chat_input_message
from yuxi.storage.postgres.models_business import (
    Agent,
    AgentRun,
    AgentRunRequest,
    Conversation,
    Message,
    ModelProvider,
)
from yuxi.storage.postgres.manager import pg_manager

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

AGENTSCOPE_BASE_URL = os.getenv("AGENTSCOPE_BASE_URL", "http://agentscope:8100")
PROVIDER_ID = "e2e-openai-mock"


@pytest.fixture
async def env(monkeypatch):
    """独立引擎会话 + 截获 arq 投递；隔离 agent/供应商夹具。"""
    agent_slug = f"it-job-chatbot-{uuid.uuid4().hex[:6]}"
    thread_id = f"it-job-{uuid.uuid4().hex[:10]}"
    uid = f"it-job-user-{uuid.uuid4().hex[:8]}"
    engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    monkeypatch.setattr(
        agent_request_queue_service,
        "resolve_agent_run_config",
        lambda *a: ("e2e-openai-mock:mock-chat-model", "default"),
    )
    enqueued: list[str] = []

    async def _capture_enqueue(run_id: str) -> None:
        enqueued.append(run_id)

    monkeypatch.setattr(agent_request_queue_service, "enqueue_agent_run", _capture_enqueue)
    monkeypatch.setenv("AGENTSCOPE_LEGACY_CUTOFF", "2000-01-01T00:00:00+00:00")

    async with session_factory() as db:
        db.add(
            Conversation(thread_id=thread_id, uid=uid, agent_id=agent_slug, status="active")
        )
        db.add(
            Agent(
                slug=agent_slug,
                name="任务测试智能体",
                backend_id="ChatbotAgent",
                config_json={
                    "context": {
                        "model": f"{PROVIDER_ID}:mock-chat-model",
                        "system_prompt": "你是任务测试助手。",
                    }
                },
                share_config={},
            )
        )
        provider = await db.scalar(
            select(ModelProvider).where(ModelProvider.provider_id == PROVIDER_ID)
        )
        if provider is None:
            provider = ModelProvider(provider_id=PROVIDER_ID)
            db.add(provider)
        provider.display_name = "e2e mock provider"
        provider.provider_type = "openai"
        provider.base_url = os.getenv("OPENAI_MOCK_BASE_URL", "http://openai-mock:8080/v1")
        provider.api_key = "e2e-mock-key"
        provider.capabilities = ["chat"]
        provider.enabled_models = [{"id": "mock-chat-model", "type": "chat"}]
        provider.is_enabled = True
        await db.commit()

    yield {
        "agent_slug": agent_slug,
        "thread_id": thread_id,
        "uid": uid,
        "session_factory": session_factory,
        "enqueued": enqueued,
    }

    async with session_factory() as db:
        conversation_id = await db.scalar(
            select(Conversation.id).where(Conversation.thread_id == thread_id)
        )
        await db.execute(
            delete(AgentRunRequest).where(
                AgentRunRequest.conversation_thread_id == thread_id
            )
        )
        if conversation_id is not None:
            await db.execute(delete(Message).where(Message.conversation_id == conversation_id))
        await db.execute(delete(AgentRun).where(AgentRun.conversation_thread_id == thread_id))
        await db.execute(delete(Conversation).where(Conversation.thread_id == thread_id))
        await db.execute(
            delete(Agent).where(
                Agent.slug.in_(
                    select(AgentRun.agent_slug).where(
                        AgentRun.conversation_thread_id == thread_id
                    )
                )
            )
        )
        await db.execute(delete(Agent).where(Agent.slug == agent_slug))
        await db.execute(
            delete(Agent).where(
                Agent.slug.like(f"{uid}%")
            )
        )
        await db.commit()
    await engine.dispose()
    # 复位跨测试事件循环的共享单例（PG 管理器与 Redis 客户端）
    from yuxi.storage.redis.manager import close_async_redis_client

    await pg_manager.reset()
    await close_async_redis_client()


async def _intake(env, request_id: str, text: str, policy: str = "enqueue"):
    async with env["session_factory"]() as db:
        result = await agent_request_queue_service.intake_request(
            db=db,
            request_id=request_id,
            uid=env["uid"],
            agent_slug=env["agent_slug"],
            thread_id=env["thread_id"],
            queue_policy=policy,
            input_message=build_chat_input_message(text),
            agent_item=MagicMock(),
            agent_backend=MagicMock(),
        )
        await agent_request_queue_service.finalize_intake(db=db, intake=result)
        return result


async def test_legacy_thread_rejected(env):
    """存量线程（cutover 前消息 + 无映射）拒绝接入并提示开新线程。"""
    async with env["session_factory"]() as db:
        conversation_id = await db.scalar(
            select(Conversation.id).where(Conversation.thread_id == env["thread_id"])
        )
        db.add(
            Message(
                conversation_id=conversation_id,
                role="user",
                content="旧栈消息",
                message_type="text",
                created_at=datetime(1999, 1, 1),
            )
        )
        await db.commit()

        assert await is_legacy_thread(db, thread_id=env["thread_id"])
        with pytest.raises(ValueError, match="新建线程"):
            await ensure_thread_eligible(db, uid=env["uid"], thread_id=env["thread_id"])

        client = AgentScopeServiceClient(AGENTSCOPE_BASE_URL)
        with pytest.raises(ValueError, match="新建线程"):
            await ensure_thread_session(
                db,
                client,
                uid=env["uid"],
                thread_id=env["thread_id"],
                agent_slug=env["agent_slug"],
            )


async def test_new_thread_not_blocked_by_cutoff(env):
    """切换后的消息不触发守卫（intake 先落用户消息的场景）。"""
    async with env["session_factory"]() as db:
        assert not await is_legacy_thread(db, thread_id=env["thread_id"])
        await ensure_thread_eligible(db, uid=env["uid"], thread_id=env["thread_id"])


async def test_worker_job_full_pipeline(env):
    """intake → 执行体 → yuxi 消息落库 → 终态 → 队头派发。"""
    first = await _intake(env, f"itj-1-{uuid.uuid4().hex[:8]}", "任务链路消息")
    assert first.status == "dispatched" and first.run_id

    await execute_agent_run_job(first.run_id)

    async with env["session_factory"]() as db:
        run = await AgentRunRepository(db).get_run_by_request_id(first.request_id)
        assert run.status == "completed"
        assert run.finished_at is not None

        messages = (
            await db.execute(
                select(Message)
                .join(Conversation, Message.conversation_id == Conversation.id)
                .where(Conversation.thread_id == env["thread_id"])
                .order_by(Message.id)
            )
        ).scalars().all()
        roles = [m.role for m in messages]
        assert "user" in roles and "assistant" in roles
        assistant = next(m for m in messages if m.role == "assistant")
        assert assistant.content.startswith("你好，我是 e2e mock 模型")


async def test_steer_interrupted_run_dispatches_queue_head(env, monkeypatch):
    """steer 中断（非审批挂起）终态后必须派发队头，否则引导消息永久滞留。"""
    from yuxi.agentscope import worker_job
    from yuxi.agentscope.gateway import GatewayRoundResult

    first = await _intake(env, f"itj-s1-{uuid.uuid4().hex[:8]}", "被中断的第一条")
    assert first.status == "dispatched"
    second = await _intake(env, f"itj-s2-{uuid.uuid4().hex[:8]}", "排队的引导消息")
    assert second.status == "queued"

    dispatched: list[str] = []

    async def _capture_dispatch(**kwargs):
        dispatched.append(kwargs.get("thread_id"))

    async def _interrupted_run(db, client, *, run, text, read_timeout=180.0, model_spec=None):
        return GatewayRoundResult(
            run_status="interrupted", text="", reasoning="", event_count=1
        )

    monkeypatch.setattr(worker_job, "dispatch_next_request", _capture_dispatch)
    monkeypatch.setattr(worker_job, "execute_run", _interrupted_run)

    await worker_job.execute_agent_run_job(first.run_id)
    assert dispatched == [env["thread_id"]]


async def test_approval_parked_interrupted_run_holds_queue(env, monkeypatch):
    """审批挂起同为 interrupted 终态，但队列必须等待审批结果而不派发。"""
    from yuxi.agentscope import worker_job
    from yuxi.agentscope.gateway import GatewayRoundResult

    first = await _intake(env, f"itj-p1-{uuid.uuid4().hex[:8]}", "挂起审批的第一条")
    assert first.status == "dispatched"
    second = await _intake(env, f"itj-p2-{uuid.uuid4().hex[:8]}", "排队等待的消息")
    assert second.status == "queued"

    dispatched: list[str] = []

    async def _capture_dispatch(**kwargs):
        dispatched.append(kwargs.get("thread_id"))

    async def _parked_run(db, client, *, run, text, read_timeout=180.0, model_spec=None):
        return GatewayRoundResult(
            run_status="interrupted",
            text="",
            reasoning="",
            event_count=1,
            parked="permission",
            pending_confirm={"reply_id": "r-park", "tool_calls": []},
        )

    monkeypatch.setattr(worker_job, "dispatch_next_request", _capture_dispatch)
    monkeypatch.setattr(worker_job, "execute_run", _parked_run)

    try:
        await worker_job.execute_agent_run_job(first.run_id)
        assert dispatched == []
    finally:
        await worker_job.load_pending_confirm(env["thread_id"])


async def test_finalize_run_normalizes_cancel_signal_to_cancelled(env):
    """存在取消信号时终态归一为 cancelled 并清除信号（执行/resume 共用路径）。"""
    from yuxi.agentscope.execution import finalize_run
    from yuxi.agentscope.gateway import GatewayRoundResult
    from yuxi.services.run_queue_service import has_cancel_signal, publish_cancel_signal

    first = await _intake(env, f"itj-c1-{uuid.uuid4().hex[:8]}", "被取消的请求")
    assert first.status == "dispatched"
    async with env["session_factory"]() as db:
        from yuxi.repositories.agent_run_repository import AgentRunRepository

        run = await AgentRunRepository(db).get_run(first.run_id)
        await publish_cancel_signal(run.id)
        result = GatewayRoundResult(
            run_status="interrupted", text="部分输出", reasoning="", event_count=2
        )
        await finalize_run(db, run, result)
        await db.commit()
        assert result.run_status == "cancelled"
        refreshed = await AgentRunRepository(db).get_run(run.id)
        assert refreshed.status == "cancelled"
        assert refreshed.error_message is None
    assert not (await has_cancel_signal(first.run_id))
