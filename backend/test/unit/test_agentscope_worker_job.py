"""AgentScope worker Run 终态收束测试。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from yuxi.agentscope import worker_job
from yuxi.services import run_queue_service
from yuxi.services.agent_run_manifest_service import compute_manifest_fingerprint


MANIFEST = {"manifest_version": 1, "resource_snapshot": {"id": "submitted-snapshot", "fingerprint": "snapshot-digest"}}


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

    await worker_job._fail_run(
        db,
        run_repo,
        run_id=run.id,
        uid=run.uid,
        agent_slug=run.agent_slug,
        thread_id=run.conversation_thread_id,
        input_message_id=None,
        worker_id=None,
        message="执行失败: timeout",
    )

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
    sync_delivery.assert_awaited_once_with(
        db,
        input_message_id=None,
        run_status="cancelled",
    )
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

    assert (
        await worker_job._fail_run(
            db,
            run_repo,
            run_id=run.id,
            uid=run.uid,
            agent_slug=run.agent_slug,
            thread_id=run.conversation_thread_id,
            input_message_id=None,
            worker_id=None,
            message="timeout",
        )
        is False
    )

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
        manifest=MANIFEST.copy(),
        manifest_fingerprint=compute_manifest_fingerprint(MANIFEST),
    )
    run_repo = SimpleNamespace(
        get_run=AsyncMock(return_value=run),
        mark_running=AsyncMock(return_value=(run, True)),
    )
    conv_repo = SimpleNamespace(
        get_message_by_id=AsyncMock(
            return_value=SimpleNamespace(content="hello", extra_metadata={}, image_content=None)
        ),
    )
    db = SimpleNamespace(
        scalar=AsyncMock(return_value=SimpleNamespace(uid="u")),
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )

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
        "load_run_resources",
        AsyncMock(return_value=SimpleNamespace()),
    )
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
        run_id="run-1",
        uid="u",
        agent_slug="agent-1",
        thread_id="thread-1",
        input_message_id=1,
        worker_id=worker_job.WORKER_ID,
        message="执行失败: database aborted",
    )


@pytest.mark.parametrize(
    "manifest,fingerprint,error",
    [
        (None, None, "Run 缺少提交时固化的运行清单"),
        (MANIFEST, None, "Run 缺少提交时固化的运行清单"),
        (
            {**MANIFEST, "resource_snapshot": {"id": "changed", "fingerprint": "snapshot-digest"}},
            compute_manifest_fingerprint(MANIFEST),
            "Run 运行清单指纹不一致",
        ),
    ],
)
async def test_manifest_failure_prevents_agentscope_execution(monkeypatch, manifest, fingerprint, error):
    """manifest 未成为持久事实时不得启动 AgentScope Session 或模型执行。"""
    run = SimpleNamespace(
        id="run-manifest",
        uid="u",
        status="pending",
        input_message_id=1,
        run_type="chat",
        input_payload={},
        conversation_id=1,
        request_id="request-manifest",
        conversation_thread_id="thread-manifest",
        agent_slug="agent-manifest",
        worker_id=worker_job.WORKER_ID,
        manifest=manifest,
        manifest_fingerprint=fingerprint,
    )
    run_repo = SimpleNamespace(
        get_run=AsyncMock(return_value=run),
        mark_running=AsyncMock(return_value=(run, True)),
    )
    conv_repo = SimpleNamespace(
        get_message_by_id=AsyncMock(
            return_value=SimpleNamespace(content="hello", extra_metadata={}, image_content=None)
        ),
    )
    db = SimpleNamespace(
        scalar=AsyncMock(return_value=SimpleNamespace(uid="u")),
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )

    class _SessionContext:
        async def __aenter__(self):
            return db

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    monkeypatch.setattr(worker_job.pg_manager, "get_async_session_context", lambda: _SessionContext())
    monkeypatch.setattr(worker_job, "AgentRunRepository", lambda _db: run_repo)
    monkeypatch.setattr(worker_job, "ConversationRepository", lambda _db: conv_repo)
    load_resources = AsyncMock()
    monkeypatch.setattr(worker_job, "load_run_resources", load_resources)
    ensure_session = AsyncMock()
    execute = AsyncMock()
    fail_run = AsyncMock()
    monkeypatch.setattr(worker_job, "ensure_thread_session", ensure_session)
    monkeypatch.setattr(worker_job, "execute_run", execute)
    monkeypatch.setattr(worker_job, "_fail_run", fail_run)

    await worker_job.execute_agent_run_job(run.id)

    db.commit.assert_awaited_once()
    db.rollback.assert_awaited_once()
    ensure_session.assert_not_awaited()
    execute.assert_not_awaited()
    load_resources.assert_not_awaited()
    fail_run.assert_awaited_once_with(
        db,
        run_repo,
        run_id="run-manifest",
        uid="u",
        agent_slug="agent-manifest",
        thread_id="thread-manifest",
        input_message_id=1,
        worker_id=worker_job.WORKER_ID,
        message=f"运行清单固化失败: {error}",
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
        agent_slug="agent",
        conversation_thread_id="thread",
        input_message_id=1,
        worker_id="worker",
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

    async def _finish(_db, _repo, **kwargs):
        order.append(("finish", kwargs["run_id"]))

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


async def test_manifest_validation_loads_persisted_snapshot_without_writing(monkeypatch):
    """重复投递只验证已有清单并加载其精确快照，不重新固化配置。"""
    run = SimpleNamespace(
        id="run-manifest",
        uid="u",
        agent_slug="agent",
        manifest=MANIFEST.copy(),
        manifest_fingerprint=compute_manifest_fingerprint(MANIFEST),
    )
    db = SimpleNamespace(commit=AsyncMock())
    repo = SimpleNamespace(record_run_manifest=AsyncMock())
    load_resources = AsyncMock()
    monkeypatch.setattr(worker_job, "load_run_resources", load_resources)

    await worker_job._record_run_manifest(db, repo, run, worker_id="worker-1")

    load_resources.assert_awaited_once_with(db, uid="u", manifest=MANIFEST, agent_slug="agent")
    assert load_resources.await_args.kwargs["manifest"] is run.manifest
    assert run.manifest == MANIFEST
    assert run.manifest_fingerprint == compute_manifest_fingerprint(MANIFEST)
    repo.record_run_manifest.assert_not_awaited()
    db.commit.assert_not_awaited()
