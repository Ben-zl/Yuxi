"""统一的 AgentRun 消息提交应用服务。

Web Chat、Agent Call 和评估入口只在路由/适配层处理各自的输入输出协议，
实际的 AgentRunRequest 入队、Conversation 绑定和提交后派发都从这里进入。
Resume 与 Subagent 保留各自的特殊生命周期，不经过本服务。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from yuxi.agents.buildin import agent_manager
from yuxi.repositories.agent_repository import AgentRepository
from yuxi.repositories.agent_run_repository import AgentRunRepository
from yuxi.services.department_context_service import DepartmentContext
from yuxi.repositories.agent_run_request_repository import AgentRunRequestRepository
from yuxi.repositories.conversation_repository import ConversationRepository
from yuxi.services.agent_request_queue_service import build_existing_intake_result, finalize_intake, intake_request
from yuxi.services.input_message_service import AgentRunInputMessage
from yuxi.services.project_service import create_implicit_project, lock_project_workdir_changes
from yuxi.services.workdir_service import resolve_conversation_workdir_binding
from yuxi.storage.postgres.models_business import Project
from yuxi.storage.postgres.manager import pg_manager
from yuxi.utils.async_cleanup import await_cleanup_completion
from yuxi.workspace.filesystem import Workspace
from yuxi.workspace.paths import normalize_managed_workdir_path, normalize_workdir_path, strictly_overlapping_workdirs


async def _require_compensation_attachment_owner(
    db: AsyncSession, *, uid: str, workdir_path: str, project_id: str | None, created_managed: bool
) -> None:
    """在路径锁下确认旧附件仍属于本次提交的 Workdir。"""
    await lock_project_workdir_changes(db=db, uid=uid)
    result = await db.execute(select(Project.id, Project.workdir_path, Project.status).where(Project.uid == uid))
    overlapping = [
        (owner_id, owner_path, status)
        for owner_id, owner_path, status in result.all()
        if owner_path == workdir_path or strictly_overlapping_workdirs(owner_path, workdir_path)
    ]
    if created_managed:
        if overlapping:
            raise RuntimeError("Managed Workdir has a new Project owner")
    elif project_id is None or overlapping != [(project_id, workdir_path, "active")]:
        raise RuntimeError("Run attachment Workdir owner changed")


async def _remove_created_managed_workdir_if_unowned(db: AsyncSession, *, uid: str, workdir_path: str) -> None:
    """锁定同 UID Project 绑定，只删除确实无其他 Owner 的空目录。"""
    normalized = normalize_managed_workdir_path(workdir_path)
    target = PurePosixPath(normalized)
    await lock_project_workdir_changes(db=db, uid=uid)
    result = await db.execute(select(Project.workdir_path).where(Project.uid == uid))
    for candidate in result.scalars().all():
        existing = PurePosixPath(normalize_workdir_path(candidate))
        if existing == target or existing in target.parents or target in existing.parents:
            raise RuntimeError("Managed Workdir has an overlapping Project owner")

    workspace = Workspace(uid)
    for suffix in ("uploads/attachments", "uploads", ""):
        scope = f"/{normalized}/{suffix}".rstrip("/")
        try:
            await asyncio.to_thread(
                workspace.remove_authorized_empty_directory,
                scope,
                root="/projects",
            )
        except FileNotFoundError:
            continue


async def _committed_submission_visible(*, request_id: str, uid: str, agent_slug: str, thread_id: str) -> bool | None:
    """用独立事务确认提交结果；无法回读时视为不确定。"""
    try:
        async with pg_manager.get_async_session_context() as reader:
            request = await AgentRunRequestRepository(reader).get_by_request_id(request_id)
            if request is None:
                return False
            if request.uid == uid and request.agent_slug == agent_slug and request.conversation_thread_id == thread_id:
                return True
            return None
    except BaseException:
        return None


async def _lock_submission_request(db: AsyncSession, request_id: str) -> None:
    """同一 request_id 的提交在附件副作用之前串行化。"""
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
        {"lock_key": f"run-submission:{request_id}"},
    )


def _submission_response(intake) -> dict[str, Any]:
    """把新建或已存在的 intake 组装为同一公开响应。"""
    return {
        "request_id": intake.request_id,
        "status": intake.status,
        "queue_policy": intake.queue_policy,
        "queue_position": intake.queue_position,
        "message_id": intake.message_id,
        "run_id": intake.run_id,
        "stream_url": f"/api/agent/runs/{intake.run_id}/events" if intake.run_id else None,
        "request_events_url": (
            f"/api/agent/requests/{intake.request_id}/events" if intake.status == "queued" else None
        ),
        "thread_id": intake.thread_id,
    }


@dataclass(frozen=True)
class RunOrigin:
    """描述一次 Run 请求的入口来源与传输通道。"""

    source: str
    channel: str
    external_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RunSubmissionAttachment:
    """外部入口随 Run 提交的有界附件。"""

    file_name: str
    media_type: str | None
    content: bytes


@dataclass(frozen=True)
class RunSubmissionCommand:
    """统一的消息型 Run 提交命令。"""

    agent_slug: str
    thread_id: str
    request_id: str
    input_message: AgentRunInputMessage
    origin: RunOrigin
    department_id: int
    request_metadata: dict[str, Any] = field(default_factory=dict)
    model_spec: str | None = None
    tool_approval_mode: str | None = None
    queue_policy: str = "enqueue"
    create_conversation: bool = False
    conversation_title: str | None = None
    agent_kind: str = "main"
    attachments: tuple[RunSubmissionAttachment, ...] = ()


async def _rollback_submission_side_effects(
    *,
    db: AsyncSession,
    uid: str,
    workdir_path: str | None,
    stored_attachments: list[dict[str, Any]],
    delete_created_managed_workdir: bool,
    owning_project_id: str | None = None,
) -> list[BaseException]:
    """回滚提交前数据库事务，并删除本次创建的附件与 managed Workdir。"""
    failures: list[BaseException] = []
    try:
        await db.rollback()
    except BaseException as exc:
        failures.append(exc)
    safe_to_remove = True
    if stored_attachments and workdir_path:
        try:
            await _require_compensation_attachment_owner(
                db,
                uid=uid,
                workdir_path=workdir_path,
                project_id=owning_project_id,
                created_managed=delete_created_managed_workdir,
            )
        except BaseException as exc:
            failures.append(exc)
            safe_to_remove = False
    if safe_to_remove and stored_attachments and workdir_path:
        from yuxi.services.attachment_service import rollback_run_submission_attachments

        try:
            await rollback_run_submission_attachments(
                records=stored_attachments,
                uid=uid,
                workdir_path=workdir_path,
            )
        except BaseException as exc:
            failures.append(exc)
    if not safe_to_remove or not delete_created_managed_workdir or not workdir_path:
        return failures
    try:
        await _remove_created_managed_workdir_if_unowned(
            db,
            uid=uid,
            workdir_path=workdir_path,
        )
    except BaseException as exc:
        failures.append(exc)
    return failures


async def _compensate_submission_failure(
    primary_error: BaseException,
    *,
    db: AsyncSession,
    uid: str,
    workdir_path: str | None,
    stored_attachments: list[dict[str, Any]],
    delete_created_managed_workdir: bool,
    owning_project_id: str,
) -> None:
    """完成提交前补偿，并把补偿失败附加到原始异常。"""
    try:
        failures = await await_cleanup_completion(
            _rollback_submission_side_effects(
                db=db,
                uid=uid,
                workdir_path=workdir_path,
                stored_attachments=stored_attachments,
                delete_created_managed_workdir=delete_created_managed_workdir,
                owning_project_id=owning_project_id,
            )
        )
    except BaseException as exc:
        primary_error.add_note(f"Run submission compensation also failed: {type(exc).__name__}")
        return
    if failures:
        summary = ", ".join(type(exc).__name__ for exc in failures)
        primary_error.add_note(f"Run submission compensation also failed: {summary}")


async def submit_run_command(
    *,
    command: RunSubmissionCommand,
    current_user: DepartmentContext,
    db: AsyncSession,
) -> dict[str, Any]:
    """校验作用域、写入 Request 并在提交后投递消息型 AgentRun。

    ``create_conversation`` 仅用于没有显式 Thread 的外部入口；普通 Web Chat
    必须复用已经创建的 Conversation。不同入口的协议适配不应绕过这里。
    """

    current_uid = str(current_user.uid)
    if current_user.department_id is None or command.department_id != current_user.department_id:
        raise HTTPException(
            status_code=403,
            detail="运行提交部门必须与当前有效部门上下文一致",
        )
    origin = command.origin
    if not origin.source.strip() or not origin.channel.strip():
        raise HTTPException(status_code=422, detail="Run origin source/channel 不能为空")
    if len(origin.source) > 32:
        raise HTTPException(status_code=422, detail="Run origin source 不能超过 32 个字符")
    if len(origin.channel) > 32:
        raise HTTPException(status_code=422, detail="Run origin channel 不能超过 32 个字符")
    external_id = str(origin.external_id).strip() if origin.external_id is not None else None
    if external_id == "":
        external_id = None
    origin_metadata = {
        key: value for key, value in origin.metadata.items() if key not in {"source", "channel", "external_id"}
    }

    await _lock_submission_request(db, command.request_id)
    await lock_project_workdir_changes(db=db, uid=current_uid)
    request_repo = AgentRunRequestRepository(db)
    existing_request = await request_repo.get_by_request_id(command.request_id)
    if existing_request is not None:
        if existing_request.department_id != command.department_id:
            raise HTTPException(
                status_code=409,
                detail=f"request_id 已绑定其他部门（{existing_request.department_id}），不能复用",
            )
        intake = await build_existing_intake_result(
            repo=request_repo,
            request=existing_request,
            uid=current_uid,
            agent_slug=command.agent_slug,
            thread_id=command.thread_id,
            source=origin.source,
            channel=origin.channel,
            external_id=external_id,
            queue_policy=command.queue_policy,
        )
        return _submission_response(intake)
    existing_run = await AgentRunRepository(db).get_run_by_request_id(command.request_id)
    if existing_run and not existing_request:
        if existing_run.uid != current_uid:
            raise HTTPException(status_code=409, detail="request_id 冲突")
        if existing_run.agent_slug != command.agent_slug or existing_run.run_type != "chat":
            raise HTTPException(status_code=409, detail="request_id 冲突")
        if command.thread_id and existing_run.conversation_thread_id != command.thread_id:
            raise HTTPException(status_code=409, detail="request_id 冲突")
        return {
            "request_id": command.request_id,
            "status": existing_run.status,
            "queue_policy": command.queue_policy,
            "queue_position": 0,
            "message_id": existing_run.input_message_id,
            "run_id": existing_run.id,
            "stream_url": f"/api/agent/runs/{existing_run.id}/events",
            "request_events_url": None,
            "thread_id": existing_run.conversation_thread_id,
        }
    agent_repo = AgentRepository(db)
    agent_item = await agent_repo.get_visible_by_slug(
        slug=command.agent_slug,
        user=current_user,
        kind=command.agent_kind,
    )
    if not agent_item:
        raise HTTPException(status_code=404, detail="智能体不存在")
    agent_backend = agent_manager.get_agent(agent_item.backend_id)
    if not agent_backend:
        raise HTTPException(status_code=404, detail=f"智能体后端 {agent_item.backend_id} 不存在")

    conversation_repo = ConversationRepository(db)
    conversation = await conversation_repo.get_conversation_by_thread_id(command.thread_id)
    created_conversation = False
    if not conversation:
        if not command.create_conversation:
            raise HTTPException(status_code=404, detail="对话线程不存在")
        try:
            async with db.begin_nested():
                project = await create_implicit_project(
                    uid=current_uid,
                    db=db,
                )
                conversation = await conversation_repo.add_conversation(
                    uid=current_uid,
                    agent_id=agent_item.slug,
                    title=command.conversation_title,
                    thread_id=command.thread_id,
                    metadata={
                        **origin_metadata,
                        "source": origin.source,
                        "channel": origin.channel,
                    },
                    project_id=project.id,
                )
                created_conversation = True
        except IntegrityError:
            conversation = await conversation_repo.get_conversation_by_thread_id(command.thread_id)
            if not conversation:
                raise

    request_metadata = dict(command.request_metadata or {})
    request_metadata["channel"] = origin.channel
    for key, value in origin_metadata.items():
        if key in {"source", "channel"}:
            continue
        request_metadata.setdefault(key, value)

    workdir_path, project = await resolve_conversation_workdir_binding(
        conversation=conversation,
        uid=current_uid,
        db=db,
    )
    stored_attachments: list[dict[str, Any]] = []
    commit_started = False

    try:
        if command.attachments:
            from yuxi.services.attachment_service import persist_run_submission_attachments

            stored_attachments = await persist_run_submission_attachments(
                conversation=conversation,
                uid=current_uid,
                attachments=command.attachments,
                db=db,
            )
            request_metadata["attachment_file_ids"] = [item["file_id"] for item in stored_attachments]

        agent_item = await agent_repo.get_visible_for_update_by_slug(
            slug=command.agent_slug,
            user=current_user,
            kind=command.agent_kind,
        )
        if not agent_item:
            raise HTTPException(status_code=404, detail="智能体不存在")
        agent_backend = agent_manager.get_agent(agent_item.backend_id)
        if not agent_backend:
            raise HTTPException(status_code=404, detail=f"智能体后端 {agent_item.backend_id} 不存在")

        try:
            intake = await intake_request(
                department_id=command.department_id,
                db=db,
                request_id=command.request_id,
                uid=current_uid,
                agent_slug=agent_item.slug,
                thread_id=command.thread_id,
                source=origin.source,
                channel=origin.channel,
                external_id=external_id,
                origin_metadata=origin_metadata,
                queue_policy=command.queue_policy,
                input_message=command.input_message,
                agent_item=agent_item,
                agent_backend=agent_backend,
                model_spec=command.model_spec,
                tool_approval_mode=command.tool_approval_mode,
                meta=request_metadata,
                user=current_user,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        commit_started = True
        await db.commit()
    except asyncio.CancelledError as exc:
        if commit_started:
            exc.add_note("Run commit outcome is not safely reversible; submitted resources were preserved")
            raise
        await _compensate_submission_failure(
            exc,
            db=db,
            uid=current_uid,
            workdir_path=workdir_path,
            stored_attachments=stored_attachments,
            delete_created_managed_workdir=created_conversation and project.directory_mode == "managed",
            owning_project_id=project.id,
        )
        raise
    except Exception as exc:
        if commit_started:
            state = await await_cleanup_completion(
                _committed_submission_visible(
                    request_id=command.request_id,
                    uid=current_uid,
                    agent_slug=agent_item.slug,
                    thread_id=command.thread_id,
                )
            )
            if state is not False or (isinstance(exc, (OSError, DBAPIError)) and not isinstance(exc, IntegrityError)):
                exc.add_note("Run commit outcome is not safely reversible; submitted resources were preserved")
                raise
        await _compensate_submission_failure(
            exc,
            db=db,
            uid=current_uid,
            workdir_path=workdir_path,
            stored_attachments=stored_attachments,
            delete_created_managed_workdir=created_conversation and project.directory_mode == "managed",
            owning_project_id=project.id,
        )
        raise

    await finalize_intake(
        db=db,
        intake=intake,
        uid=current_uid,
        workdir_path=workdir_path,
        materialize_managed=project.directory_mode == "managed",
        commit=False,
    )

    return _submission_response(intake)
