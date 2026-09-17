"""Agent 任务中心路由（父规格 #958 接口面）。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query
from fastapi.responses import FileResponse, Response, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from server.utils.auth_middleware import get_db, get_required_user, verify_api_key
from yuxi.services.agent_task_crud_service import (
    AgentTaskCRUDService,
    can_manage_task,
)
from yuxi.services.agent_task_dispatcher import AgentTaskDispatcher
from yuxi.services.agent_task_execution_view_service import AgentTaskExecutionViewService
from yuxi.services.agent_task_schedule_service import preview_schedule
from yuxi.services.agent_task_trigger_service import (
    AgentTaskTriggerService,
    manual_idempotency_key,
)
from yuxi.repositories.agent_task_repository import TaskExecutionRepository
from yuxi.storage.postgres.models_business import User
from yuxi.utils.datetime_utils import utc_now_naive

agent_task_router = APIRouter(prefix="/agent-tasks", tags=["agent-task-center"])


# ---------------------------------------------------------------- CRUD


@agent_task_router.get("")
async def list_tasks(
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    service = AgentTaskCRUDService(db)
    tasks = await service.list_tasks(user=current_user)
    exec_repo = TaskExecutionRepository(db)
    items = []
    for task in tasks:
        item = task.to_dict()
        item["queue_length"] = await exec_repo.queued_count(task.id)
        items.append(item)
    return {"tasks": items}


@agent_task_router.post("")
async def create_task(
    payload: dict[str, Any] = Body(...),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    if current_user is None:
        raise HTTPException(status_code=401, detail="未登录")
    service = AgentTaskCRUDService(db)
    task = await service.create_task(user=current_user, payload=payload)
    return task.to_dict()


@agent_task_router.post("/schedule-preview")
async def schedule_preview(
    payload: dict[str, Any] = Body(...),
    current_user: User = Depends(get_required_user),
):
    """预览未来五次本地执行时间（创建/编辑校验用）。"""
    if current_user is None:
        raise HTTPException(status_code=401, detail="未登录")
    schedule = payload.get("schedule") or {}
    timezone_name = str(schedule.get("timezone") or "UTC")
    return {"preview": preview_schedule(schedule, timezone_name)}


@agent_task_router.get("/{task_id}")
async def get_task(
    task_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    service = AgentTaskCRUDService(db)
    task = await service.get_task_for(user=current_user, task_id=task_id)
    return task.to_dict()


@agent_task_router.patch("/{task_id}")
async def update_task(
    task_id: str,
    payload: dict[str, Any] = Body(...),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    service = AgentTaskCRUDService(db)
    task = await service.update_task(user=current_user, task_id=task_id, payload=payload)
    return task.to_dict()


# ---------------------------------------------------------------- 触发


@agent_task_router.post("/{task_id}/executions")
async def trigger_manual(
    task_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """Web 手动触发：使用触发者身份，立即返回 execution_id。"""
    if current_user is None:
        raise HTTPException(status_code=401, detail="未登录")
    service = AgentTaskTriggerService(db)
    execution, _ = await service.trigger(
        task_id=task_id,
        user=current_user,
        trigger_type="manual",
        idempotency_key=manual_idempotency_key(),
    )
    return execution.to_dict()


@agent_task_router.post("/{task_id}/trigger", status_code=202)
async def trigger_api(
    task_id: str,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    authorization: str | None = Header(None),
    db: AsyncSession = Depends(get_db),
):
    """API Key 触发：只接受 Bearer API Key（yxkey_），JWT 不算 API 触发。"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="需要 Bearer API Key")
    token = authorization.split("Bearer ", 1)[1]
    if not token.startswith("yxkey_"):
        raise HTTPException(status_code=401, detail="API 触发只接受 API Key，不接受 JWT")
    user, api_key = await verify_api_key(token, db)
    if user is None:
        raise HTTPException(status_code=401, detail="无效的 API Key")
    api_key.last_used_at = utc_now_naive()
    await db.commit()

    service = AgentTaskTriggerService(db)
    execution, _ = await service.trigger(
        task_id=task_id,
        user=user,
        trigger_type="api",
        idempotency_key=str(idempotency_key),
    )
    return {"execution_id": execution.id, "status": execution.status}


@agent_task_router.get("/{task_id}/executions")
async def list_executions(
    task_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    service = AgentTaskCRUDService(db)
    task = await service.get_task_for(user=current_user, task_id=task_id)
    dispatcher = AgentTaskDispatcher(db)
    executions = await dispatcher.executions.list_by_task(task.id)
    return {"executions": [e.to_dict() for e in executions]}


# ---------------------------------------------------------------- 执行视图


executions_router = APIRouter(prefix="/agent-task-executions", tags=["agent-task-center"])


@executions_router.get("/{execution_id}")
async def get_execution(
    execution_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """查询单次执行状态、消息和结果定位信息（父规格 #958 接口面）。"""
    if current_user is None:
        raise HTTPException(status_code=401, detail="未登录")
    view = AgentTaskExecutionViewService(db)
    execution = await view.get_execution_for(user=current_user, execution_id=execution_id)
    result = execution.to_dict()
    result["messages"] = await view.messages(user=current_user, execution_id=execution_id)
    return result


@executions_router.get("/{execution_id}/events")
async def stream_execution_events(
    execution_id: str,
    after_seq: str = "0-0",
    verbose: bool = Query(default=True),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    current_user: User = Depends(get_required_user),
):
    """按任务可见范围转发该执行的 Run SSE 事件流（工单 03/05）。"""
    if current_user is None:
        raise HTTPException(status_code=401, detail="未登录")
    from yuxi.services.agent_task_execution_view_service import stream_agent_task_execution_events

    cursor = last_event_id or after_seq
    return StreamingResponse(
        stream_agent_task_execution_events(
            execution_id=execution_id,
            after_seq=cursor,
            user=current_user,
            verbose=verbose,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@executions_router.get("/{execution_id}/artifacts/{path:path}")
async def get_execution_artifact(
    execution_id: str,
    path: str,
    download: bool = Query(False),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """按任务可见范围读取执行线程产物（工单 05）。"""
    if current_user is None:
        raise HTTPException(status_code=401, detail="未登录")
    import aiofiles

    from yuxi.services.file_preview import detect_media_type
    from yuxi.services.thread_files_service import InMemoryArtifact

    view = AgentTaskExecutionViewService(db)
    file_path = await view.resolve_artifact(user=current_user, execution_id=execution_id, path=path)

    if isinstance(file_path, InMemoryArtifact):
        media_type = detect_media_type(file_path.name, file_path.content[:512])
        headers = {"Content-Disposition": f'attachment; filename="{file_path.name}"'} if download else None
        return Response(content=file_path.content, media_type=media_type, headers=headers)

    async with aiofiles.open(file_path, "rb") as artifact_file:
        file_head = await artifact_file.read(512)
    media_type = detect_media_type(file_path.name, file_head)
    headers = {"Content-Disposition": f'attachment; filename="{file_path.name}"'} if download else None
    return FileResponse(path=file_path, media_type=media_type, headers=headers)


@executions_router.post("/{execution_id}/approval")
async def submit_approval(
    execution_id: str,
    payload: dict[str, Any] = Body(...),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """仅执行身份可审批/拒绝（工单 05）；复用标准 resume 提交链路。"""
    if current_user is None:
        raise HTTPException(status_code=401, detail="未登录")
    view = AgentTaskExecutionViewService(db)
    return await view.submit_approval(
        user=current_user,
        execution_id=execution_id,
        decisions=payload.get("decisions"),
    )


@executions_router.post("/{execution_id}/cancel")
async def cancel_execution(
    execution_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """取消执行：任务创建者任意取消；部门成员只能取消自己触发的。"""
    if current_user is None:
        raise HTTPException(status_code=401, detail="未登录")
    view = AgentTaskExecutionViewService(db)
    execution = await view.get_execution_for(user=current_user, execution_id=execution_id)
    task = await view.tasks.get(execution.task_id)
    dispatcher = AgentTaskDispatcher(db)
    await dispatcher.cancel_execution(
        user_uid=str(current_user.uid),
        is_task_owner=can_manage_task(current_user, task) if task else False,
        execution=execution,
    )
    return {"status": "cancelling"}
