"""取消期间仍等待补偿任务结束。"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any


async def await_cleanup_completion[T](cleanup: Coroutine[Any, Any, T]) -> T:
    """即使外层再次取消，也等待独立补偿任务取得终态。"""
    task = asyncio.create_task(cleanup)
    while True:
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.done():
                return task.result()
