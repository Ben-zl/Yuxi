"""ARQ worker 入口（agentscope 执行体，迁移工单 14② 网关翻转）。

旧的 LangGraph 执行路径已被 yuxi.agentscope.worker_job 取代：队列任务
从 AgentRun 列与输入消息恢复参数，经 agentscope 会话服务执行并回写
yuxi 消息表与 Run 事件流。旧栈管理面（Skills/MCP/agent_state 视图）的
清退计划见迁移工单 14。
"""

import asyncio
import os
from dataclasses import dataclass
from datetime import datetime

from yuxi.agents.mcp.service import ensure_builtin_mcp_servers_in_db
from yuxi.agents.skills.service import init_builtin_skills
from yuxi.config import config as sys_config
from yuxi.config.runtime import lite_mode_enabled
from yuxi.services.agent_cleanup_service import reconcile_deleted_agent_memories
from yuxi.services.agent_request_queue_service import recover_pending_dispatches
from yuxi.repositories.agent_run_repository import AgentRunRepository
from yuxi.storage.postgres.manager import pg_manager
from yuxi.services.run_queue_service import (
    WORKER_HEALTH_INTERVAL_SECONDS,
    WORKER_HEALTH_KEY,
    WORKER_RECONCILIATION_HEALTH_KEY,
    WORKER_RECONCILIATION_HEALTH_TTL_SECONDS,
    get_redis_client,
)
from yuxi.storage.redis import get_arq_redis_settings
from yuxi.utils.logging_config import logger


def _agent_task_scan_job():
    """任务中心定时扫描（60 秒周期，工单 07/08/11）。"""
    from arq import cron

    from yuxi.services.agent_task_scheduler_service import scan_agent_task_schedules

    return cron(
        scan_agent_task_schedules,
        minute=set(range(60)),
        second={0},
        unique=True,
        max_tries=1,
    )


async def reconcile_deleted_agent_memories_job(ctx):
    """独立收敛已删除 Agent 的外部长期记忆，避免阻塞 Run lease。"""
    del ctx
    cleaned_agents = await reconcile_deleted_agent_memories()
    if cleaned_agents:
        logger.warning("Reconciled deleted Agent memory scope(s): %s", ",".join(cleaned_agents))


def _agent_cleanup_reconciliation_job():
    """每分钟执行一次唯一的 Agent memory 补偿任务。"""
    from arq import cron

    return cron(
        reconcile_deleted_agent_memories_job,
        minute=set(range(60)),
        second={15},
        unique=True,
        max_tries=1,
    )


async def process_agent_run(ctx, run_id: str):
    """执行队列中的 AgentRun（agentscope 执行体）。

    从 run 列与输入消息恢复参数，经 yuxi.agentscope.worker_job 执行：
    映射保障 → 网关协议转换写入 Run 事件流 → yuxi 消息落库 → 终态回写。
    """
    from yuxi.agentscope.worker_job import execute_agent_run_job, fail_agent_run_by_id

    try:
        async with asyncio.timeout(PROCESS_AGENT_RUN_TIMEOUT_SECONDS):
            await execute_agent_run_job(run_id)
    except TimeoutError:
        await fail_agent_run_by_id(
            run_id,
            "执行超过最长运行时间",
        )


PROCESS_AGENT_RUN_TIMEOUT_SECONDS = int(os.getenv("YUXI_JOB_TIMEOUT_SECONDS", "3600"))
RUN_LEASE_SECONDS = int(os.getenv("YUXI_RUN_LEASE_SECONDS", "120"))
RUN_RECONCILIATION_SECONDS = int(os.getenv("YUXI_RUN_RECONCILIATION_SECONDS", "30"))
_reconciliation_task: asyncio.Task | None = None


@dataclass(frozen=True)
class TerminalTransition:
    """记录一次 AgentRun 终态提交是否实际生效。"""

    status: str | None
    changed: bool


async def mark_run_running(run_id: str, worker_id: str) -> bool:
    """在独立事务中取得一个 Run 的 lease。"""

    async with pg_manager.get_async_session_context() as db:
        _, acquired = await AgentRunRepository(db).mark_running(
            run_id,
            worker_id=worker_id,
            lease_seconds=RUN_LEASE_SECONDS,
        )
        return acquired


async def renew_run_lease(run_id: str, worker_id: str) -> bool:
    """在独立事务中续租；owner 或 lease 失效时返回 False。"""

    async with pg_manager.get_async_session_context() as db:
        return await AgentRunRepository(db).renew_lease(
            run_id,
            worker_id=worker_id,
            lease_seconds=RUN_LEASE_SECONDS,
        )


async def release_run_lease_for_retry(run_id: str, worker_id: str) -> bool:
    """释放当前 attempt 的 lease，使重试创建新的 attempt 事实。"""

    async with pg_manager.get_async_session_context() as db:
        return await AgentRunRepository(db).release_lease_for_retry(run_id, worker_id=worker_id)


async def mark_run_terminal(
    run_id: str,
    status: str,
    error_type: str | None = None,
    error_message: str | None = None,
    token_usage: dict | None = None,
    worker_id: str | None = None,
    now: datetime | None = None,
) -> TerminalTransition:
    """在 PostgreSQL 中原子提交 Run 终态并收束其 AgentScope 子 Run。"""

    cancelled_descendants: list[tuple[str, str]] = []
    async with pg_manager.get_async_session_context() as db:
        repository = AgentRunRepository(db)
        run, changed = await repository.set_terminal_status(
            run_id,
            status=status,
            error_type=error_type,
            error_message=error_message,
            token_usage=token_usage,
            worker_id=worker_id,
            now=now,
        )
        if run is not None and changed:
            cancelled_descendants = await repository.cancel_active_execution_tree_descendants(run)
        persisted_status = run.status if run is not None else None

    if cancelled_descendants:
        from yuxi.services.run_queue_service import publish_cancel_signal

        await asyncio.gather(
            *(publish_cancel_signal(child_id) for child_id, _thread_id in cancelled_descendants),
            return_exceptions=True,
        )
    return TerminalTransition(status=persisted_status, changed=changed)


async def reconcile_expired_run_leases(*, now: datetime | None = None) -> list[str]:
    """周期性收敛失联的 AgentScope Run lease。"""

    async with pg_manager.get_async_session_context() as db:
        runs, cancelled_descendants = await AgentRunRepository(db).reconcile_expired_leases(now=now)

    if cancelled_descendants:
        from yuxi.services.run_queue_service import publish_cancel_signal

        await asyncio.gather(
            *(publish_cancel_signal(child_id) for child_id, _thread_id in cancelled_descendants),
            return_exceptions=True,
        )
    return [run.id for run in runs]


async def _reconcile_forever() -> None:
    """持续执行失联 Run 的数据库收敛。"""

    while True:
        try:
            await asyncio.sleep(RUN_RECONCILIATION_SECONDS)
            recovered = await reconcile_expired_run_leases()
            if recovered:
                logger.warning("Reconciled expired AgentScope Run lease(s): %s", ",".join(recovered))
            await _publish_reconciliation_health()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("AgentScope Run lease reconciliation failed")


async def _publish_reconciliation_health() -> None:
    """发布 lease reconciliation 的短 TTL 健康事实。"""

    redis = await get_redis_client()
    await redis.set(
        WORKER_RECONCILIATION_HEALTH_KEY,
        "healthy",
        ex=WORKER_RECONCILIATION_HEALTH_TTL_SECONDS,
    )


async def _worker_startup(ctx):
    """初始化 worker 依赖。"""
    pg_manager.initialize()
    await pg_manager.require_current_schema(include_knowledge=not lite_mode_enabled())
    await ensure_builtin_mcp_servers_in_db()
    async with pg_manager.get_async_session_context() as session:
        await init_builtin_skills(session)
        from yuxi.config.options import ensure_options_in_db

        await ensure_options_in_db(session)
    sys_config.start_runtime_sync()
    from yuxi.agentscope.client import AgentScopeServiceClient
    from yuxi.agentscope.recovery import reconcile_stale_running_runs

    agentscope_client = AgentScopeServiceClient(os.getenv("AGENTSCOPE_BASE_URL", "http://agentscope:8100"))
    recovered = await reconcile_stale_running_runs(agentscope_client)
    if recovered:
        logger.warning("Recovered %s stale AgentScope run(s) from persisted replies", recovered)
    cleaned_agents = await reconcile_deleted_agent_memories()
    if cleaned_agents:
        logger.warning("Reconciled deleted Agent memory scope(s) at startup: %s", ",".join(cleaned_agents))
    await recover_pending_dispatches()
    await _publish_reconciliation_health()
    global _reconciliation_task
    _reconciliation_task = asyncio.create_task(_reconcile_forever())


async def _worker_shutdown(ctx):
    """释放 worker 依赖。

    runtime sync 线程为 daemon（cache.py 无 stop API），随进程退出结束。
    """
    global _reconciliation_task
    if _reconciliation_task is not None:
        _reconciliation_task.cancel()
        await asyncio.gather(_reconciliation_task, return_exceptions=True)
        _reconciliation_task = None
    await pg_manager.close()


class WorkerSettings:
    functions = [process_agent_run]
    cron_jobs = [_agent_task_scan_job(), _agent_cleanup_reconciliation_job()]
    max_tries = 2
    retry_jobs = True
    health_check_interval = WORKER_HEALTH_INTERVAL_SECONDS
    health_check_key = WORKER_HEALTH_KEY
    # 单任务最长执行时间（秒），可配置：超长图谱构建/深度检索场景需调大，
    # 避免长任务被 arq 取消并误标为 cancelled。
    job_timeout = PROCESS_AGENT_RUN_TIMEOUT_SECONDS + 30
    keep_result = 60
    on_startup = _worker_startup
    on_shutdown = _worker_shutdown
    try:
        redis_settings = get_arq_redis_settings()
    except Exception:
        logger.warning("ARQ redis settings unavailable, worker may not start")
        redis_settings = None
