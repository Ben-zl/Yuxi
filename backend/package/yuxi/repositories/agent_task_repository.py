"""AgentTask / TaskExecution 仓储：CRUD、行锁领取与 FIFO 状态转换。

任务级互斥由「锁定 agent_tasks 行后领取 FIFO 队头」保证（父规格 #958），
不引入分布式锁；执行与 Run/Thread 的关联具有唯一约束，重试不产生第二份事实。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.models_business import (
    TASK_EXECUTION_ACTIVE_STATUSES,
    AgentTask,
    TaskExecution,
)
from yuxi.utils.datetime_utils import utc_now_naive


class AgentTaskRepository:
    """AgentTask 表访问与生命周期转换。"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get(self, task_id: str) -> AgentTask | None:
        return await self.db.scalar(select(AgentTask).where(AgentTask.id == task_id))

    async def get_for_update(self, task_id: str) -> AgentTask | None:
        """锁定任务行：FIFO 领取与生命周期变更的互斥入口。"""
        return await self.db.scalar(select(AgentTask).where(AgentTask.id == task_id).with_for_update())

    async def create(self, **kwargs) -> AgentTask:
        task = AgentTask(**kwargs)
        self.db.add(task)
        await self.db.flush()
        return task

    async def list_recent(self, limit: int = 500) -> list[AgentTask]:
        """列出最近任务；可见范围过滤在服务层由权限解析完成。"""
        result = await self.db.execute(
            select(AgentTask).order_by(AgentTask.created_at.desc()).limit(limit)
        )
        return list(result.scalars().all())

    async def count_active_references(self, agent_id: int) -> int:
        """统计仍引用指定 Agent 的未归档任务。"""
        result = await self.db.scalar(
            select(func.count(AgentTask.id)).where(
                AgentTask.agent_id == agent_id,
                AgentTask.archived_at.is_(None),
            )
        )
        return int(result or 0)

    async def detach_agent_references(self, agent_id: int) -> None:
        """清理已归档任务与执行记录中的 Agent 引用。"""
        await self.db.execute(
            AgentTask.__table__.update().where(AgentTask.agent_id == agent_id).values(agent_id=None)
        )
        await self.db.execute(
            TaskExecution.__table__.update().where(TaskExecution.agent_id == agent_id).values(agent_id=None)
        )
        await self.db.flush()


class TaskExecutionRepository:
    """TaskExecution 表访问、幂等创建与 FIFO 查询。"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get(self, execution_id: str) -> TaskExecution | None:
        return await self.db.scalar(select(TaskExecution).where(TaskExecution.id == execution_id))

    async def get_by_run_id(self, agent_run_id: str) -> TaskExecution | None:
        return await self.db.scalar(select(TaskExecution).where(TaskExecution.agent_run_id == agent_run_id))

    async def find_idempotent(self, *, task_id: str, principal_uid: str, idempotency_key: str) -> TaskExecution | None:
        return await self.db.scalar(
            select(TaskExecution).where(
                TaskExecution.task_id == task_id,
                TaskExecution.execution_principal_uid == principal_uid,
                TaskExecution.idempotency_key == idempotency_key,
            )
        )

    async def create(self, **kwargs) -> TaskExecution:
        execution = TaskExecution(**kwargs)
        self.db.add(execution)
        await self.db.flush()
        return execution

    async def active_slot(self, task_id: str) -> TaskExecution | None:
        """占用执行槽的活动执行（running / interrupted / 已领取未终态）。"""
        return await self.db.scalar(
            select(TaskExecution)
            .where(
                TaskExecution.task_id == task_id,
                TaskExecution.status.in_(TASK_EXECUTION_ACTIVE_STATUSES),
            )
            .order_by(TaskExecution.queued_at, TaskExecution.id)
        )

    async def fifo_head(self, task_id: str) -> TaskExecution | None:
        """最早的 queued 执行；调用方必须已锁定任务行。"""
        return await self.db.scalar(
            select(TaskExecution)
            .where(TaskExecution.task_id == task_id, TaskExecution.status == "queued")
            .order_by(TaskExecution.queued_at, TaskExecution.id)
        )

    async def queued_schedule_count(self, task_id: str) -> int:
        from sqlalchemy import func

        result = await self.db.execute(
            select(func.count(TaskExecution.id)).where(
                TaskExecution.task_id == task_id,
                TaskExecution.status == "queued",
                TaskExecution.trigger_type == "schedule",
            )
        )
        return result.scalar() or 0

    async def queued_count(self, task_id: str) -> int:
        """该任务排队中的执行总数（含手动/API/定时）。"""
        from sqlalchemy import func

        result = await self.db.execute(
            select(func.count(TaskExecution.id)).where(
                TaskExecution.task_id == task_id,
                TaskExecution.status == "queued",
            )
        )
        return result.scalar() or 0

    async def list_by_task(self, task_id: str, limit: int = 100) -> list[TaskExecution]:
        result = await self.db.execute(
            select(TaskExecution)
            .where(TaskExecution.task_id == task_id)
            .order_by(TaskExecution.queued_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def stale_active(self, before: datetime) -> list[TaskExecution]:
        """恢复扫描目标：早于给定时间仍在 queued/running 的执行。"""
        result = await self.db.execute(
            select(TaskExecution)
            .where(
                TaskExecution.status.in_(("queued", "running")),
                TaskExecution.queued_at < before,
            )
            .limit(200)
        )
        return list(result.scalars().all())

    async def mark(
        self,
        execution: TaskExecution,
        *,
        status: str,
        error_summary: str | None = None,
        started: bool = False,
        finished: bool = False,
    ) -> None:
        """执行状态转换；时间戳由转换类型决定。"""
        execution.status = status
        if error_summary is not None:
            execution.error_summary = error_summary[:500]
        if started:
            execution.started_at = utc_now_naive()
        if finished:
            execution.finished_at = utc_now_naive()
        await self.db.flush()
