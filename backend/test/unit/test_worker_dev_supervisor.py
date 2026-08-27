"""开发环境 ARQ worker 监督器测试。"""

import asyncio

from server.worker_dev import wait_for_worker_event


class _Process:
    """提供可控 wait 行为的最小子进程替身。"""

    def __init__(self, wait_for):
        self._wait_for = wait_for

    async def wait(self) -> int:
        return await self._wait_for()


async def test_wait_for_worker_event_observes_worker_exit() -> None:
    """ARQ startup 失败退出时监督器必须立即感知，不能遗留 zombie。"""

    async def worker_exit() -> int:
        return 1

    never_changed = asyncio.Event()

    event, exit_code = await wait_for_worker_event(
        _Process(worker_exit),
        never_changed.wait(),
    )

    assert event == "exited"
    assert exit_code == 1


async def test_wait_for_worker_event_observes_python_change() -> None:
    """Python 文件变化时监督器必须触发热重载。"""
    worker_stopped = asyncio.Event()

    async def worker_wait() -> int:
        await worker_stopped.wait()
        return 0

    async def changed() -> set[tuple[int, str]]:
        return {(2, "/app/package/yuxi/example.py")}

    event, exit_code = await wait_for_worker_event(
        _Process(worker_wait),
        changed(),
    )

    assert event == "changed"
    assert exit_code is None
