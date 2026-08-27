"""开发环境 ARQ worker 热重载监督器。"""

from __future__ import annotations

import asyncio
import signal
import sys
from collections.abc import Awaitable
from contextlib import suppress
from typing import Any, Literal

from watchfiles import PythonFilter, awatch

WORKER_COMMAND = ("arq", "server.worker_main.WorkerSettings")
WATCH_PATHS = ("/app/server", "/app/package")
RESTART_DELAY_SECONDS = 2


async def wait_for_worker_event(
    process,
    changes: Awaitable[Any],
    stop: Awaitable[Any] | None = None,
) -> tuple[Literal["changed", "exited", "stopped"], int | None]:
    """等待 worker 退出、代码变化或容器停止中的第一个事件。"""
    process_task = asyncio.create_task(process.wait())
    changes_task = asyncio.create_task(changes)
    tasks = {process_task, changes_task}
    stop_task = asyncio.create_task(stop) if stop is not None else None
    if stop_task is not None:
        tasks.add(stop_task)

    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    for task in pending:
        with suppress(asyncio.CancelledError):
            await task

    if process_task in done:
        return "exited", process_task.result()
    if stop_task is not None and stop_task in done:
        return "stopped", None
    return "changed", None


async def _stop_worker(process) -> None:
    """优先让 ARQ 完成当前收尾，超时后再强制结束。"""
    if process.returncode is not None:
        return
    process.send_signal(signal.SIGINT)
    try:
        await asyncio.wait_for(process.wait(), timeout=10)
    except TimeoutError:
        process.kill()
        await process.wait()


async def supervise_worker() -> None:
    """持续监督 ARQ，并在代码变化或异常退出后重新启动。"""
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(signum, stop_event.set)

    while not stop_event.is_set():
        process = await asyncio.create_subprocess_exec(*WORKER_COMMAND)
        changes = awatch(
            *WATCH_PATHS,
            watch_filter=PythonFilter(),
            stop_event=stop_event,
        )
        try:
            event, exit_code = await wait_for_worker_event(
                process,
                anext(changes),
                stop_event.wait(),
            )
        finally:
            await changes.aclose()

        if event == "stopped":
            await _stop_worker(process)
            return
        if event == "changed":
            print("Python files changed, restarting ARQ worker...", flush=True)
            await _stop_worker(process)
            continue

        print(
            f"ARQ worker exited with code {exit_code}; restarting in {RESTART_DELAY_SECONDS}s...",
            file=sys.stderr,
            flush=True,
        )
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=RESTART_DELAY_SECONDS)
        except TimeoutError:
            pass


def main() -> None:
    """运行开发 worker 监督器。"""
    asyncio.run(supervise_worker())


if __name__ == "__main__":
    main()
