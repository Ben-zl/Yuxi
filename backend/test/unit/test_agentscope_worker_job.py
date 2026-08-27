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
    run_repo = SimpleNamespace(set_terminal_status=AsyncMock())
    db = SimpleNamespace(commit=AsyncMock())

    monkeypatch.setattr(run_queue_service, "has_cancel_signal", AsyncMock(return_value=True))
    clear_cancel = AsyncMock()
    monkeypatch.setattr(run_queue_service, "clear_cancel_signal", clear_cancel)
    emit_end = AsyncMock()
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
