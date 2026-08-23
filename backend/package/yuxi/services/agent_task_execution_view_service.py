"""任务执行共享只读边界（工单 05）：按任务当前可见范围读取与审批。

不放宽普通 Thread/AgentRun 接口的用户隔离；审批额外要求当前用户
是该次执行的执行身份（任务创建者不能代替他人审批，ADR-0003）。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.repositories.agent_task_repository import (
    AgentTaskRepository,
    TaskExecutionRepository,
)
from yuxi.services.agent_task_crud_service import can_view_task
from yuxi.storage.postgres.models_business import TaskExecution, User
from yuxi.utils.datetime_utils import utc_now_naive
from yuxi.utils.logging_config import logger


class AgentTaskExecutionViewService:
    """以任务可见范围为边界聚合执行详情、消息与审批。"""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.tasks = AgentTaskRepository(db)
        self.executions = TaskExecutionRepository(db)

    async def get_execution_for(self, *, user: User, execution_id: str) -> TaskExecution:
        execution = await self.executions.get(execution_id)
        if execution is None:
            raise HTTPException(status_code=404, detail="执行不存在")
        task = await self.tasks.get(execution.task_id)
        if task is None or not can_view_task(user, task):
            raise HTTPException(status_code=404, detail="执行不存在")
        return execution

    async def messages(self, *, user: User, execution_id: str) -> dict:
        """读取执行线程消息；终态任务线程天然只读（无续写入口）。"""
        execution = await self.get_execution_for(user=user, execution_id=execution_id)
        if not execution.thread_id:
            return {"messages": [], "thread_id": None}
        from yuxi.repositories.conversation_repository import ConversationRepository

        repo = ConversationRepository(self.db)
        conversation = await repo.get_conversation_by_thread_id(execution.thread_id)
        if conversation is None:
            return {"messages": [], "thread_id": execution.thread_id}
        result = await repo.get_messages(conversation.id, limit=200)
        items = [m.to_dict() for m in result] if isinstance(result, list) else []
        return {"thread_id": execution.thread_id, "messages": items}

    async def resolve_artifact(self, *, user: User, execution_id: str, path: str):
        """按任务可见范围解析执行线程产物；文件归属执行身份而非查看者。"""
        execution = await self.get_execution_for(user=user, execution_id=execution_id)
        if not execution.thread_id:
            raise HTTPException(status_code=404, detail="执行尚未创建线程")
        from yuxi.services.thread_files_service import resolve_thread_artifact_by_owner

        return await resolve_thread_artifact_by_owner(
            thread_id=execution.thread_id,
            owner_uid=execution.execution_principal_uid,
            db=self.db,
            path=path,
        )

    async def submit_approval(self, *, user: User, execution_id: str, decisions) -> dict:
        """审批/拒绝：仅执行身份；复用标准 resume Run 提交链路。"""
        from yuxi.services.agent_run_service import create_agent_run_view

        execution = await self.get_execution_for(user=user, execution_id=execution_id)
        if str(user.uid) != execution.execution_principal_uid:
            raise HTTPException(status_code=403, detail="只有该次执行的执行身份可以审批")
        if not isinstance(decisions, list) or not decisions:
            raise HTTPException(status_code=422, detail="decisions 必须是非空数组")
        if execution.status != "interrupted" or not execution.agent_run_id:
            raise HTTPException(status_code=409, detail="执行不在等待审批状态")

        await create_agent_run_view(
            input_message=None,
            agent_slug=execution.agent_slug,
            thread_id=execution.thread_id or "",
            meta={"request_id": f"{execution.id}-approval"},
            model_spec=None,
            tool_approval_mode=None,
            current_uid=str(user.uid),
            db=self.db,
            resume={"decisions": decisions},
            created_by_run_id=execution.agent_run_id,
        )
        return {"status": "resumed"}


async def stream_agent_task_execution_events(
    *,
    execution_id: str,
    after_seq: str,
    user: User,
    verbose: bool = True,
) -> AsyncIterator[str]:
    """按 SSE 格式读取任务执行事件流；以任务可见范围授权（工单 03/05）。

    与标准 stream_agent_run_events 的区别：授权改用任务可见范围而非线程所有者；
    执行尚未派发（agent_run_id 为空）时发送心跳等待派发。
    """
    from yuxi.repositories.agent_run_repository import (
        AgentRunRepository,
        TERMINAL_RUN_STATUSES,
    )
    from yuxi.services.run_queue_service import (
        build_run_event_envelope,
        get_last_run_stream_seq,
        list_run_stream_events,
        normalize_after_seq,
    )
    from yuxi.storage.postgres.manager import pg_manager
    from yuxi.utils.sse_utils import (
        SSE_HEARTBEAT_SECONDS,
        SSE_MAX_CONNECTION_MINUTES,
        SSE_POLL_INTERVAL_SECONDS,
        format_heartbeat,
        format_sse,
    )

    started_at = utc_now_naive()
    last_heartbeat_ts = started_at
    last_seq = normalize_after_seq(after_seq)

    try:
        while True:
            try:
                async with pg_manager.get_async_session_context() as db:
                    view = AgentTaskExecutionViewService(db)
                    execution = await view.get_execution_for(user=user, execution_id=execution_id)
            except HTTPException as exc:
                yield format_sse(
                    {"execution_id": execution_id, "message": exc.detail},
                    event="error",
                )
                return
            except Exception as e:
                logger.warning(f"任务执行 SSE DB error execution={execution_id}: {e}")
                yield format_sse(
                    {"execution_id": execution_id, "message": "执行事件流暂时不可用，请重连"},
                    event="error",
                )
                return

            run_id = execution.agent_run_id
            if not run_id:
                # 尚未派发：发送心跳等待 Dispatcher 领取
                now = utc_now_naive()
                if (now - last_heartbeat_ts).total_seconds() >= SSE_HEARTBEAT_SECONDS:
                    yield format_sse(
                        {"execution_id": execution_id, "status": execution.status},
                        event="queued",
                    )
                    last_heartbeat_ts = now
                if (now - started_at).total_seconds() >= SSE_MAX_CONNECTION_MINUTES * 60:
                    return
                await asyncio.sleep(SSE_POLL_INTERVAL_SECONDS)
                continue

            try:
                events = await list_run_stream_events(run_id, after_seq=last_seq, limit=200)
            except Exception as e:
                logger.warning(f"任务执行 SSE redis error execution={execution_id}: {e}")
                yield format_sse(
                    {"execution_id": execution_id, "message": "执行事件流暂时不可用，请重连"},
                    event="error",
                )
                return

            emitted_terminal = False
            for event in events:
                seq = str(event.get("seq") or "0-0")
                last_seq = seq
                event_type = event.get("event_type") or "message"
                envelope = event.get("payload") or {}
                yield format_sse(envelope, event=event_type, event_id=seq)
                if event_type == "end":
                    emitted_terminal = True

            if emitted_terminal:
                return

            # 检查 Run 终态补发 end 帧
            try:
                async with pg_manager.get_async_session_context() as db:
                    run = await AgentRunRepository(db).get_run(run_id)
            except Exception:
                run = None

            if run and run.status in TERMINAL_RUN_STATUSES and not events:
                terminal_seq = last_seq
                if terminal_seq in {"", "0-0"}:
                    terminal_seq = await get_last_run_stream_seq(run_id)
                if terminal_seq in {"", "0-0"}:
                    terminal_seq = None
                terminal_envelope = build_run_event_envelope(
                    run_id=run_id,
                    thread_id=run.conversation_thread_id,
                    event_type="end",
                    payload={"status": run.status, "request_id": run.request_id},
                    created_at=utc_now_naive().isoformat(),
                )
                yield format_sse(terminal_envelope, event="end", event_id=terminal_seq)
                return

            now = utc_now_naive()
            elapsed_seconds = (now - started_at).total_seconds()
            heartbeat_elapsed = (now - last_heartbeat_ts).total_seconds()
            if heartbeat_elapsed >= SSE_HEARTBEAT_SECONDS:
                yield format_heartbeat()
                last_heartbeat_ts = now

            if elapsed_seconds >= SSE_MAX_CONNECTION_MINUTES * 60:
                return

            await asyncio.sleep(SSE_POLL_INTERVAL_SECONDS)
    except asyncio.CancelledError:
        return
