"""AgentTask 派发器：FIFO 领取、标准 Run 提交与终态推进（工单 03/04/11）。

派发链路：锁定任务行 → 无活动执行时领取 FIFO 队头 → 以 execution_id 作为
request_id、确定性 thread_id 调用标准 submit_run_command（复用其幂等、
Agent 可见性校验与 ARQ 投递）→ 回写关联。崩溃恢复按稳定身份反查补齐，
不产生第二份 Run/Thread 事实。
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.repositories.agent_run_repository import AgentRunRepository
from yuxi.repositories.agent_task_repository import (
    AgentTaskRepository,
    TaskExecutionRepository,
)
from yuxi.services.run_submission_service import (
    RunOrigin,
    RunSubmissionCommand,
    submit_run_command,
)
from yuxi.storage.postgres.models_business import TaskExecution, User
from yuxi.utils.logging_config import logger

# AgentRun 终态 → TaskExecution 终态；interrupted 保持占槽（等待审批）
RUN_STATUS_TO_EXECUTION = {
    "completed": "succeeded",
    "failed": "failed",
    "cancelled": "cancelled",
}


def execution_thread_id(execution_id: str) -> str:
    """由执行身份确定性派生线程 ID（恢复重试命中同一线程）。"""
    return f"task-exec-{execution_id}"


class AgentTaskDispatcher:
    """任务级 FIFO 派发与生命周期推进。"""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.tasks = AgentTaskRepository(db)
        self.executions = TaskExecutionRepository(db)

    async def _principal(self, uid: str) -> User:
        from yuxi.repositories.user_repository import UserRepository

        user = await UserRepository().get_by_uid_with_db(self.db, uid)
        if user is None or user.is_deleted:
            raise RuntimeError(f"执行身份不存在: {uid}")
        return user

    async def dispatch_head(self, task_id: str) -> TaskExecution | None:
        """领取并派发 FIFO 队头；活动执行占槽时直接返回。"""
        task = await self.tasks.get_for_update(task_id)
        if task is None or not task.enabled:
            return None
        active = await self.executions.active_slot(task_id)
        if active is not None and active.status in ("running", "interrupted"):
            return active
        head = (
            active if (active is not None and active.status == "queued") else await self.executions.fifo_head(task_id)
        )
        if head is None:
            return None

        try:
            result = await self._submit(head)
        except HTTPException as exc:
            detail = str(exc.detail)[:500]
            await self.executions.mark(head, status="failed", error_summary=detail, finished=True)
            logger.warning(f"任务执行派发失败 execution={head.id}: {detail}")
            await self.db.commit()
            return await self.dispatch_head(task_id)  # 失败不暂停任务，继续下一项

        head.agent_run_id = result.get("run_id")
        head.thread_id = result.get("thread_id")
        head.conversation_id = result.get("conversation_id")
        await self.executions.mark(head, status="running", started=True)
        return head

    async def _submit(self, execution: TaskExecution) -> dict[str, Any]:
        """以标准提交链路创建 AgentRun；幂等由 submit_run_command 保证。"""
        from yuxi.services.agent_task_trigger_service import TRIGGER_CHANNELS

        principal = await self._principal(execution.execution_principal_uid)
        # 过渡：执行记录固定部门优先；未迁移的旧任务暂以 principal 旧部门字段兜底（任务6 收口拒绝）
        actor_department_id = execution.department_id or principal.department_id
        command = RunSubmissionCommand(
            department_id=actor_department_id,
            agent_slug=execution.agent_slug,
            thread_id=execution_thread_id(execution.id),
            request_id=execution.id,
            input_message=build_task_input_message(execution.prompt),
            origin=RunOrigin(
                source="agent_task",
                channel=TRIGGER_CHANNELS.get(execution.trigger_type, "internal"),
            ),
            tool_approval_mode=execution.tool_approval_mode,
            queue_policy="reject",
            create_conversation=True,
            conversation_title=f"任务执行 {execution.id[:8]}",
        )
        result = await submit_run_command(command=command, current_user=principal, db=self.db)
        conversation_id = result.get("conversation_id")
        if conversation_id is None and result.get("thread_id"):
            from yuxi.repositories.conversation_repository import ConversationRepository

            conversation = await ConversationRepository(self.db).get_conversation_by_thread_id(result["thread_id"])
            conversation_id = conversation.id if conversation else None
        result["conversation_id"] = conversation_id
        return result

    async def cancel_queued(self, task) -> int:
        """停用/归档时取消全部排队执行；当前 AgentRun 继续。"""
        count = 0
        while True:
            head = await self.executions.fifo_head(task.id)
            if head is None:
                break
            await self.executions.mark(head, status="cancelled", finished=True)
            count += 1
        if count:
            await self.db.flush()
        return count

    async def cancel_execution(self, *, user_uid: str, is_task_owner: bool, execution: TaskExecution) -> None:
        """取消执行：排队项直接终态；运行中走标准 Run 取消链路。"""
        if not is_task_owner and execution.triggered_by_uid != user_uid:
            raise HTTPException(status_code=403, detail="只能取消自己触发的执行")
        if execution.status == "queued":
            await self.executions.mark(execution, status="cancelled", finished=True)
            await self.db.commit()
            return
        if execution.status in ("running", "interrupted") and execution.agent_run_id:
            from yuxi.services.agent_run_service import request_cancel_agent_run

            principal = await self._principal(user_uid)
            await request_cancel_agent_run(
                run_id=execution.agent_run_id,
                current_uid=str(principal.uid),
                db=self.db,
            )
            await self.db.commit()
            return
        raise HTTPException(status_code=409, detail="执行已结束")

    async def notify_run_finished(self, run_id: str, run_status: str) -> bool:
        """AgentRun 终态钩子：回写执行终态并派发下一队头。

        返回是否为本任务执行（worker 侧无任务时不做事）。
        """
        execution = await self.executions.get_by_run_id(run_id)
        if execution is None:
            run = await AgentRunRepository(self.db).get_run(run_id)
            if run is not None and run.run_type == "resume" and run.created_by_run_id:
                execution = await self.executions.get_by_run_id(run.created_by_run_id)
        if execution is None:
            return False
        final = RUN_STATUS_TO_EXECUTION.get(run_status)
        if final is None:  # interrupted：等待审批，保持占槽
            await self._mark_interrupted(execution)
            return True
        if execution.status in ("queued", "running", "interrupted"):
            await self.executions.mark(execution, status=final, finished=True)
            await self.db.commit()
        await self.dispatch_head(execution.task_id)
        await self.db.commit()
        return True

    async def _mark_interrupted(self, execution: TaskExecution) -> None:
        if execution.status == "running":
            execution.status = "interrupted"
            await self.db.commit()

    async def recover_stale(self, older_than) -> int:
        """崩溃恢复：按 execution_id 反查 Request/Run 补关联，缺失才重派。

        （工单 11）恢复不产生第二个 Run：submit_run_command 幂等命中
        已有事实；pending Run 复用现有 recover_pending_dispatches 补投递。
        """
        recovered = 0
        stale = await self.executions.stale_active(older_than)
        for execution in stale:
            task = await self.tasks.get(execution.task_id)
            if task is None:
                continue
            run = await AgentRunRepository(self.db).get_run_by_request_id(execution.id)
            if run is not None:
                execution.agent_run_id = run.id
                execution.thread_id = run.conversation_thread_id
                if run.status in RUN_STATUS_TO_EXECUTION:
                    await self.executions.mark(
                        execution,
                        status=RUN_STATUS_TO_EXECUTION[run.status],
                        finished=True,
                    )
                elif run.status == "pending":
                    # pending Run 需要补投递 ARQ 任务
                    await self.executions.mark(execution, status="running", started=True)
                    await self._recover_pending_dispatch(run)
                else:
                    await self.executions.mark(execution, status="running", started=True)
                await self.db.commit()
                recovered += 1
                continue
            # 无 Run 事实：重试幂等派发（queued 队头场景）
            await self.dispatch_head(execution.task_id)
            recovered += 1
        return recovered

    async def _recover_pending_dispatch(self, run) -> None:
        """补投递 pending AgentRun 的 ARQ 任务（复用现有确定性恢复机制）。"""
        from yuxi.services.agent_request_queue_service import dispatch_next_request

        await dispatch_next_request(
            uid=run.uid,
            agent_slug=run.agent_slug,
            thread_id=run.conversation_thread_id,
        )


def build_task_input_message(prompt: str):
    """构造标准提交链路的消息型输入。"""
    from yuxi.services.input_message_service import build_chat_input_message

    return build_chat_input_message(prompt)


async def notify_agent_task_finished(run_id: str, run_status: str) -> bool:
    """worker 终态路径的轻量入口：非任务 Run 返回 False。"""
    from yuxi.storage.postgres.manager import pg_manager

    async with pg_manager.get_async_session_context() as db:
        return await AgentTaskDispatcher(db).notify_run_finished(run_id, run_status)
