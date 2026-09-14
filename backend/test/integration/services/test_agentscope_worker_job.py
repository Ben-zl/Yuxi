"""网关翻转与守卫集成测试（迁移工单 14 · ①②⑥）。

对真实 PG/agentscope 服务验证：存量线程拒发、队列任务全链路（intake →
arq 执行体 → yuxi 消息落库 → 终态派发）、审批挂起 → resume 决定映射。
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from test.e2e.agentscope_e2e_fixtures import (
    PROVIDER_RESOURCE_ID,
    upsert_mock_provider,
)

from yuxi.agents.buildin import agent_manager
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
    Project,
    User,
)
from yuxi.storage.postgres.manager import pg_manager

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

AGENTSCOPE_BASE_URL = os.getenv("AGENTSCOPE_BASE_URL", "http://agentscope:8100")


@pytest.fixture
async def env(monkeypatch):
    """独立引擎会话 + 截获 arq 投递；隔离 agent/供应商夹具。"""
    agent_slug = f"it-job-chatbot-{uuid.uuid4().hex[:6]}"
    thread_id = f"it-job-{uuid.uuid4().hex[:10]}"
    uid = f"it-job-user-{uuid.uuid4().hex[:8]}"
    engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    enqueued: list[str] = []

    async def _capture_enqueue(run_id: str) -> None:
        enqueued.append(run_id)

    monkeypatch.setattr(agent_request_queue_service, "enqueue_agent_run", _capture_enqueue)
    monkeypatch.setenv("AGENTSCOPE_LEGACY_CUTOFF", "2000-01-01T00:00:00+00:00")

    async with session_factory() as db:
        await upsert_mock_provider(db)
        project_id = str(uuid.uuid4())
        db.add(
            User(
                uid=uid,
                username=uid,
                password_hash="test-only",
                role="superadmin",
            )
        )
        await db.flush()
        db.add(
            Project(
                id=project_id,
                uid=uid,
                selection_status="implicit",
                workdir_path=f"projects/{project_id}",
                directory_mode="managed",
            )
        )
        db.add(
            Conversation(
                thread_id=thread_id,
                uid=uid,
                agent_id=agent_slug,
                project_id=project_id,
                status="active",
            )
        )
        db.add(
            Agent(
                slug=agent_slug,
                name="任务测试智能体",
                backend_id="ChatbotAgent",
                config_json={
                    "context": {
                        "model": f"{PROVIDER_RESOURCE_ID}:mock-chat-model",
                        "skills": [],
                        "mcps": [],
                        "system_prompt": "你是任务测试助手。",
                    }
                },
                share_config={
                    "version": 2,
                    "read_scope": {
                        "access_level": "global",
                        "department_ids": [],
                        "user_uids": [],
                    },
                    "manage_scope": None,
                },
            )
        )
        await db.commit()

    yield {
        "agent_slug": agent_slug,
        "thread_id": thread_id,
        "uid": uid,
        "project_id": project_id,
        "session_factory": session_factory,
        "enqueued": enqueued,
    }

    async with session_factory() as db:
        conversation_id = await db.scalar(select(Conversation.id).where(Conversation.thread_id == thread_id))
        await db.execute(delete(AgentRunRequest).where(AgentRunRequest.conversation_thread_id == thread_id))
        if conversation_id is not None:
            await db.execute(delete(Message).where(Message.conversation_id == conversation_id))
        await db.execute(delete(AgentRun).where(AgentRun.conversation_thread_id == thread_id))
        await db.execute(delete(Conversation).where(Conversation.thread_id == thread_id))
        await db.execute(delete(Project).where(Project.id == project_id))
        await db.execute(delete(User).where(User.uid == uid))
        await db.execute(
            delete(Agent).where(
                Agent.slug.in_(select(AgentRun.agent_slug).where(AgentRun.conversation_thread_id == thread_id))
            )
        )
        await db.execute(delete(Agent).where(Agent.slug == agent_slug))
        await db.execute(delete(Agent).where(Agent.slug.like(f"{uid}%")))
        await db.commit()
    await engine.dispose()
    # 复位跨测试事件循环的共享单例（PG 管理器与 Redis 客户端）
    from yuxi.storage.redis.manager import close_async_redis_client

    await pg_manager.reset()
    await close_async_redis_client()


async def _intake(env, request_id: str, text: str, policy: str = "enqueue"):
    async with env["session_factory"]() as db:
        user = await db.scalar(select(User).where(User.uid == env["uid"]))
        agent_item = await db.scalar(select(Agent).where(Agent.slug == env["agent_slug"]))
        result = await agent_request_queue_service.intake_request(
            db=db,
            request_id=request_id,
            uid=env["uid"],
            agent_slug=env["agent_slug"],
            thread_id=env["thread_id"],
            queue_policy=policy,
            input_message=build_chat_input_message(text),
            agent_item=agent_item,
            agent_backend=agent_manager.get_agent(agent_item.backend_id),
            user=user,
        )
        await agent_request_queue_service.finalize_intake(
            db=db,
            intake=result,
            uid=env["uid"],
            workdir_path=f"projects/{env['project_id']}",
            materialize_managed=True,
        )
        return result


async def test_legacy_thread_rejected(env):
    """存量线程（cutover 前消息 + 无映射）拒绝接入并提示开新线程。"""
    async with env["session_factory"]() as db:
        conversation_id = await db.scalar(select(Conversation.id).where(Conversation.thread_id == env["thread_id"]))
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
        assert run.manifest is not None
        assert run.manifest_fingerprint
        assert run.manifest_recorded_at is not None

        messages = (
            (
                await db.execute(
                    select(Message)
                    .join(Conversation, Message.conversation_id == Conversation.id)
                    .where(Conversation.thread_id == env["thread_id"])
                    .order_by(Message.id)
                )
            )
            .scalars()
            .all()
        )
        roles = [m.role for m in messages]
        assert "user" in roles and "assistant" in roles
        assistant = next(m for m in messages if m.role == "assistant")
        assert assistant.content.startswith("你好，我是 e2e mock 模型")

        # run 终态回写输入消息投递状态
        user = next(m for m in messages if m.role == "user")
        assert user.delivery_status == "complete"
        assert run.output_message_id == assistant.id


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

    async def _interrupted_run(
        db,
        client,
        *,
        run,
        text,
        read_timeout=180.0,
        model_spec=None,
        image_content=None,
        mapping=None,
        persist_result=True,
    ):
        return GatewayRoundResult(run_status="interrupted", text="", reasoning="", event_count=1)

    monkeypatch.setattr(worker_job, "dispatch_next_request", _capture_dispatch)
    monkeypatch.setattr(worker_job, "execute_run", _interrupted_run)

    await worker_job.execute_agent_run_job(first.run_id)
    assert dispatched == [env["thread_id"]]


async def test_failed_run_dispatches_queue_head(env, monkeypatch):
    """队头执行失败也必须释放 FIFO，不能等待进程重启恢复扫描。"""
    from yuxi.agentscope import worker_job

    first = await _intake(env, f"itj-f1-{uuid.uuid4().hex[:8]}", "会失败的第一条")
    assert first.status == "dispatched"
    second = await _intake(env, f"itj-f2-{uuid.uuid4().hex[:8]}", "排队的第二条")
    assert second.status == "queued"

    enqueued: list[str] = []

    async def _capture_enqueue(run_id: str):
        enqueued.append(run_id)

    monkeypatch.setattr(agent_request_queue_service, "enqueue_agent_run", _capture_enqueue)
    monkeypatch.setattr(worker_job, "execute_run", AsyncMock(side_effect=RuntimeError("boom")))

    await worker_job.execute_agent_run_job(first.run_id)

    assert len(enqueued) == 1
    async with env["session_factory"]() as db:
        from yuxi.repositories.agent_run_repository import AgentRunRepository
        from yuxi.repositories.agent_run_request_repository import AgentRunRequestRepository

        failed = await AgentRunRepository(db).get_run(first.run_id)
        assert failed.status == "failed"
        assert failed.error_message == "执行失败: boom"
        promoted = await AgentRunRequestRepository(db).get_by_request_id(second.request_id)
        assert promoted.status == "dispatched"
        assert promoted.dispatched_run_id == enqueued[0]


async def test_mcp_failure_is_redacted_from_worker_run_events_and_result(env, monkeypatch):
    """MCP 底层异常不能跨过执行边界进入日志、Run、SSE 或结果 API。"""
    from yuxi.agents.mcp import service as mcp_service
    from yuxi.agentscope import worker_job
    from yuxi.services.agent_run_service import get_agent_run_result
    from yuxi.services.run_queue_service import get_redis_client, list_run_stream_events

    sensitive_marker = "synthetic-sensitive-mcp-token"
    first = await _intake(env, f"itj-mcp-{uuid.uuid4().hex[:8]}", "触发 MCP 失败")
    assert first.status == "dispatched"

    class FailingClient:
        async def list_tools(self):
            raise RuntimeError(sensitive_marker)

    async def fake_get_mcp_client(_server_configs):
        return FailingClient()

    async def execute_with_failing_mcp(*_args, **_kwargs):
        await mcp_service.get_mcp_tools(
            "mcp-sensitive",
            additional_servers={
                "mcp-sensitive": {
                    "resource_id": "mcp-sensitive",
                    "transport": "streamable_http",
                    "url": "https://example.test/mcp",
                }
            },
            user=SimpleNamespace(uid=env["uid"], role="superadmin"),
        )

    messages: list[str] = []
    sink_id = worker_job.logger.add(
        lambda message: messages.append(str(message)),
        format="{message}\n{exception}",
        enqueue=False,
    )
    monkeypatch.setattr(mcp_service, "get_mcp_client", fake_get_mcp_client)
    monkeypatch.setattr(worker_job, "execute_run", execute_with_failing_mcp)
    try:
        await worker_job.execute_agent_run_job(first.run_id)

        async with env["session_factory"]() as db:
            failed = await AgentRunRepository(db).get_run(first.run_id)
            result = await get_agent_run_result(run_id=first.run_id, current_uid=env["uid"], db=db)
        events = await list_run_stream_events(first.run_id)
    finally:
        worker_job.logger.remove(sink_id)
        mcp_service.clear_mcp_cache()
        redis = await get_redis_client()
        await redis.delete(f"run:events:{first.run_id}")

    assert failed.status == "failed"
    assert failed.error_message == "执行失败: 所选 MCP 暂不可用"
    assert result["error"]["message"] == "执行失败: 所选 MCP 暂不可用"
    assert any(event["event_type"] == "end" for event in events)
    assert sensitive_marker not in "".join(messages)
    assert sensitive_marker not in repr(events)
    assert sensitive_marker not in repr(result)


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

    async def _parked_run(
        db,
        client,
        *,
        run,
        text,
        read_timeout=180.0,
        model_spec=None,
        image_content=None,
        mapping=None,
        persist_result=True,
    ):
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


async def test_cancelling_parked_run_dispatches_waiting_request(env, monkeypatch):
    """Worker 已退出的审批 Run 被取消后，API 必须派发原队头。"""
    from yuxi.agentscope import worker_job
    from yuxi.agentscope.gateway import GatewayRoundResult
    from yuxi.agentscope.thread_guard import clear_pending_confirm, load_pending_confirm
    from yuxi.services.agent_run_service import request_cancel_agent_run

    first = await _intake(env, f"itj-park-{uuid.uuid4().hex[:8]}", "需要审批")
    second = await _intake(env, f"itj-wait-{uuid.uuid4().hex[:8]}", "等待取消后派发")
    assert second.status == "queued"

    async def parked_round(*_args, **_kwargs):
        return GatewayRoundResult(
            run_status="interrupted",
            text="",
            reasoning="",
            event_count=1,
            parked="permission",
            pending_confirm={"reply_id": "parked-reply", "tool_calls": []},
        )

    monkeypatch.setattr(worker_job, "execute_run", parked_round)
    try:
        await worker_job.execute_agent_run_job(first.run_id)
        async with env["session_factory"]() as db:
            run = await AgentRunRepository(db).get_run(first.run_id)
            assert run.status == "interrupted"
            await request_cancel_agent_run(run_id=first.run_id, current_uid=env["uid"], db=db)
        assert await load_pending_confirm(env["thread_id"]) is None
        async with env["session_factory"]() as db:
            request = await db.scalar(select(AgentRunRequest).where(AgentRunRequest.request_id == second.request_id))
            assert request.status == "dispatched"
        assert len(env["enqueued"]) == 2
    finally:
        await clear_pending_confirm(env["thread_id"])


async def test_cancelling_unclaimed_run_dispatches_waiting_request(env):
    """未认领 Run 在 Worker 取 lease 前被取消仍须续派队头。"""
    from yuxi.services.agent_run_service import request_cancel_agent_run

    first = await _intake(env, f"itj-unclaimed-{uuid.uuid4().hex[:8]}", "待 Worker 认领")
    second = await _intake(env, f"itj-unclaimed-next-{uuid.uuid4().hex[:8]}", "等待前条取消")
    assert second.status == "queued"

    async with env["session_factory"]() as db:
        await request_cancel_agent_run(run_id=first.run_id, current_uid=env["uid"], db=db)
    async with env["session_factory"]() as db:
        first_run = await AgentRunRepository(db).get_run(first.run_id)
        second_request = await db.scalar(select(AgentRunRequest).where(AgentRunRequest.request_id == second.request_id))
        assert first_run.status == "cancelled"
        assert second_request.status == "dispatched"
    assert len(env["enqueued"]) == 2


async def test_fail_run_observes_durable_cancel_after_redis_miss(env, monkeypatch):
    """异常收束读取取消信号后发生的持久取消必须赢过 failed。"""
    from yuxi.agentscope import worker_job
    from yuxi.services import run_queue_service
    from yuxi.services.run_queue_service import list_run_stream_events

    first = await _intake(env, f"itj-fail-cancel-{uuid.uuid4().hex[:8]}", "异常取消竞争")
    async with env["session_factory"]() as db:
        run_repo = AgentRunRepository(db)
        run, acquired = await run_repo.mark_running(first.run_id, worker_id="test-fail-worker", lease_seconds=60)
        assert acquired
        await db.commit()

    async def miss_then_cancel(_run_id):
        async with env["session_factory"]() as db:
            await AgentRunRepository(db).request_cancel(first.run_id)
            await db.commit()
        return False

    monkeypatch.setattr(run_queue_service, "has_cancel_signal", miss_then_cancel)
    async with env["session_factory"]() as db:
        changed = await worker_job._fail_run(
            db,
            AgentRunRepository(db),
            run_id=run.id,
            uid=env["uid"],
            agent_slug=env["agent_slug"],
            thread_id=env["thread_id"],
            input_message_id=run.input_message_id,
            worker_id="test-fail-worker",
            message="synthetic failure",
        )
    assert changed
    async with env["session_factory"]() as db:
        persisted = await AgentRunRepository(db).get_run(first.run_id)
        assert persisted.status == "cancelled"
    events = await list_run_stream_events(first.run_id)
    assert any(
        event["event_type"] == "end" and event["payload"]["payload"]["status"] == "cancelled" for event in events
    )


async def test_failed_approval_persistence_clears_owned_pending(env, monkeypatch):
    """审批键写入后持久化失败不能污染后续请求。"""
    from yuxi.agentscope import worker_job
    from yuxi.agentscope.gateway import GatewayRoundResult
    from yuxi.agentscope.thread_guard import clear_pending_confirm, load_pending_confirm

    first = await _intake(env, f"itj-park-fail-{uuid.uuid4().hex[:8]}", "审批持久化失败")
    second = await _intake(env, f"itj-park-next-{uuid.uuid4().hex[:8]}", "后续请求")
    assert second.status == "queued"

    async def parked_round(*_args, **_kwargs):
        return GatewayRoundResult(
            run_status="interrupted",
            text="",
            reasoning="",
            event_count=1,
            parked="permission",
            pending_confirm={"reply_id": "failed-approval", "tool_calls": []},
        )

    async def fail_output(*_args, **_kwargs):
        raise RuntimeError("synthetic persistence failure")

    monkeypatch.setattr(worker_job, "execute_run", parked_round)
    monkeypatch.setattr(worker_job, "persist_run_output", fail_output)
    try:
        await worker_job.execute_agent_run_job(first.run_id)
        async with env["session_factory"]() as db:
            failed = await AgentRunRepository(db).get_run(first.run_id)
            second_request = await db.scalar(
                select(AgentRunRequest).where(AgentRunRequest.request_id == second.request_id)
            )
            assert failed.status == "failed"
            assert second_request.status == "dispatched"
        assert await load_pending_confirm(env["thread_id"]) is None
    finally:
        await clear_pending_confirm(env["thread_id"])


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
        result = GatewayRoundResult(run_status="interrupted", text="部分输出", reasoning="", event_count=2)
        await finalize_run(db, run, result)
        await db.commit()
        assert result.run_status == "cancelled"
        refreshed = await AgentRunRepository(db).get_run(run.id)
        assert refreshed.status == "cancelled"
        assert refreshed.error_message is None
    assert not (await has_cancel_signal(first.run_id))


async def test_cancel_legacy_pending_confirm_and_preserve_new_owner(env, monkeypatch):
    """取消历史无 Owner 审批后，重复取消不能删除新 Run 审批。"""
    from fastapi import HTTPException

    from yuxi.agentscope import thread_guard
    from yuxi.agentscope.thread_guard import clear_pending_confirm, load_pending_confirm, store_pending_confirm
    from yuxi.services.agent_run_service import request_cancel_agent_run

    first = await _intake(env, f"itj-legacy-{uuid.uuid4().hex[:8]}", "历史审批")
    second = await _intake(env, f"itj-legacy-next-{uuid.uuid4().hex[:8]}", "等待历史审批取消")
    assert second.status == "queued"
    async with env["session_factory"]() as db:
        run = await AgentRunRepository(db).get_run(first.run_id)
        run.status = "interrupted"
        await db.commit()
    try:
        await store_pending_confirm(env["thread_id"], {"reply_id": "legacy"})
        real_clear = thread_guard.clear_pending_confirm

        async def fail_clear(*_args, **_kwargs):
            raise ConnectionError("Redis unavailable")

        monkeypatch.setattr(thread_guard, "clear_pending_confirm", fail_clear)
        async with env["session_factory"]() as db:
            with pytest.raises(HTTPException) as exc:
                await request_cancel_agent_run(run_id=first.run_id, current_uid=env["uid"], db=db)
            assert exc.value.status_code == 503
        assert (await load_pending_confirm(env["thread_id"]))["reply_id"] == "legacy"
        monkeypatch.setattr(thread_guard, "clear_pending_confirm", real_clear)
        async with env["session_factory"]() as db:
            await request_cancel_agent_run(run_id=first.run_id, current_uid=env["uid"], db=db)
        assert await load_pending_confirm(env["thread_id"]) is None
        async with env["session_factory"]() as db:
            request = await db.scalar(select(AgentRunRequest).where(AgentRunRequest.request_id == second.request_id))
            assert request.status == "dispatched"

        await store_pending_confirm(env["thread_id"], {"reply_id": "new"}, run_id="new-run")
        async with env["session_factory"]() as db:
            await request_cancel_agent_run(run_id=first.run_id, current_uid=env["uid"], db=db)
        assert (await load_pending_confirm(env["thread_id"]))["reply_id"] == "new"
    finally:
        await clear_pending_confirm(env["thread_id"])


@pytest.mark.parametrize("cancel_before_store", [True, False])
async def test_cancel_during_approval_park_cleans_pending_and_dispatches(env, monkeypatch, cancel_before_store):
    """取消与审批写入交错时以数据库终态为准，并放行队头。"""
    from yuxi.agentscope import worker_job
    from yuxi.agentscope.gateway import GatewayRoundResult
    from yuxi.agentscope.thread_guard import clear_pending_confirm, load_pending_confirm
    from yuxi.services.agent_run_service import request_cancel_agent_run
    from yuxi.services.run_queue_service import list_run_stream_events

    first = await _intake(env, f"itj-race-{uuid.uuid4().hex[:8]}", "审批中的请求")
    second = await _intake(env, f"itj-next-{uuid.uuid4().hex[:8]}", "后续排队请求")
    assert second.status == "queued"
    real_store = worker_job.store_pending_confirm

    async def store_during_cancel(thread_id, event, *, run_id):
        if cancel_before_store:
            async with env["session_factory"]() as db:
                await request_cancel_agent_run(run_id=run_id, current_uid=env["uid"], db=db)
        await real_store(thread_id, event, run_id=run_id)
        if not cancel_before_store:
            async with env["session_factory"]() as db:
                await request_cancel_agent_run(run_id=run_id, current_uid=env["uid"], db=db)

    async def parked_round(*_args, **_kwargs):
        return GatewayRoundResult(
            run_status="interrupted",
            text="",
            reasoning="",
            event_count=1,
            parked="permission",
            pending_confirm={"reply_id": "approval-race", "tool_calls": []},
        )

    monkeypatch.setattr(worker_job, "store_pending_confirm", store_during_cancel)
    monkeypatch.setattr(worker_job, "execute_run", parked_round)
    try:
        await worker_job.execute_agent_run_job(first.run_id)
        async with env["session_factory"]() as db:
            run = await AgentRunRepository(db).get_run(first.run_id)
            assert run.status == "cancelled"
            dispatched_request = await db.scalar(
                select(AgentRunRequest).where(AgentRunRequest.request_id == second.request_id)
            )
            assert dispatched_request.status == "dispatched"
        assert await load_pending_confirm(env["thread_id"]) is None
        assert len(env["enqueued"]) == 2
        events = await list_run_stream_events(first.run_id)
        assert any(
            event["event_type"] == "end" and event["payload"]["payload"]["status"] == "cancelled" for event in events
        )
    finally:
        await clear_pending_confirm(env["thread_id"])
