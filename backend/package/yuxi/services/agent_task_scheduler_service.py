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
from yuxi.storage.postgres.models_business import AgentTask
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

    async def _owner_context(self, owner_uid: str, department_id: int | None):
        """按任务固定部门解析所有者执行身份；无绑定或成员失效明确拒绝。"""
        from yuxi.repositories.user_repository import UserRepository
        from yuxi.services.department_context_service import DepartmentContextError, resolve_department_context

        user = await UserRepository(self.db).get_by_uid(owner_uid)
        if user is None or user.is_deleted:
            return None
        if department_id is None:
            return None
        try:
            return await resolve_department_context(self.db, user_id=user.id, department_id=department_id, strict=True)
        except DepartmentContextError:
            return None

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
        # 无绑定部门的旧任务不猜测归属：成员失效或无部门均拒绝派发
        actor = await self._owner_context(task.owner_uid, task.department_id)
        if actor is None:
            reason = self._unavailable_reason(task)
            logger.warning(
                f"定时任务执行身份不可用（{reason}）task={task.id} dept={task.department_id}"
            )
            # 失败事实落库：可查询的 failed 终态；幂等键与正常触发一致，不重复创建
            await self._record_failed(task, scheduled_at, reason)
            counts["skipped"] = 1
            return counts

        if scheduled_at is not None and now - scheduled_at > RECOVERY_GRACE:
            # 宽限窗外：不补发陈旧周期，记 missed（工单 08）
            await self._record_missed(task, actor, scheduled_at)
            counts["missed"] = 1
            return counts

        service = AgentTaskTriggerService(self.db)
        execution, created = await service.trigger(
            task_id=task.id,
            user=actor,
            trigger_type="schedule",
            idempotency_key=schedule_idempotency_key(task.id, scheduled_at, task.schedule_timezone or "UTC"),
            scheduled_at=scheduled_at,
        )
        counts["triggered"] = 1 if created and execution.status == "queued" else 0
        counts["skipped"] = 1 if created and execution.status == "skipped" else 0
        return counts

    @staticmethod
    def _unavailable_reason(task: AgentTask) -> str:
        """返回执行身份不可用的可展示原因类别。"""
        if task.department_id is None:
            return "任务未绑定部门"
        return "任务所属部门的成员授权已失效或所有者已删除"

    async def _record_failed(self, task: AgentTask, scheduled_at, reason: str) -> None:
        """为部门上下文失效的到期周期落一条 failed 终态执行；幂等键与正常触发一致。

        DST 秋季回拨使两个周期映射到同一幂等键（与正常触发同语义：只执行一次）；
        调用方已持任务行锁，先按幂等键复用既有执行事实，避免唯一约束冲突
        打断扫描事务导致 next_run_at 无法推进。
        """
        idempotency_key = schedule_idempotency_key(
            task.id, scheduled_at, task.schedule_timezone or "UTC"
        )
        existing = await self.executions.find_idempotent(
            task_id=task.id,
            principal_uid=str(task.owner_uid),
            idempotency_key=idempotency_key,
        )
        if existing is not None:
            return
        await self.executions.create(
            id=new_uuid(),
            task_id=task.id,
            trigger_type="schedule",
            triggered_by_uid=str(task.owner_uid),
            execution_principal_uid=str(task.owner_uid),
            department_id=task.department_id,
            agent_id=task.agent_id,
            agent_slug=task.agent_slug_snapshot or "",
            prompt=task.prompt,
            tool_approval_mode=task.tool_approval_mode,
            scheduled_at=scheduled_at,
            idempotency_key=idempotency_key,
            status="failed",
            error_summary=f"部门上下文失效：{reason}"[:500],
            finished_at=utc_now_naive(),
        )

    async def _record_missed(self, task: AgentTask, owner, scheduled_at) -> None:
        await self.executions.create(
            id=new_uuid(),
            task_id=task.id,
            trigger_type="schedule",
            triggered_by_uid=str(owner.uid),
            execution_principal_uid=str(owner.uid),
            department_id=task.department_id,
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
        owner = await self._owner_context(task.owner_uid, task.department_id)
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
