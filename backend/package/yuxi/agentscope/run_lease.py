"""AgentScope Run lease 的进程内 heartbeat 生命周期。"""

from __future__ import annotations

import asyncio

from yuxi.repositories.agent_run_repository import AgentRunRepository
from yuxi.storage.postgres.manager import pg_manager
from yuxi.utils import logger

RUN_LEASE_SECONDS = 120
RUN_HEARTBEAT_SECONDS = 30

_heartbeat_tasks: dict[str, asyncio.Task[None]] = {}


async def renew_run_lease(
    run_id: str,
    *,
    worker_id: str,
    lease_seconds: float = RUN_LEASE_SECONDS,
    heartbeat_seconds: float = RUN_HEARTBEAT_SECONDS,
) -> None:
    """周期续租一个已由当前 worker 持有的 Run。"""

    while True:
        await asyncio.sleep(heartbeat_seconds)
        async with pg_manager.get_async_session_context() as db:
            renewed = await AgentRunRepository(db).renew_lease(
                run_id,
                worker_id=worker_id,
                lease_seconds=lease_seconds,
            )
            if renewed:
                await db.commit()
        if not renewed:
            logger.warning(f"AgentScope Run lease lost: run={run_id} worker={worker_id}")
            return


def start_run_lease_heartbeat(
    run_id: str,
    *,
    worker_id: str,
    lease_seconds: float = RUN_LEASE_SECONDS,
    heartbeat_seconds: float = RUN_HEARTBEAT_SECONDS,
) -> asyncio.Task[None]:
    """幂等启动 Run heartbeat；同一进程内一个 Run 只保留一个任务。"""

    existing = _heartbeat_tasks.get(run_id)
    if existing is not None and not existing.done():
        return existing

    task = asyncio.create_task(
        renew_run_lease(
            run_id,
            worker_id=worker_id,
            lease_seconds=lease_seconds,
            heartbeat_seconds=heartbeat_seconds,
        )
    )
    _heartbeat_tasks[run_id] = task
    task.add_done_callback(lambda completed: _heartbeat_tasks.pop(run_id, None) if completed is task else None)
    return task


async def stop_run_lease_heartbeat(run_id: str) -> None:
    """停止并回收指定 Run 的 heartbeat。"""

    task = _heartbeat_tasks.pop(run_id, None)
    if task is None:
        return
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
