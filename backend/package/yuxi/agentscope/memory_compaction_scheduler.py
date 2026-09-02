"""Yuxi ReMe Daily 近重复归并调度器。"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from pathlib import Path

from yuxi.agentscope.memory import MemoryScopeBusyError, validate_memory_workspace
from yuxi.agentscope.memory_compaction import compact_daily_memories
from yuxi.config import UserConfig
from yuxi.repositories.agent_memory_scope_repository import AgentMemoryScopeRepository
from yuxi.storage.postgres.manager import pg_manager
from yuxi.utils import logger


class MemoryCompactionScheduler:
    """定期归并最近七天的高置信重复 Daily。"""

    def __init__(
        self,
        *,
        registry,
        memory_service,
        base_dir: str | Path,
        scan_interval_seconds: float = 300,
        concurrency: int = 2,
    ) -> None:
        self.registry = registry
        self.memory_service = memory_service
        self.base_dir = Path(base_dir)
        self.scan_interval_seconds = scan_interval_seconds
        self._semaphore = asyncio.Semaphore(concurrency)
        self._task: asyncio.Task | None = None
        self._stopping = asyncio.Event()

    async def start(self) -> None:
        """启动单进程归并循环。"""
        if self._task is None:
            self._stopping.clear()
            self._task = asyncio.create_task(self._run_loop(), name="yuxi-reme-compaction")

    async def stop(self) -> None:
        """停止调度并等待当前扫描退出。"""
        self._stopping.set()
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _run_loop(self) -> None:
        """启动即扫描，之后每五分钟重复。"""
        while not self._stopping.is_set():
            try:
                await self.run_once()
            except Exception as exc:  # noqa: BLE001 - 单 scope 之外的扫描错误不能终止调度器
                logger.exception("ReMe compaction scan failed: %s", exc)
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=self.scan_interval_seconds)
            except TimeoutError:
                pass

    async def run_once(self, *, today: date | None = None) -> None:
        """扫描一次启用 Memory 的 scope，并选择最近七天已有目录。"""
        current = today or date.today()
        earliest = current - timedelta(days=6)
        due = []
        async with pg_manager.get_async_session_context() as db:
            records = await AgentMemoryScopeRepository(db).list_all()
            for record in records:
                if not (await UserConfig.load(db, record.uid)).schema.enable_memory:
                    continue
                workspace = validate_memory_workspace(self.base_dir, record.uid, record.agent_slug)
                daily_root = workspace / "daily"
                dates = []
                if daily_root.is_dir() and not daily_root.is_symlink():
                    for day_dir in daily_root.iterdir():
                        if day_dir.is_symlink() or not day_dir.is_dir():
                            continue
                        try:
                            value = date.fromisoformat(day_dir.name)
                        except ValueError:
                            continue
                        if earliest <= value <= current:
                            dates.append(day_dir.name)
                if dates:
                    due.append((record.uid, record.agent_slug, sorted(dates)))
        await asyncio.gather(*(self._process_scope(uid, slug, dates) for uid, slug, dates in due))

    async def _process_scope(self, uid: str, agent_slug: str, dates: list[str]) -> None:
        """在独占租约内顺序归并一个 scope 的日期目录。"""
        async with self._semaphore:
            if await self.registry.is_busy(uid, agent_slug):
                return
            try:
                async with self.registry.acquire_exclusive(uid, agent_slug):
                    workspace = validate_memory_workspace(self.base_dir, uid, agent_slug)
                    if not workspace.is_dir():
                        return
                    for memory_date in dates:
                        await compact_daily_memories(
                            workspace,
                            date=memory_date,
                            maintain_index=lambda daily_date: self.memory_service.reindex_scope(
                                uid,
                                agent_slug,
                                workspace,
                                daily_date=daily_date,
                            ),
                        )
            except MemoryScopeBusyError:
                return
            except Exception as exc:  # noqa: BLE001 - 单 scope 失败等待下一轮重试
                logger.exception("ReMe compaction failed for %s/%s: %s", uid, agent_slug, exc)


__all__ = ["MemoryCompactionScheduler"]
