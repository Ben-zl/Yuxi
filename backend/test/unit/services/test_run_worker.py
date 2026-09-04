"""ARQ AgentRun worker 边界测试。"""

import asyncio
from unittest.mock import AsyncMock

from yuxi.services import run_worker


async def test_process_agent_run_timeout_finishes_run_with_fresh_transaction(monkeypatch):
    """内层明确超时必须进入远端中断与终态收束流程。"""
    execute = AsyncMock(side_effect=TimeoutError)
    finish = AsyncMock()
    monkeypatch.setattr(
        "yuxi.agentscope.worker_job.execute_agent_run_job",
        execute,
    )
    monkeypatch.setattr(
        "yuxi.agentscope.worker_job.fail_agent_run_by_id",
        finish,
    )

    await run_worker.process_agent_run({}, "run-1")

    execute.assert_awaited_once_with("run-1")
    finish.assert_awaited_once_with("run-1", "执行超过最长运行时间")


async def test_process_agent_run_cancellation_is_left_for_arq_retry(monkeypatch):
    """服务重载取消任务时不得提前把可重试 Run 判为失败。"""
    execute = AsyncMock(side_effect=asyncio.CancelledError())
    finish = AsyncMock()
    monkeypatch.setattr(
        "yuxi.agentscope.worker_job.execute_agent_run_job",
        execute,
    )
    monkeypatch.setattr(
        "yuxi.agentscope.worker_job.fail_agent_run_by_id",
        finish,
    )

    try:
        await run_worker.process_agent_run({}, "run-1")
    except asyncio.CancelledError:
        pass
    else:
        raise AssertionError("CancelledError must propagate to ARQ")

    finish.assert_not_awaited()
