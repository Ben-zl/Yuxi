"""外部 Run 提交失败时数据库事实与 managed Workdir 的补偿验证。"""

from __future__ import annotations

import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.services import conversation_service, run_submission_service as service
from yuxi.services.input_message_service import build_chat_input_message
from yuxi.storage.postgres.models_business import (
    AgentRunRequest,
    Conversation,
    ConversationStats,
    Message,
    Project,
    User,
)
from yuxi.workspace.paths import user_workspace_dir

pytestmark = pytest.mark.integration


async def test_rollback_preserves_attachment_after_other_project_claims_workdir(monkeypatch, tmp_path):
    """回滚后出现新 Owner 时，附件补偿不得再删除原路径文件。"""
    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path))
    engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    uid = f"comp-owner-{uuid4().hex[:12]}"
    project_id = str(uuid4())
    workdir_path = f"projects/{project_id}"
    original_path = f"/home/gem/user-data/{workdir_path}/uploads/file.txt"
    attachment = user_workspace_dir(uid) / workdir_path / "uploads" / "file.txt"
    attachment.parent.mkdir(parents=True)
    attachment.write_bytes(b"new owner data")
    try:
        async with session_factory() as db:
            db.add(User(uid=uid, username=uid, password_hash="test-only", role="user"))
            await db.flush()
            db.add(
                Project(
                    id=project_id,
                    uid=uid,
                    workdir_path=workdir_path,
                    directory_mode="linked",
                    selection_status="selectable",
                )
            )
            await db.commit()

            failures = await service._rollback_submission_side_effects(
                db=db,
                uid=uid,
                workdir_path=workdir_path,
                stored_attachments=[{"file_id": "file", "original_path": original_path, "path": original_path}],
                delete_created_managed_workdir=True,
            )
            assert failures
            assert attachment.read_bytes() == b"new owner data"
            await db.rollback()
    finally:
        async with session_factory() as db:
            await db.execute(delete(Project).where(Project.id == project_id))
            await db.execute(delete(User).where(User.uid == uid))
            await db.commit()
        await engine.dispose()


class _EmptyRequestRepository:
    def __init__(self, db):
        del db

    async def get_by_request_id(self, request_id):
        del request_id
        return None


class _EmptyRunRepository:
    def __init__(self, db):
        del db

    async def get_run_by_request_id(self, request_id):
        del request_id
        return None


class _VisibleAgentRepository:
    def __init__(self, db):
        del db

    async def get_visible_by_slug(self, *, slug, user, kind):
        del user, kind
        return SimpleNamespace(slug=slug, backend_id="ChatbotAgent")

    async def get_visible_for_update_by_slug(self, *, slug, user, kind):
        return await self.get_visible_by_slug(slug=slug, user=user, kind=kind)


async def test_submission_request_lock_serializes_same_id() -> None:
    """相同 request_id 的第二个事务不能越过附件前的锁。"""
    engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    request_id = f"lock-{uuid4()}"
    try:
        async with session_factory() as first, session_factory() as second:
            await service._lock_submission_request(first, request_id)
            competing = await second.scalar(
                text("SELECT pg_try_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
                {"lock_key": f"run-submission:{request_id}"},
            )
            assert competing is False
            await first.rollback()
            released = await second.scalar(
                text("SELECT pg_try_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
                {"lock_key": f"run-submission:{request_id}"},
            )
            assert released is True
            await second.rollback()
    finally:
        await engine.dispose()


async def test_commit_probes_read_only_matching_persisted_owners() -> None:
    """独立 PostgreSQL 连接只认本次 Request 与附件的精确归属。"""
    engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    uid = f"commit-probe-{uuid4().hex[:12]}"
    thread_id = str(uuid4())
    project_id = str(uuid4())
    request_id = f"request-{uuid4()}"
    file_id = uuid4().hex
    original_path = f"/home/gem/user-data/projects/{project_id}/uploads/{file_id}_report.txt"
    try:
        async with session_factory() as db:
            db.add(User(uid=uid, username=uid, password_hash="test-only", role="user"))
            await db.flush()
            db.add(
                Project(
                    id=project_id,
                    uid=uid,
                    selection_status="implicit",
                    workdir_path=f"projects/{project_id}",
                    directory_mode="managed",
                )
            )
            await db.flush()
            conversation = Conversation(
                thread_id=thread_id,
                uid=uid,
                agent_id="probe-agent",
                project_id=project_id,
                extra_metadata={"attachments": [{"file_id": file_id, "original_path": original_path}]},
            )
            db.add(conversation)
            await db.flush()
            message = Message(conversation_id=conversation.id, role="user", content="probe")
            db.add(message)
            await db.flush()
            db.add(
                AgentRunRequest(
                    request_id=request_id,
                    uid=uid,
                    agent_slug="probe-agent",
                    conversation_thread_id=thread_id,
                    input_message_id=message.id,
                )
            )
            await db.commit()

        assert (
            await service._committed_submission_visible(
                request_id=request_id, uid=uid, agent_slug="probe-agent", thread_id=thread_id
            )
            is True
        )
        assert (
            await service._committed_submission_visible(
                request_id=request_id, uid="another-user", agent_slug="probe-agent", thread_id=thread_id
            )
            is None
        )
        assert (
            await service._committed_submission_visible(
                request_id="missing-request", uid=uid, agent_slug="probe-agent", thread_id=thread_id
            )
            is False
        )
        assert (
            await conversation_service._committed_attachment_visible(
                thread_id=thread_id, uid=uid, file_id=file_id, original_path=original_path
            )
            is True
        )
        assert (
            await conversation_service._committed_attachment_visible(
                thread_id=thread_id, uid=uid, file_id=file_id, original_path="/different.txt"
            )
            is None
        )
        assert (
            await conversation_service._committed_attachment_visible(
                thread_id=thread_id, uid=uid, file_id="missing-file", original_path=original_path
            )
            is False
        )
    finally:
        async with session_factory() as db:
            await db.execute(delete(AgentRunRequest).where(AgentRunRequest.request_id == request_id))
            await db.execute(delete(Message).where(Message.request_id == request_id))
            await db.execute(
                delete(Message).where(
                    Message.conversation_id.in_(select(Conversation.id).where(Conversation.thread_id == thread_id))
                )
            )
            await db.execute(
                delete(ConversationStats).where(
                    ConversationStats.conversation_id.in_(
                        select(Conversation.id).where(Conversation.thread_id == thread_id)
                    )
                )
            )
            await db.execute(delete(Conversation).where(Conversation.thread_id == thread_id))
            await db.execute(delete(Project).where(Project.id == project_id))
            await db.execute(delete(User).where(User.uid == uid))
            await db.commit()
        await engine.dispose()


async def test_new_external_run_intake_failure_removes_conversation_project_and_workdir(
    monkeypatch,
    tmp_path,
):
    """新建外部线程的提交前失败不能留下任何业务事实或 managed 目录。"""
    engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    uid = f"run-comp-{uuid4().hex[:12]}"
    thread_id = str(uuid4())
    request_id = f"request-{uuid4()}"
    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(service, "AgentRepository", _VisibleAgentRepository)
    monkeypatch.setattr(service, "AgentRunRequestRepository", _EmptyRequestRepository)
    monkeypatch.setattr(service, "AgentRunRepository", _EmptyRunRepository)
    monkeypatch.setattr(service.agent_manager, "get_agent", lambda _backend_id: object())

    async def reject_intake(**_kwargs):
        raise ValueError("模型资源已撤销")

    monkeypatch.setattr(service, "intake_request", reject_intake)

    try:
        async with session_factory() as db:
            user = User(
                uid=uid,
                username=uid,
                password_hash="test-only",
                role="superadmin",
            )
            db.add(user)
            await db.commit()

            command = service.RunSubmissionCommand(
                agent_slug="external-agent",
                thread_id=thread_id,
                request_id=request_id,
                input_message=build_chat_input_message("hello"),
                origin=service.RunOrigin(source="agent_call", channel="api"),
                create_conversation=True,
                attachments=(
                    service.RunSubmissionAttachment(
                        file_name="evidence.txt",
                        media_type="text/plain",
                        content=b"evidence",
                    ),
                ),
            )

            with pytest.raises(HTTPException) as exc_info:
                await service.submit_run_command(command=command, current_user=user, db=db)

            assert exc_info.value.status_code == 422
            assert exc_info.value.detail == "模型资源已撤销"

        async with session_factory() as verify:
            assert await verify.scalar(select(Conversation).where(Conversation.thread_id == thread_id)) is None
            assert await verify.scalar(select(Project).where(Project.uid == uid)) is None
        projects_root = user_workspace_dir(uid) / "projects"
        assert not projects_root.exists() or list(projects_root.iterdir()) == []
    finally:
        async with session_factory() as cleanup:
            conversation_ids = select(Conversation.id).where(Conversation.thread_id == thread_id)
            await cleanup.execute(
                delete(ConversationStats).where(ConversationStats.conversation_id.in_(conversation_ids))
            )
            await cleanup.execute(delete(Conversation).where(Conversation.thread_id == thread_id))
            await cleanup.execute(delete(Project).where(Project.uid == uid))
            await cleanup.execute(delete(User).where(User.uid == uid))
            await cleanup.commit()
        await engine.dispose()
