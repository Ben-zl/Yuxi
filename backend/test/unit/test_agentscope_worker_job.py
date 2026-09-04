"""AgentScope worker Run 终态收束测试。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from yuxi.agentscope import worker_job
from yuxi.services import run_queue_service


async def test_fail_run_with_cancel_signal_finishes_as_cancelled(monkeypatch):
    """异常路径收到用户取消时，不得把 Run 错误标记为失败。"""
    run = SimpleNamespace(
        id="run-1",
        uid="u",
        agent_slug="agent",
        conversation_thread_id="thread-1",
    )
    run_repo = SimpleNamespace(set_terminal_status=AsyncMock(return_value=(SimpleNamespace(status="cancelled"), True)))
    order = []
    db = SimpleNamespace(commit=AsyncMock(side_effect=lambda: order.append("commit")))

    monkeypatch.setattr(run_queue_service, "has_cancel_signal", AsyncMock(return_value=True))
    clear_cancel = AsyncMock()
    monkeypatch.setattr(run_queue_service, "clear_cancel_signal", clear_cancel)
    emit_end = AsyncMock(side_effect=lambda *_args, **_kwargs: order.append("end"))
    monkeypatch.setattr(worker_job, "_emit_end_event", emit_end)
    sync_delivery = AsyncMock()
    monkeypatch.setattr(worker_job, "_sync_input_delivery_status", sync_delivery)
    dispatch_next = AsyncMock()
    monkeypatch.setattr(worker_job, "dispatch_next_request", dispatch_next)
    notify_task = AsyncMock()
    monkeypatch.setattr(worker_job, "_notify_agent_task", notify_task)

    await worker_job._fail_run(db, run_repo, run, "执行失败: timeout")

    run_repo.set_terminal_status.assert_awaited_once_with(
        "run-1",
        status="cancelled",
        error_message=None,
    )
    emit_end.assert_awaited_once_with(
        "run-1",
        "thread-1",
        {"status": "cancelled"},
    )
    sync_delivery.assert_awaited_once_with(db, run, "cancelled")
    clear_cancel.assert_awaited_once_with("run-1")
    dispatch_next.assert_awaited_once_with(
        uid="u",
        agent_slug="agent",
        thread_id="thread-1",
    )
    notify_task.assert_awaited_once_with("run-1", "cancelled")
    assert order == ["commit", "end"]


async def test_fail_run_loser_has_no_terminal_side_effects(monkeypatch):
    """正常完成或恢复已获胜时，超时失败方不得覆盖消息和事件状态。"""
    run = SimpleNamespace(
        id="run-1",
        uid="u",
        agent_slug="agent",
        conversation_thread_id="thread-1",
    )
    completed = SimpleNamespace(status="completed")
    run_repo = SimpleNamespace(set_terminal_status=AsyncMock(return_value=(completed, False)))
    db = SimpleNamespace(commit=AsyncMock())
    monkeypatch.setattr(run_queue_service, "has_cancel_signal", AsyncMock(return_value=False))
    emit_end = AsyncMock()
    monkeypatch.setattr(worker_job, "_emit_end_event", emit_end)
    sync_delivery = AsyncMock()
    monkeypatch.setattr(worker_job, "_sync_input_delivery_status", sync_delivery)
    dispatch_next = AsyncMock()
    monkeypatch.setattr(worker_job, "dispatch_next_request", dispatch_next)
    notify_task = AsyncMock()
    monkeypatch.setattr(worker_job, "_notify_agent_task", notify_task)

    assert await worker_job._fail_run(db, run_repo, run, "timeout") is False

    db.commit.assert_not_awaited()
    emit_end.assert_not_awaited()
    sync_delivery.assert_not_awaited()
    dispatch_next.assert_not_awaited()
    notify_task.assert_not_awaited()


async def test_worker_error_rolls_back_before_finishing_run(monkeypatch):
    """业务事务失败后必须先 rollback，再用同一 session 写 Run 终态。"""
    run = SimpleNamespace(
        id="run-1",
        uid="u",
        status="pending",
        input_message_id=1,
        run_type="chat",
        input_payload={},
        conversation_id=1,
        request_id="request-1",
        conversation_thread_id="thread-1",
        agent_slug="agent-1",
    )
    run_repo = SimpleNamespace(
        get_run=AsyncMock(return_value=run),
        mark_running=AsyncMock(),
    )
    conv_repo = SimpleNamespace(
        get_message_by_id=AsyncMock(
            return_value=SimpleNamespace(content="hello", extra_metadata={}, image_content=None)
        ),
    )
    db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())

    class _SessionContext:
        async def __aenter__(self):
            return db

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    monkeypatch.setattr(worker_job.pg_manager, "get_async_session_context", lambda: _SessionContext())
    monkeypatch.setattr(worker_job, "AgentRunRepository", lambda _db: run_repo)
    monkeypatch.setattr(worker_job, "ConversationRepository", lambda _db: conv_repo)
    monkeypatch.setattr(
        worker_job,
        "ensure_thread_session",
        AsyncMock(
            return_value=SimpleNamespace(
                agentscope_agent_id="agent-id",
                agentscope_session_id="session-id",
            )
        ),
    )
    monkeypatch.setattr(worker_job, "_materialize_run_attachments", AsyncMock(return_value="hello"))
    monkeypatch.setattr(worker_job, "_apply_permission_mode", AsyncMock())
    monkeypatch.setattr(worker_job, "execute_run", AsyncMock(side_effect=RuntimeError("database aborted")))
    fail_run = AsyncMock()
    monkeypatch.setattr(worker_job, "_fail_run", fail_run)

    await worker_job.execute_agent_run_job("run-1")

    db.rollback.assert_awaited_once()
    fail_run.assert_awaited_once_with(
        db,
        run_repo,
        run,
        "执行失败: database aborted",
    )


async def test_timeout_interrupts_remote_sessions_before_finishing_run(monkeypatch):
    """超时必须先确认 parent/child Session 停止，再写本地终态。"""
    from contextlib import asynccontextmanager

    from yuxi.repositories import agentscope_team_workers, agentscope_thread_sessions

    order = []
    run = SimpleNamespace(
        id="parent-run",
        uid="u",
        status="running",
        conversation_thread_id="thread",
    )
    child = SimpleNamespace(id="child-run")
    first_repo = SimpleNamespace(
        get_run=AsyncMock(return_value=run),
        list_active_child_runs_for_user=AsyncMock(return_value=[child]),
    )
    second_repo = SimpleNamespace(get_run=AsyncMock(return_value=run))
    repos = iter((first_repo, second_repo))
    monkeypatch.setattr(worker_job, "AgentRunRepository", lambda _db: next(repos))

    @asynccontextmanager
    async def _db_context():
        yield SimpleNamespace()

    monkeypatch.setattr(worker_job.pg_manager, "get_async_session_context", _db_context)
    monkeypatch.setattr(
        agentscope_thread_sessions,
        "get_thread_session",
        AsyncMock(
            return_value=SimpleNamespace(
                agentscope_agent_id="parent-agent",
                agentscope_session_id="parent-session",
            )
        ),
    )
    monkeypatch.setattr(
        agentscope_team_workers,
        "AgentScopeTeamWorkerRepository",
        lambda _db: SimpleNamespace(
            list_for_active_runs=AsyncMock(
                return_value=[
                    SimpleNamespace(
                        worker_agent_id="child-agent",
                        worker_session_id="child-session",
                    )
                ]
            )
        ),
    )

    class _Client:
        async def interrupt_session(self, _uid, _agent_id, session_id):
            order.append(("interrupt", session_id))

        async def get_session_status(self, _uid, _agent_id, session_id):
            order.append(("status", session_id))
            return "idle"

    monkeypatch.setattr(worker_job, "AgentScopeServiceClient", lambda _url, **_kwargs: _Client())
    from yuxi.agentscope import recovery

    async def _reconcile(_client, *, parent_run_id, uid):
        order.append(("reconcile", parent_run_id, uid))
        return False

    monkeypatch.setattr(recovery, "reconcile_active_team_child_runs", _reconcile)

    async def _finish_children(*, parent_run_id, uid):
        order.append(("finish-children", parent_run_id, uid))

    monkeypatch.setattr(worker_job, "_fail_remaining_timeout_children", _finish_children)

    async def _finish(_db, _repo, _run, _message):
        order.append(("finish", _run.id))

    monkeypatch.setattr(worker_job, "_fail_run", _finish)

    await worker_job.fail_agent_run_by_id("parent-run", "timeout")

    assert order == [
        ("interrupt", "parent-session"),
        ("interrupt", "child-session"),
        ("status", "parent-session"),
        ("status", "child-session"),
        ("reconcile", "parent-run", "u"),
        ("finish-children", "parent-run", "u"),
        ("finish", "parent-run"),
    ]
