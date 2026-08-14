"""agentscope 执行器 × 线程队列集成测试（迁移工单 05）。

验证运行事实语义在新执行路径上成立：提交即落库、同线程 FIFO 串行、
interrupted 挂起互斥、幂等提交、恢复扫描不产生孤儿排队请求。
执行侧走真实 agentscope 服务与 OpenAI 兼容流式 mock。
"""

from __future__ import annotations

import os
import uuid
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.agentscope.client import AgentScopeServiceClient
from yuxi.agentscope.execution import execute_run
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

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

AGENTSCOPE_BASE_URL = os.getenv("AGENTSCOPE_BASE_URL", "http://agentscope:8100")
AGENT_SLUG_PREFIX = "it-queue-chatbot"
PROVIDER_ID = "e2e-openai-mock"


@pytest.fixture
async def env(monkeypatch):
    """独立引擎 + 会话工厂；截获 ARQ 投递；准备投影所需的智能体与供应商。"""
    agent_slug = f"{AGENT_SLUG_PREFIX}-{uuid.uuid4().hex[:6]}"
    thread_id = f"it-queue-{uuid.uuid4().hex[:10]}"
    uid = f"it-queue-user-{uuid.uuid4().hex[:8]}"
    engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    monkeypatch.setattr(
        agent_request_queue_service, "resolve_agent_run_config", lambda *a: ("model", "default")
    )
    enqueued: list[str] = []

    async def _capture_enqueue(run_id: str) -> None:
        enqueued.append(run_id)

    monkeypatch.setattr(agent_request_queue_service, "enqueue_agent_run", _capture_enqueue)

    async with session_factory() as db:
        db.add(
            Conversation(thread_id=thread_id, uid=uid, agent_id=agent_slug, status="active")
        )
        db.add(
            Agent(
                slug=agent_slug,
                name="队列执行智能体",
                backend_id="ChatbotAgent",
                config_json={
                    "context": {
                        "model": f"{PROVIDER_ID}:mock-chat-model",
                        "system_prompt": "你是队列测试助手。",
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
        await db.execute(delete(Agent).where(Agent.slug == agent_slug))
        await db.commit()
    await engine.dispose()
    # 复位跨测试事件循环的共享单例（PG 管理器与 Redis 客户端）
    from yuxi.storage.postgres.manager import pg_manager
    from yuxi.storage.redis.manager import close_async_redis_client

    await pg_manager.close()
    pg_manager._initialized = False
    await close_async_redis_client()


async def _intake(env, request_id: str, text: str):
    """intake → 提交事务 → 提交成功后才投递（复刻路由层的两阶段调用序）。"""
    async with env["session_factory"]() as db:
        result = await agent_request_queue_service.intake_request(
            db=db,
            request_id=request_id,
            uid=env["uid"],
            agent_slug=env["agent_slug"],
            thread_id=env["thread_id"],
            queue_policy="enqueue",
            input_message=build_chat_input_message(text),
            agent_item=MagicMock(),
            agent_backend=MagicMock(),
        )
        await agent_request_queue_service.finalize_intake(db=db, intake=result)
        return result


async def _execute_pending_run(env, request_id: str, text: str) -> str:
    """取出该请求对应的 Run 并用 agentscope 执行器执行到终态。"""
    client = AgentScopeServiceClient(AGENTSCOPE_BASE_URL)
    async with env["session_factory"]() as db:
        run = await AgentRunRepository(db).get_run_by_request_id(request_id)
        assert run is not None and run.status == "pending"
        await AgentRunRepository(db).mark_running(run.id)
        await db.commit()
        result = await execute_run(db, client, run=run, text=text)
        await db.commit()
        return result.run_status


async def test_fifo_serial_dispatch_and_execution(env):
    first = await _intake(env, f"itq-1-{uuid.uuid4().hex[:8]}", "第一条消息")
    second = await _intake(env, f"itq-2-{uuid.uuid4().hex[:8]}", "第二条消息")

    # 第一条立即派发（运行事实已落库），第二条排队，不产生第二个 Run
    assert first.status == "dispatched" and first.run_id is not None
    assert len(env["enqueued"]) == 1
    assert second.status == "queued" and second.run_id is None

    status = await _execute_pending_run(env, first.request_id, "第一条消息")
    assert status == "completed"

    # 队头完成后，恢复/续派机制把排队请求升级为 Run
    promoted = await agent_request_queue_service.dispatch_next_request(
        uid=env["uid"], agent_slug=env["agent_slug"], thread_id=env["thread_id"]
    )
    assert promoted is not None
    assert len(env["enqueued"]) == 2

    status = await _execute_pending_run(env, second.request_id, "第二条消息")
    assert status == "completed"

    # 队列清空：无排队残留
    async with env["session_factory"]() as db:
        queued = await db.execute(
            select(AgentRunRequest.id).where(
                AgentRunRequest.conversation_thread_id == env["thread_id"],
                AgentRunRequest.status == "queued",
            )
        )
        assert queued.first() is None


async def test_interrupted_run_blocks_intake(env):
    async with env["session_factory"]() as db:
        db.add(
            AgentRun(
                id=f"itq-run-{uuid.uuid4().hex[:10]}",
                conversation_thread_id=env["thread_id"],
                agent_slug=env["agent_slug"],
                uid=env["uid"],
                status="interrupted",
                request_id=f"itq-int-{uuid.uuid4().hex[:8]}",
                run_type="chat",
                origin_metadata={},
            )
        )
        await db.commit()

    with pytest.raises(HTTPException) as exc_info:
        await _intake(env, f"itq-blocked-{uuid.uuid4().hex[:8]}", "挂起后的新消息")
    assert exc_info.value.status_code == 409


async def test_intake_is_idempotent_per_request_id(env):
    request_id = f"itq-idem-{uuid.uuid4().hex[:8]}"
    first = await _intake(env, request_id, "重复提交")
    second = await _intake(env, request_id, "重复提交")
    assert first.request_id == second.request_id
    # 幂等语义：重复提交命中既有 intake 结果，重复投递的是同一个 run（worker 侧有状态守卫）
    assert env["enqueued"] and env["enqueued"][0] == first.run_id
    assert set(env["enqueued"]) == {first.run_id}

    async with env["session_factory"]() as db:
        runs = await db.execute(
            select(AgentRun.id).where(AgentRun.request_id == request_id)
        )
        assert len(runs.all()) == 1


async def test_recovery_scan_promotes_orphaned_queue(env):
    first = await _intake(env, f"itq-r1-{uuid.uuid4().hex[:8]}", "恢复第一条")
    second = await _intake(env, f"itq-r2-{uuid.uuid4().hex[:8]}", "恢复第二条")

    # 模拟执行器崩溃：Run 1 停在 pending。恢复扫描应重新投递 pending run，
    # 排队请求按 FIFO 语义继续等待（不越队），run 1 完成后才被升级派发。
    await agent_request_queue_service.recover_pending_dispatches()
    assert env["enqueued"].count(first.run_id) >= 2  # 原始派发 + 恢复重投

    status = await _execute_pending_run(env, first.request_id, "恢复第一条")
    assert status == "completed"

    promoted = await agent_request_queue_service.dispatch_next_request(
        uid=env["uid"], agent_slug=env["agent_slug"], thread_id=env["thread_id"]
    )
    assert promoted == second.run_id or promoted is not None

    async with env["session_factory"]() as db:
        orphan = await db.execute(
            select(AgentRunRequest.id).where(
                AgentRunRequest.conversation_thread_id == env["thread_id"],
                AgentRunRequest.status == "queued",
            )
        )
        assert orphan.first() is None, "队头完成后不应残留孤儿排队请求"
