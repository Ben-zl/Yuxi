"""ARQ worker 入口（agentscope 执行体，迁移工单 14② 网关翻转）。

旧的 LangGraph 执行路径已被 yuxi.agentscope.worker_job 取代：队列任务
从 AgentRun 列与输入消息恢复参数，经 agentscope 会话服务执行并回写
yuxi 消息表与 Run 事件流。旧栈管理面（Skills/MCP/agent_state 视图）的
清退计划见迁移工单 14。
"""

import asyncio
import os

from yuxi.agents.mcp.service import ensure_builtin_mcp_servers_in_db
from yuxi.agents.skills.service import init_builtin_skills
from yuxi.config import config as sys_config
from yuxi.services.agent_request_queue_service import recover_pending_dispatches
from yuxi.storage.postgres.manager import pg_manager
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


async def _worker_startup(ctx):
    """初始化 worker 依赖。"""
    pg_manager.initialize()
    await pg_manager.create_business_tables()
    await pg_manager.ensure_business_schema()
    await ensure_builtin_mcp_servers_in_db()
    async with pg_manager.get_async_session_context() as session:
        await init_builtin_skills(session)
        from yuxi.config.options import ensure_options_in_db

        await ensure_options_in_db(session)
    sys_config.start_runtime_sync()
    from yuxi.agentscope.client import AgentScopeServiceClient
    from yuxi.agentscope.recovery import reconcile_stale_running_runs

    recovered = await reconcile_stale_running_runs(
        AgentScopeServiceClient(os.getenv("AGENTSCOPE_BASE_URL", "http://agentscope:8100"))
    )
    if recovered:
        logger.warning("Recovered %s stale AgentScope run(s) from persisted replies", recovered)
    await recover_pending_dispatches()


async def _worker_shutdown(ctx):
    """释放 worker 依赖。

    runtime sync 线程为 daemon（cache.py 无 stop API），随进程退出结束。
    """
    await pg_manager.close()


PROCESS_AGENT_RUN_TIMEOUT_SECONDS = int(os.getenv("YUXI_JOB_TIMEOUT_SECONDS", "3600"))


class WorkerSettings:
    functions = [process_agent_run]
    cron_jobs = [_agent_task_scan_job()]
    max_tries = 2
    retry_jobs = True
    health_check_interval = 15
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
