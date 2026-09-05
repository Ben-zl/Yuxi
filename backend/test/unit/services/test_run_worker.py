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


async def test_reconciliation_health_publishes_bounded_lease(monkeypatch):
    """worker 启动和周期收敛必须发布 readiness 所需的续租事实。"""
    redis = AsyncMock()

    async def get_redis():
        return redis

    monkeypatch.setattr(run_worker, "get_redis_client", get_redis)

    await run_worker._publish_reconciliation_health()

    redis.set.assert_awaited_once_with(
        run_worker.WORKER_RECONCILIATION_HEALTH_KEY,
        "healthy",
        ex=run_worker.WORKER_RECONCILIATION_HEALTH_TTL_SECONDS,
    )


def test_worker_settings_publish_arq_health_lease():
    """ARQ health lease 的刷新周期必须匹配 readiness 的 TTL 上限。"""
    assert run_worker.WorkerSettings.health_check_interval == run_worker.WORKER_HEALTH_INTERVAL_SECONDS
    assert run_worker.WorkerSettings.health_check_key == run_worker.WORKER_HEALTH_KEY
