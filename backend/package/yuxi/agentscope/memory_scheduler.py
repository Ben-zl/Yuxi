"""Yuxi 持久化 ReMe Dream 调度器。"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from yuxi.agentscope.config_projection import project_runtime
from yuxi.agentscope.memory import (
    MemoryScopeBusyError,
    build_memory_models,
    memory_workspace_path,
    reme_maintenance_app,
)
from yuxi.config import UserConfig
from yuxi.repositories.agent_memory_scope_repository import AgentMemoryScopeRepository
from yuxi.storage.postgres.manager import pg_manager
from yuxi.utils import logger
from yuxi.utils.datetime_utils import utc_now


class MemoryDreamScheduler:
    """按 catalog 补跑 Daily→Digest Dream，并把进度持久化到 PostgreSQL。"""

    def __init__(
        self,
        *,
        registry,
        base_dir: str | Path,
        scan_interval_seconds: float = 900,
        concurrency: int = 2,
        job_timeout_seconds: float = 900,
    ) -> None:
        self.registry = registry
        self.base_dir = Path(base_dir)
        self.scan_interval_seconds = scan_interval_seconds
        self.job_timeout_seconds = job_timeout_seconds
        self._semaphore = asyncio.Semaphore(concurrency)
        self._task: asyncio.Task | None = None
        self._stopping = asyncio.Event()
        self.timezone = ZoneInfo("Asia/Shanghai")

    async def start(self) -> None:
        """启动单进程调度循环。"""
        if self._task is None:
            self._stopping.clear()
            self._task = asyncio.create_task(self._run_loop(), name="yuxi-reme-dream")

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
        """启动即扫描，之后每 15 分钟重复。"""
        while not self._stopping.is_set():
            try:
                await self.run_due_once()
            except Exception as exc:  # noqa: BLE001 - 单次扫描不能终止调度器
                logger.exception("ReMe Dream scan failed: %s", exc)
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=self.scan_interval_seconds)
            except TimeoutError:
                pass

    def _target_date(self, now: datetime) -> date:
        """23:15 后允许处理当天，否则只处理昨天及更早日期。"""
        return now.date() if now.time() >= time(23, 15) else now.date() - timedelta(days=1)

    def _due_dates(self, record, target: date) -> list[date]:
        """计算一个 scope 本轮最多七个待补 Dream 日期。"""
        start = (record.last_dream_date + timedelta(days=1)) if record.last_dream_date else None
        if start is None and record.last_memory_at:
            memory_at = record.last_memory_at
            if memory_at.tzinfo is None:
                memory_at = memory_at.replace(tzinfo=ZoneInfo("UTC"))
            start = memory_at.astimezone(self.timezone).date()
        if start is None or start > target:
            return []
        return [start + timedelta(days=offset) for offset in range(min(7, (target - start).days + 1))]

    async def run_due_once(self, now: datetime | None = None) -> None:
        """扫描一次全部 scope；每个 scope 最多补跑七天。"""
        local_now = now.astimezone(self.timezone) if now else datetime.now(self.timezone)
        async with pg_manager.get_async_session_context() as db:
            records = await AgentMemoryScopeRepository(db).list_all()
            due = []
            for record in records:
                if not (await UserConfig.load(db, record.uid)).schema.enable_memory:
                    continue
                if (
                    record.dream_status == "failed"
                    and record.dream_attempted_at
                    and utc_now() - self._as_utc(record.dream_attempted_at) < timedelta(hours=1)
                ):
                    continue
                target = self._target_date(local_now)
                dates = self._due_dates(record, target)
                if not dates:
                    continue
                due.append((record.uid, record.agent_slug, dates))
        await asyncio.gather(*(self._process_scope(uid, slug, dates) for uid, slug, dates in due))

    async def _process_scope(self, uid: str, agent_slug: str, dates: list[date]) -> None:
        """独占一个 scope 顺序执行待补日期。"""
        async with self._semaphore:
            if await self.registry.is_busy(uid, agent_slug):
                return
            try:
                async with self.registry.acquire_exclusive(uid, agent_slug):
                    async with pg_manager.get_async_session_context() as db:
                        projection = await project_runtime(
                            db,
                            uid=uid,
                            agent_slug=agent_slug,
                            include_memory_models=True,
                        )
                    chat_model, embedding_model, _fingerprint = build_memory_models(projection)
                    workspace = memory_workspace_path(self.base_dir, uid, agent_slug)
                    async with reme_maintenance_app(
                        workspace_dir=workspace,
                        chat_model=chat_model,
                        embedding_model=embedding_model,
                    ) as maintenance:
                        for dream_date in dates:
                            await self._run_date(uid, agent_slug, dream_date, maintenance)
            except MemoryScopeBusyError:
                return
            except Exception as exc:  # noqa: BLE001
                await self._record_result(uid, agent_slug, "failed", error=str(exc)[:2000])

    async def _run_date(self, uid: str, agent_slug: str, dream_date: date, maintenance) -> None:
        """执行一个日期，成功才推进 last_dream_date。"""
        response = await asyncio.wait_for(
            maintenance.run_job("auto_dream", date=dream_date.isoformat()),
            timeout=self.job_timeout_seconds,
        )
        if getattr(response, "success", True) is False:
            raise RuntimeError(str(getattr(response, "answer", "Dream failed")))
        await self._record_result(uid, agent_slug, "completed", dream_date=dream_date)

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        """把 PostgreSQL 时间归一为 aware UTC；旧 naive 值按 UTC 解释。"""
        utc = ZoneInfo("UTC")
        return value.replace(tzinfo=utc) if value.tzinfo is None else value.astimezone(utc)

    async def _record_result(
        self,
        uid: str,
        agent_slug: str,
        status: str,
        *,
        dream_date: date | None = None,
        error: str | None = None,
    ) -> None:
        """持久化一次 Dream 结果。"""
        async with pg_manager.get_async_session_context() as db:
            repo = AgentMemoryScopeRepository(db)
            record = await repo.get(uid, agent_slug)
            if record is None:
                return
            await repo.set_dream_result(
                record,
                attempted_at=utc_now(),
                status=status,
                dream_date=dream_date,
                error=error,
            )
            await db.commit()
