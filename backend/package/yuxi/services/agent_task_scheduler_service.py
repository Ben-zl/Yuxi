"""AgentTask 定时扫描（工单 07/08）：到期领取、合并、宽限与 missed。

ARQ Worker 周期调用 scan_due：行锁领取到期任务 → 幂等键派生自
task_id + scheduled_at → 触发服务负责 skipped 合并；停机恢复只补发
15 分钟宽限期内最近一次，其余记 missed。DST 语义（春跳过、秋一次）
由 APScheduler CronTrigger 的时区计算承担。
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from yuxi.repositories.agent_task_repository import (
    AgentTaskRepository,
    TaskExecutionRepository,
)
from yuxi.services.agent_task_dispatcher import AgentTaskDispatcher
from yuxi.services.agent_task_schedule_service import (
    compute_next_run,
    schedule_idempotency_key,
)
from yuxi.storage.postgres.models_business import AgentTask, User
from yuxi.utils.datetime_utils import utc_now_naive
from yuxi.utils.ids import new_uuid
from yuxi.utils.logging_config import logger

SCAN_INTERVAL_SECONDS = 60
RECOVERY_GRACE = timedelta(minutes=15)


class AgentTaskSchedulerService:
    """到期任务扫描与触发；每实例周期调用，行锁保证并发安全。"""

    def __init__(self, db):
        self.db = db
        self.tasks = AgentTaskRepository(db)
        self.executions = TaskExecutionRepository(db)

    async def _owner(self, owner_uid: str) -> User | None:
        from yuxi.repositories.user_repository import UserRepository

        user = await UserRepository(self.db).get_by_uid(owner_uid)
        if user is None or user.is_deleted:
            return None
        return user

    async def scan_due(self) -> dict[str, int]:
        """扫描一轮：触发到期任务 + 记录 missed + 回写 next_run_at。"""
        now = utc_now_naive()
        result = await self.db.execute(
            select(AgentTask)
            .where(
                AgentTask.enabled.is_(True),
                AgentTask.archived_at.is_(None),
                AgentTask.schedule_mode.isnot(None),
                AgentTask.next_run_at.isnot(None),
                AgentTask.next_run_at <= now,
            )
            .limit(200)
        )
        triggered = skipped = missed = 0
        for task in result.scalars().all():
            task_locked = await self.tasks.get_for_update(task.id)
            if task_locked is None or not task_locked.enabled or task_locked.archived_at is not None:
                continue
            scheduled_at = task_locked.next_run_at
            counts = await self._fire(task_locked, scheduled_at, now)
            triggered += counts["triggered"]
            skipped += counts["skipped"]
            missed += counts["missed"]
            await self._advance_with_missed(task_locked, scheduled_at, now)
        await self.db.commit()
        return {"triggered": triggered, "skipped": skipped, "missed": missed}

    async def _fire(self, task: AgentTask, scheduled_at, now) -> dict[str, int]:
        """触发一次到期；宽限外的历史周期记 missed。"""
        from yuxi.services.agent_task_trigger_service import AgentTaskTriggerService

        counts = {"triggered": 0, "skipped": 0, "missed": 0}
        owner = await self._owner(task.owner_uid)
        if owner is None:
            logger.warning(f"定时任务所有者不存在 task={task.id}")
            return counts

        if scheduled_at is not None and now - scheduled_at > RECOVERY_GRACE:
            # 宽限窗外：不补发陈旧周期，记 missed（工单 08）
            await self._record_missed(task, owner, scheduled_at)
            counts["missed"] = 1
            return counts

        service = AgentTaskTriggerService(self.db)
        execution, created = await service.trigger(
            task_id=task.id,
            user=owner,
            trigger_type="schedule",
            idempotency_key=schedule_idempotency_key(
                task.id, scheduled_at, task.schedule_timezone or "UTC"
            ),
            scheduled_at=scheduled_at,
        )
        counts["triggered"] = 1 if created and execution.status == "queued" else 0
        counts["skipped"] = 1 if created and execution.status == "skipped" else 0
        return counts

    async def _record_missed(self, task: AgentTask, owner: User, scheduled_at) -> None:
        await self.executions.create(
            id=new_uuid(),
            task_id=task.id,
            trigger_type="schedule",
            triggered_by_uid=str(owner.uid),
            execution_principal_uid=str(owner.uid),
            agent_id=task.agent_id,
            agent_slug=task.agent_slug_snapshot or "",
            prompt=task.prompt,
            tool_approval_mode=task.tool_approval_mode,
            scheduled_at=scheduled_at,
            idempotency_key=f"missed:{task.id}:{scheduled_at.isoformat()}",
            status="missed",
            finished_at=utc_now_naive(),
        )

    async def _advance_with_missed(self, task: AgentTask, fired_at, now) -> None:
        """推进 next_run_at 并逐次记录宽限窗外的过期周期为 missed。

        停机恢复场景中，next_run_at 可能远落后于 now；只补发最近一次（_fire
        已处理），其余过期周期逐次记 missed（工单 08 逐次记录）。
        """
        if not task.schedule_cron or not task.schedule_timezone:
            task.next_run_at = None
            return
        owner = await self._owner(task.owner_uid)
        try:
            next_run = compute_next_run(task.schedule_cron, task.schedule_timezone, after=fired_at)
        except Exception as exc:  # noqa: BLE001 - 非法规则停摆该任务定时
            logger.warning(f"计算 next_run 失败 task={task.id}: {exc}")
            task.next_run_at = None
            return
        # 逐次记录宽限窗外、到 next_run 之间的过期周期
        while owner is not None and next_run < now and (now - next_run) > RECOVERY_GRACE:
            await self._record_missed(task, owner, next_run)
            try:
                next_run = compute_next_run(task.schedule_cron, task.schedule_timezone, after=next_run)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"计算 next_run 失败 task={task.id}: {exc}")
                task.next_run_at = None
                return
        task.next_run_at = next_run


async def scan_agent_task_schedules(ctx=None) -> None:
    """ARQ 周期入口：扫描到期任务 + 崩溃恢复（工单 11 兜底）。"""
    from yuxi.storage.postgres.manager import pg_manager

    async with pg_manager.get_async_session_context() as db:
        scheduler = AgentTaskSchedulerService(db)
        try:
            counts = await scheduler.scan_due()
            if counts["triggered"] or counts["skipped"] or counts["missed"]:
                logger.info(f"任务定时扫描: {counts}")
        except Exception as exc:  # noqa: BLE001 - 扫描失败等待下轮
            logger.warning(f"任务定时扫描失败: {exc}")
        try:
            recovered = await AgentTaskDispatcher(db).recover_stale(utc_now_naive() - timedelta(minutes=10))
            if recovered:
                logger.info(f"任务执行恢复: {recovered}")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"任务执行恢复失败: {exc}")
