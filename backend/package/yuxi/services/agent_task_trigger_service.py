"""AgentTask 触发收口：手动、API 与定时统一创建幂等 TaskExecution。

触发事务只固化执行事实（智能体身份、提示词、审批模式、执行身份、
幂等键、任务固定部门），派发交由 AgentTaskDispatcher（父规格 #958）。
"""

from __future__ import annotations

import uuid

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.repositories.agent_task_repository import (
    AgentTaskRepository,
    TaskExecutionRepository,
)
from yuxi.services.agent_task_crud_service import can_view_task
from yuxi.services.department_context_service import DepartmentContext
from yuxi.storage.postgres.models_business import TaskExecution
from yuxi.utils.ids import new_uuid

TRIGGER_CHANNELS = {"manual": "web", "api": "api", "schedule": "internal"}


class AgentTaskTriggerService:
    """三个触发入口的唯一收口，负责校验与幂等。"""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.tasks = AgentTaskRepository(db)
        self.executions = TaskExecutionRepository(db)

    async def trigger(
        self,
        *,
        task_id: str,
        user: DepartmentContext,
        trigger_type: str,
        idempotency_key: str,
        scheduled_at=None,
        key_department_id: int | None = None,
    ) -> tuple[TaskExecution, bool]:
        """创建（或幂等返回）一次执行；返回 (execution, created)。

        API 触发额外校验 key 绑定部门与任务固定部门一致，不允许
        以 Web 活动部门覆盖任一绑定事实。
        """
        if trigger_type not in TRIGGER_CHANNELS:
            raise HTTPException(status_code=422, detail="无效的触发方式")

        task = await self.tasks.get_for_update(task_id)
        if task is None or not can_view_task(user, task):
            raise HTTPException(status_code=404, detail="任务不存在")
        if task.archived_at is not None or not task.enabled:
            raise HTTPException(status_code=409, detail="任务未启用")
        if trigger_type == "api" and not task.api_enabled:
            raise HTTPException(status_code=409, detail="任务未启用 API 触发")
        if task.department_id is None:
            raise HTTPException(status_code=409, detail="任务未绑定部门，不能触发执行")
        if trigger_type == "api" and key_department_id != task.department_id:
            raise HTTPException(status_code=403, detail="API Key 绑定部门与任务部门不一致")

        principal_uid = str(user.uid)
        existing = await self.executions.find_idempotent(
            task_id=task.id,
            principal_uid=principal_uid,
            idempotency_key=idempotency_key,
        )
        if existing is not None:
            return existing, False

        # 定时合并：已有排队定时项时，本周期记为 skipped 不重复入队
        if trigger_type == "schedule" and await self.executions.queued_schedule_count(task.id) > 0:
            skipped = await self.executions.create(
                id=new_uuid(),
                task_id=task.id,
                trigger_type=trigger_type,
                triggered_by_uid=principal_uid,
                execution_principal_uid=principal_uid,
                department_id=task.department_id,
                agent_id=task.agent_id,
                agent_slug=task.agent_slug_snapshot or "",
                prompt=task.prompt,
                tool_approval_mode=task.tool_approval_mode,
                scheduled_at=scheduled_at,
                idempotency_key=idempotency_key,
                status="skipped",
            )
            await self.db.commit()
            return skipped, True

        execution = await self.executions.create(
            id=new_uuid(),
            task_id=task.id,
            trigger_type=trigger_type,
            triggered_by_uid=principal_uid,
            execution_principal_uid=principal_uid,
            department_id=task.department_id,
            agent_id=task.agent_id,
            agent_slug=task.agent_slug_snapshot or "",
            prompt=task.prompt,
            tool_approval_mode=task.tool_approval_mode,
            scheduled_at=scheduled_at,
            idempotency_key=idempotency_key,
            status="queued",
        )
        await self.db.commit()
        await self._dispatch(task_id)
        # dispatch 可能 rollback 使实例失效，按 ID 重取执行事实
        execution = await self.executions.get(execution.id)
        return execution, True

    async def _dispatch(self, task_id: str) -> None:
        """触发后立即尝试派发队头；失败不影响已创建的执行事实。"""
        from yuxi.services.agent_task_dispatcher import AgentTaskDispatcher

        try:
            await AgentTaskDispatcher(self.db).dispatch_head(task_id)
            await self.db.commit()
        except Exception:  # noqa: BLE001 - 派发失败由恢复扫描兜底
            await self.db.rollback()
            raise


def manual_idempotency_key() -> str:
    """手动触发生成唯一幂等键。"""
    return f"manual:{uuid.uuid4().hex}"
