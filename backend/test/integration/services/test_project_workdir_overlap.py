"""真实 PostgreSQL Project Workdir Owner 重叠验证。"""

from __future__ import annotations

import asyncio
import io
import os
from contextlib import suppress
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException, UploadFile
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.repositories.project_repository import ProjectRepository
from yuxi.repositories.conversation_repository import ConversationRepository
from yuxi.services import artifact_service, conversation_service
from yuxi.services.project_service import create_implicit_project, create_project_record, delete_project_view
from yuxi.services.workdir_service import AuthorizedWorkdir, resolve_conversation_workdir_binding
from yuxi.storage.postgres.models_business import AgentRunRequest, Conversation, Message, Project, User
from yuxi.workspace.paths import ensure_user_workspace, user_workspace_dir
from yuxi.workspace.workdir import Workdir

pytestmark = pytest.mark.integration


async def test_unbound_attachment_cannot_be_deleted_while_queued_request_references_it(monkeypatch, tmp_path):
    """请求已排队、Worker 尚未绑定附件时不能删除原文件。"""
    monkeypatch.setattr("yuxi.workspace.paths.get_user_data_dir", lambda: tmp_path)
    engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    uid = f"queued-attachment-{uuid4().hex[:12]}"
    project_id, thread_id, file_id, request_id = (str(uuid4()) for _ in range(4))
    workdir_path = f"projects/{project_id}"
    original_path = f"/home/gem/user-data/{workdir_path}/uploads/{file_id}.txt"
    ensure_user_workspace(uid)
    upload_path = user_workspace_dir(uid) / workdir_path / "uploads" / f"{file_id}.txt"
    upload_path.parent.mkdir(parents=True)
    upload_path.write_bytes(b"queued input")
    try:
        async with session_factory() as setup_db:
            setup_db.add(User(uid=uid, username=uid, password_hash="test-only", role="user"))
            await setup_db.flush()
            setup_db.add(
                Project(
                    id=project_id,
                    uid=uid,
                    workdir_path=workdir_path,
                    directory_mode="managed",
                    selection_status="implicit",
                )
            )
            await setup_db.flush()
            conversation = Conversation(
                thread_id=thread_id,
                uid=uid,
                agent_id="test-agent",
                project_id=project_id,
                extra_metadata={"attachments": [{"file_id": file_id, "original_path": original_path}]},
            )
            setup_db.add(conversation)
            await setup_db.flush()
            message = Message(
                conversation_id=conversation.id,
                role="user",
                content="use attachment",
                request_id=request_id,
                extra_metadata={"attachment_file_ids": [file_id]},
            )
            setup_db.add(message)
            await setup_db.flush()
            setup_db.add(
                AgentRunRequest(
                    request_id=request_id,
                    uid=uid,
                    agent_slug="test-agent",
                    conversation_thread_id=thread_id,
                    input_message_id=message.id,
                    status="queued",
                )
            )
            await setup_db.commit()

        async with session_factory() as deleting_db:
            with pytest.raises(HTTPException) as denied:
                await conversation_service.delete_thread_attachment_view(
                    thread_id=thread_id, file_id=file_id, db=deleting_db, current_uid=uid
                )
            assert denied.value.status_code == 409
            await deleting_db.rollback()

        async with session_factory() as verify_db:
            conversation = await ConversationRepository(verify_db).get_conversation_by_thread_id(thread_id)
            attachments = await ConversationRepository(verify_db).get_attachments(conversation.id)
            assert [item["file_id"] for item in attachments] == [file_id]
        assert upload_path.read_bytes() == b"queued input"
    finally:
        async with session_factory() as cleanup_db:
            await cleanup_db.execute(delete(AgentRunRequest).where(AgentRunRequest.request_id == request_id))
            await cleanup_db.execute(delete(Message).where(Message.request_id == request_id))
            await cleanup_db.execute(delete(Conversation).where(Conversation.thread_id == thread_id))
            await cleanup_db.execute(delete(Project).where(Project.id == project_id))
            await cleanup_db.execute(delete(User).where(User.uid == uid))
            await cleanup_db.commit()
        await engine.dispose()


async def test_attachment_delete_commit_failure_preserves_database_record_and_workdir_file(monkeypatch, tmp_path):
    """数据库事务失败不得留下指向缺失 Workdir 文件的附件记录。"""
    monkeypatch.setattr("yuxi.workspace.paths.get_user_data_dir", lambda: tmp_path)
    engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    uid = f"attachment-delete-{uuid4().hex[:12]}"
    project_id, thread_id, file_id = str(uuid4()), str(uuid4()), str(uuid4())
    workdir_path = f"projects/{project_id}"
    original_path = f"/home/gem/user-data/{workdir_path}/uploads/{file_id}.txt"
    ensure_user_workspace(uid)
    (user_workspace_dir(uid) / workdir_path / "uploads").mkdir(parents=True)
    workdir = Workdir.open_existing(uid, workdir_path)
    (user_workspace_dir(uid) / workdir_path / "uploads" / f"{file_id}.txt").write_bytes(b"keep on commit failure")
    try:
        async with session_factory() as setup_db:
            setup_db.add(User(uid=uid, username=uid, password_hash="test-only", role="user"))
            await setup_db.flush()
            setup_db.add(
                Project(
                    id=project_id,
                    uid=uid,
                    workdir_path=workdir_path,
                    directory_mode="managed",
                    selection_status="implicit",
                )
            )
            await setup_db.flush()
            setup_db.add(
                Conversation(
                    thread_id=thread_id,
                    uid=uid,
                    agent_id="test-agent",
                    project_id=project_id,
                    extra_metadata={"attachments": [{"file_id": file_id, "original_path": original_path}]},
                )
            )
            await setup_db.commit()

        async with session_factory() as deleting_db:

            async def fail_commit():
                await deleting_db.flush()
                raise RuntimeError("injected commit failure")

            with monkeypatch.context() as patch:
                patch.setattr(deleting_db, "commit", fail_commit)
                with pytest.raises(RuntimeError, match="injected commit failure"):
                    await conversation_service.delete_thread_attachment_view(
                        thread_id=thread_id, file_id=file_id, db=deleting_db, current_uid=uid
                    )
            await deleting_db.rollback()

        async with session_factory() as verify_db:
            conversation = await ConversationRepository(verify_db).get_conversation_by_thread_id(thread_id)
            assert [
                item["file_id"] for item in await ConversationRepository(verify_db).get_attachments(conversation.id)
            ] == [file_id]
        assert workdir.read_file(f"/uploads/{file_id}.txt", max_bytes=1024) == b"keep on commit failure"
    finally:
        async with session_factory() as cleanup_db:
            await cleanup_db.execute(delete(Conversation).where(Conversation.thread_id == thread_id))
            await cleanup_db.execute(delete(Project).where(Project.id == project_id))
            await cleanup_db.execute(delete(User).where(User.uid == uid))
            await cleanup_db.commit()
        await engine.dispose()


async def test_active_linked_parent_blocks_nested_project_and_implicit_managed_workdir(monkeypatch, tmp_path):
    """父 Workdir 不能覆盖后续 linked 或 implicit Project 的文件。"""
    monkeypatch.setattr("yuxi.workspace.paths.get_user_data_dir", lambda: tmp_path)
    engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    uid = f"workdir-overlap-{uuid4().hex[:12]}"
    parent_id = str(uuid4())
    historical_child_id = str(uuid4())
    ensure_user_workspace(uid)
    (user_workspace_dir(uid) / "projects" / "manual").mkdir(parents=True)

    class VisibleAgentRepo:
        def __init__(self, _db):
            pass

        async def get_visible_by_slug(self, **_kwargs):
            return SimpleNamespace(slug="main", backend_id="ChatbotAgent")

    monkeypatch.setattr(conversation_service, "AgentRepository", VisibleAgentRepo)
    try:
        async with session_factory() as db:
            db.add(User(uid=uid, username=uid, password_hash="test-only", role="user"))
            await db.flush()
            db.add(
                Project(
                    id=parent_id,
                    uid=uid,
                    selection_status="selectable",
                    workdir_path="projects",
                    directory_mode="linked",
                )
            )
            await db.commit()

            with pytest.raises(HTTPException) as nested:
                await create_project_record(
                    uid=uid,
                    name="Manual",
                    directory_mode="linked",
                    selection_status="selectable",
                    db=db,
                    workdir_path="projects/manual",
                )
            assert nested.value.status_code == 409
            with pytest.raises(HTTPException) as implicit:
                await create_implicit_project(uid=uid, db=db)
            assert implicit.value.status_code == 409

            db.add(
                Project(
                    id=historical_child_id,
                    uid=uid,
                    selection_status="selectable",
                    workdir_path="projects/manual",
                    directory_mode="linked",
                )
            )
            await db.flush()
            with pytest.raises(HTTPException) as active:
                await resolve_conversation_workdir_binding(
                    conversation=SimpleNamespace(project_id=parent_id), uid=uid, db=db
                )
            assert active.value.status_code == 409
            with pytest.raises(HTTPException) as new_thread:
                await conversation_service.create_thread_view(
                    agent_slug="main",
                    request_id=None,
                    title="No orphan",
                    metadata={},
                    project_id=parent_id,
                    db=db,
                    current_uid=uid,
                )
            assert new_thread.value.status_code == 409
            assert await db.scalar(select(func.count()).select_from(Conversation).where(Conversation.uid == uid)) == 0
            await db.rollback()
    finally:
        async with session_factory() as db:
            await db.execute(delete(Conversation).where(Conversation.uid == uid))
            await db.execute(delete(Project).where(Project.id.in_([parent_id, historical_child_id])))
            await db.execute(delete(User).where(User.uid == uid))
            await db.commit()
        await engine.dispose()


@pytest.mark.parametrize("operation", ["download", "save"])
async def test_artifact_waits_for_new_project_owner_before_file_access(monkeypatch, tmp_path, operation):
    """未提交的 Project 绑定必须先于 Artifact Owner 判定和文件副作用。"""
    monkeypatch.setattr("yuxi.workspace.paths.get_user_data_dir", lambda: tmp_path)
    engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    uid = f"artifact-race-{uuid4().hex[:12]}"
    current_id, other_id = str(uuid4()), None
    current_path = f"projects/{current_id}"
    ensure_user_workspace(uid)
    current_dir = user_workspace_dir(uid) / current_path
    other_dir = user_workspace_dir(uid) / "clients" / "acme"
    current_dir.mkdir(parents=True)
    other_dir.mkdir(parents=True)
    (current_dir / "report.md").write_bytes(b"current project")
    (other_dir / "report.md").write_bytes(b"other project")

    async def resolve(*, thread_id, uid, db):
        assert thread_id == "thread-1"
        return AuthorizedWorkdir(
            conversation_id=1,
            thread_id=thread_id,
            uid=uid,
            workdir=Workdir.open_existing(uid, current_path),
            project_id=current_id,
            directory_mode="managed",
            project_workdir_paths=tuple(await ProjectRepository(db).list_active_workdir_paths_for_user(uid)),
        )

    monkeypatch.setattr(artifact_service, "resolve_authorized_workdir", resolve)
    artifact_task = None
    try:
        async with session_factory() as setup_db:
            setup_db.add(User(uid=uid, username=uid, password_hash="test-only", role="user"))
            await setup_db.flush()
            setup_db.add(
                Project(
                    id=current_id,
                    uid=uid,
                    workdir_path=current_path,
                    directory_mode="managed",
                    selection_status="implicit",
                )
            )
            await setup_db.commit()

        async with session_factory() as creator_db, session_factory() as artifact_db:
            other = await create_project_record(
                uid=uid,
                name="Other",
                directory_mode="linked",
                selection_status="selectable",
                db=creator_db,
                workdir_path="clients/acme",
            )
            other_id = other.id

            async def access_artifact():
                if operation == "download":
                    return await artifact_service.resolve_thread_artifact_view(
                        thread_id="thread-1",
                        current_uid=uid,
                        db=artifact_db,
                        path="/home/gem/user-data/clients/acme/report.md",
                    )
                return await artifact_service.save_thread_artifact_to_workspace_view(
                    thread_id="thread-1",
                    current_uid=uid,
                    db=artifact_db,
                    path=f"/home/gem/user-data/{current_path}/report.md",
                    destination_path="/clients/acme",
                )

            artifact_task = asyncio.create_task(access_artifact())
            await asyncio.sleep(0.2)
            try:
                assert not artifact_task.done(), "Artifact proceeded before the Project Owner transaction committed"
            finally:
                await creator_db.commit()

            with pytest.raises(HTTPException) as denied:
                await asyncio.wait_for(artifact_task, 5)
            assert denied.value.status_code == 403
            assert (other_dir / "report.md").read_bytes() == b"other project"
            await artifact_db.rollback()
    finally:
        if artifact_task is not None and not artifact_task.done():
            artifact_task.cancel()
            with suppress(asyncio.CancelledError):
                await artifact_task
        elif artifact_task is not None:
            with suppress(HTTPException):
                response = await artifact_task
                if hasattr(response, "background") and response.background is not None:
                    await response.background()
        async with session_factory() as cleanup_db:
            await cleanup_db.execute(delete(Project).where(Project.id.in_([current_id, other_id])))
            await cleanup_db.execute(delete(User).where(User.uid == uid))
            await cleanup_db.commit()
        await engine.dispose()


async def test_deleted_project_cannot_accept_old_thread_attachment_during_rebind(monkeypatch, tmp_path):
    """删除事务占有路径锁时，旧线程附件不能写入等待重绑的目录。"""
    monkeypatch.setattr("yuxi.workspace.paths.get_user_data_dir", lambda: tmp_path)
    engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    uid = f"attachment-rebind-{uuid4().hex[:12]}"
    project_id, thread_id = str(uuid4()), str(uuid4())
    workdir_path = f"projects/{project_id}"
    ensure_user_workspace(uid)
    project_dir = user_workspace_dir(uid) / workdir_path
    project_dir.mkdir(parents=True)
    deleted = asyncio.Event()
    release = asyncio.Event()
    materialize_entered = asyncio.Event()
    release_materialize = asyncio.Event()
    original_soft_delete = ProjectRepository.soft_delete_with_conversations

    async def paused_delete(self, project, *, deleted_at):
        deleted.set()
        await release.wait()
        return await original_soft_delete(self, project, deleted_at=deleted_at)

    monkeypatch.setattr(ProjectRepository, "soft_delete_with_conversations", paused_delete)

    async def observed_materialize(**_kwargs):
        materialize_entered.set()
        await release_materialize.wait()
        raise HTTPException(status_code=409, detail="old thread reached file materialization")

    monkeypatch.setattr(conversation_service, "_materialize_attachment_files", observed_materialize)
    delete_task = upload_task = None
    try:
        async with session_factory() as setup_db:
            setup_db.add(User(uid=uid, username=uid, password_hash="test-only", role="user"))
            await setup_db.flush()
            setup_db.add(
                Project(
                    id=project_id,
                    uid=uid,
                    workdir_path=workdir_path,
                    directory_mode="linked",
                    selection_status="selectable",
                )
            )
            await setup_db.flush()
            setup_db.add(Conversation(thread_id=thread_id, uid=uid, agent_id="test-agent", project_id=project_id))
            await setup_db.commit()

        async with session_factory() as deleting_db, session_factory() as uploading_db:
            delete_task = asyncio.create_task(delete_project_view(uid=uid, project_id=project_id, db=deleting_db))
            await asyncio.wait_for(deleted.wait(), 5)
            upload_task = asyncio.create_task(
                conversation_service.upload_thread_attachment_view(
                    thread_id=thread_id,
                    file=UploadFile(filename="proof.bin", file=io.BytesIO(b"old thread write")),
                    db=uploading_db,
                    current_uid=uid,
                )
            )
            await asyncio.sleep(0.2)
            assert not materialize_entered.is_set(), "old thread reached file materialization during Project deletion"
            release.set()
            await asyncio.wait_for(delete_task, 5)
            release_materialize.set()
            with pytest.raises(HTTPException) as denied:
                await asyncio.wait_for(upload_task, 5)
            assert denied.value.status_code == 404
            assert not (project_dir / "uploads").exists()
    finally:
        release.set()
        release_materialize.set()
        for task in (delete_task, upload_task):
            if task is not None:
                with suppress(BaseException):
                    await task
        async with session_factory() as cleanup_db:
            await cleanup_db.execute(delete(Conversation).where(Conversation.thread_id == thread_id))
            await cleanup_db.execute(delete(Project).where(Project.id == project_id))
            await cleanup_db.execute(delete(User).where(User.uid == uid))
            await cleanup_db.commit()
        await engine.dispose()


async def test_attachment_rollback_preserves_file_after_project_owner_changes(monkeypatch, tmp_path):
    """旧线程补偿不得删除后来由新 Project 接管的原附件路径。"""
    monkeypatch.setattr("yuxi.workspace.paths.get_user_data_dir", lambda: tmp_path)
    engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    uid = f"attachment-rollback-{uuid4().hex[:12]}"
    project_id, old_id = str(uuid4()), str(uuid4())
    workdir_path = f"projects/{old_id}"
    ensure_user_workspace(uid)
    attachment = user_workspace_dir(uid) / workdir_path / "uploads" / "proof.txt"
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

            primary = RuntimeError("failed before commit")
            await conversation_service._rollback_materialized_attachments(
                db,
                Workdir.open_existing(uid, workdir_path),
                ["/uploads/proof.txt"],
                primary,
                uid=uid,
                project_id=old_id,
            )
            assert attachment.read_bytes() == b"new owner data"
            await db.rollback()
    finally:
        async with session_factory() as db:
            await db.execute(delete(Project).where(Project.id == project_id))
            await db.execute(delete(User).where(User.uid == uid))
            await db.commit()
        await engine.dispose()
