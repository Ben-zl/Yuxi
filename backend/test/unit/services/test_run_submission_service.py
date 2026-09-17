from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock

import pytest
from fastapi import HTTPException
from yuxi.services import run_submission_service as svc
from yuxi.services.agent_request_queue_service import IntakeResult
from yuxi.services.input_message_service import build_chat_input_message


@pytest.fixture(autouse=True)
def isolate_submission_advisory_lock(monkeypatch):
    """单测的假数据库不模拟 PostgreSQL 事务锁。"""
    monkeypatch.setattr(svc, "_lock_submission_request", AsyncMock())
    monkeypatch.setattr(svc, "lock_project_workdir_changes", AsyncMock())


class _EmptyRequestRepo:
    def __init__(self, db):
        del db

    async def get_by_request_id(self, request_id):
        del request_id
        return None


class _EmptyRunRepo:
    def __init__(self, db):
        del db

    async def get_run_by_request_id(self, request_id):
        del request_id
        return None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source", "channel", "detail"),
    [
        ("x" * 33, "web", "Run origin source 不能超过 32 个字符"),
        ("chat", "x" * 33, "Run origin channel 不能超过 32 个字符"),
    ],
)
async def test_submit_run_command_rejects_overlong_origin_before_repository_access(source, channel, detail):
    command = svc.RunSubmissionCommand(
        department_id=11,
        agent_slug="translator",
        thread_id="thread-1",
        request_id="req-1",
        input_message=build_chat_input_message("hello"),
        origin=svc.RunOrigin(source=source, channel=channel),
    )

    with pytest.raises(HTTPException) as exc_info:
        await svc.submit_run_command(
            command=command,
            current_user=SimpleNamespace(uid="user-1", department_id=11),
            db=object(),
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == detail


@pytest.mark.asyncio
async def test_submit_run_command_shares_conversation_intake_and_finalize(monkeypatch: pytest.MonkeyPatch):
    calls: dict[str, object] = {}
    current_user = SimpleNamespace(uid="user-1", role="user", department_id=11)

    class Db:
        commit = AsyncMock()

        @asynccontextmanager
        async def begin_nested(self):
            yield

    class AgentRepo:
        def __init__(self, db):
            del db

        async def get_visible_by_slug(self, *, slug: str, user, kind="main"):
            assert user is current_user
            assert kind == "main"
            return SimpleNamespace(slug=slug, backend_id="ChatbotAgent")

        async def get_visible_for_update_by_slug(self, *, slug: str, user, kind="main"):
            calls["agent_locked"] = slug
            return await self.get_visible_by_slug(slug=slug, user=user, kind=kind)

    class ConvRepo:
        def __init__(self, db):
            del db

        async def get_conversation_by_thread_id(self, thread_id: str):
            calls["thread_id"] = thread_id
            return None

        async def add_conversation(self, **kwargs):
            calls["conversation"] = kwargs
            return SimpleNamespace(
                id=1,
                thread_id=kwargs["thread_id"],
                project_id=kwargs["project_id"],
            )

    async def fake_intake_request(**kwargs):
        calls["intake"] = kwargs
        return SimpleNamespace(
            request_id="req-1",
            status="dispatched",
            queue_policy="enqueue",
            queue_position=None,
            message_id=10,
            run_id="run-1",
            thread_id="thread-1",
        )

    async def fake_finalize_intake(**kwargs):
        calls["finalize"] = kwargs

    monkeypatch.setattr(svc, "AgentRepository", AgentRepo)
    monkeypatch.setattr(svc, "AgentRunRequestRepository", _EmptyRequestRepo)
    monkeypatch.setattr(svc, "AgentRunRepository", _EmptyRunRepo)
    monkeypatch.setattr(svc, "ConversationRepository", ConvRepo)

    async def fake_create_implicit_project(**kwargs):
        calls["project"] = kwargs
        return SimpleNamespace(
            id="11111111-1111-4111-8111-111111111111",
            workdir_path="projects/11111111-1111-4111-8111-111111111111",
            directory_mode="managed",
        )

    async def fake_resolve_binding(**kwargs):
        del kwargs
        return (
            "projects/11111111-1111-4111-8111-111111111111",
            SimpleNamespace(directory_mode="managed"),
        )

    monkeypatch.setattr(svc, "create_implicit_project", fake_create_implicit_project)
    monkeypatch.setattr(svc, "resolve_conversation_workdir_binding", fake_resolve_binding)
    monkeypatch.setattr(svc.agent_manager, "get_agent", lambda backend_id: object())
    monkeypatch.setattr(svc, "intake_request", fake_intake_request)
    monkeypatch.setattr(svc, "finalize_intake", fake_finalize_intake)

    command = svc.RunSubmissionCommand(
        department_id=11,
        agent_slug="translator",
        thread_id="thread-1",
        request_id="req-1",
        input_message=build_chat_input_message("hello"),
        origin=svc.RunOrigin(
            source="agent_call",
            channel="api",
            external_id="external-1",
            metadata={
                "source": "spoofed",
                "channel": "spoofed",
                "agent_invocation_meta": {"trace_id": "trace-1"},
            },
        ),
        request_metadata={"request_id": "req-1", "channel": "spoofed"},
        model_spec="provider:model",
        create_conversation=True,
        conversation_title="Agent Call Run",
    )

    result = await svc.submit_run_command(command=command, current_user=current_user, db=Db())

    assert calls["agent_locked"] == "translator"
    assert calls["conversation"]["metadata"] == {
        "source": "agent_call",
        "channel": "api",
        "agent_invocation_meta": {"trace_id": "trace-1"},
    }
    assert calls["conversation"]["project_id"] == "11111111-1111-4111-8111-111111111111"
    assert calls["intake"]["source"] == "agent_call"
    assert calls["intake"]["channel"] == "api"
    assert calls["intake"]["external_id"] == "external-1"
    assert calls["intake"]["origin_metadata"] == {"agent_invocation_meta": {"trace_id": "trace-1"}}
    assert calls["intake"]["meta"] == {
        "request_id": "req-1",
        "channel": "api",
        "agent_invocation_meta": {"trace_id": "trace-1"},
    }
    assert result == {
        "request_id": "req-1",
        "status": "dispatched",
        "queue_policy": "enqueue",
        "queue_position": None,
        "message_id": 10,
        "run_id": "run-1",
        "stream_url": "/api/agent/runs/run-1/events",
        "request_events_url": None,
        "thread_id": "thread-1",
    }
    assert calls["finalize"]["intake"].run_id == "run-1"
    assert calls["finalize"]["uid"] == "user-1"
    assert calls["finalize"]["workdir_path"] == "projects/11111111-1111-4111-8111-111111111111"
    assert calls["finalize"]["materialize_managed"] is True


@pytest.mark.asyncio
async def test_submit_run_command_requires_existing_conversation_for_web_chat(
    monkeypatch: pytest.MonkeyPatch,
):
    current_user = SimpleNamespace(uid="user-1", role="user", department_id=11)

    class AgentRepo:
        def __init__(self, db):
            del db

        async def get_visible_by_slug(self, *, slug: str, user, kind="main"):
            del user, kind
            return SimpleNamespace(slug=slug, backend_id="ChatbotAgent")

    class ConvRepo:
        def __init__(self, db):
            del db

        async def get_conversation_by_thread_id(self, thread_id: str):
            del thread_id
            return None

        async def add_conversation(self, **kwargs):
            raise AssertionError(f"web chat must not create a conversation: {kwargs}")

    monkeypatch.setattr(svc, "AgentRepository", AgentRepo)
    monkeypatch.setattr(svc, "AgentRunRequestRepository", _EmptyRequestRepo)
    monkeypatch.setattr(svc, "AgentRunRepository", _EmptyRunRepo)
    monkeypatch.setattr(svc, "ConversationRepository", ConvRepo)
    monkeypatch.setattr(svc.agent_manager, "get_agent", lambda backend_id: object())

    command = svc.RunSubmissionCommand(
        department_id=11,
        agent_slug="translator",
        thread_id="missing-thread",
        request_id="req-1",
        input_message=build_chat_input_message("hello"),
        origin=svc.RunOrigin(source="chat", channel="web"),
    )

    with pytest.raises(HTTPException) as exc_info:
        await svc.submit_run_command(command=command, current_user=current_user, db=object())

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_submit_run_command_maps_runtime_resource_validation_to_422(
    monkeypatch: pytest.MonkeyPatch,
):
    """提交时发现 Skill 等运行资源已不可访问时必须返回客户端校验错误。"""
    current_user = SimpleNamespace(uid="user-1", role="user", department_id=11)
    conversation = SimpleNamespace(id=1, thread_id="thread-1", project_id="project-1")

    class AgentRepo:
        def __init__(self, _db):
            pass

        async def get_visible_by_slug(self, **_kwargs):
            return SimpleNamespace(slug="translator", backend_id="ChatbotAgent")

        async def get_visible_for_update_by_slug(self, **_kwargs):
            return SimpleNamespace(slug="translator", backend_id="ChatbotAgent")

    class ConvRepo:
        def __init__(self, _db):
            pass

        async def get_conversation_by_thread_id(self, _thread_id):
            return conversation

    async def resolve_binding(**_kwargs):
        return "projects/project-1", SimpleNamespace(id="project-1", directory_mode="managed")

    monkeypatch.setattr(svc, "AgentRepository", AgentRepo)
    monkeypatch.setattr(svc, "AgentRunRequestRepository", _EmptyRequestRepo)
    monkeypatch.setattr(svc, "AgentRunRepository", _EmptyRunRepo)
    monkeypatch.setattr(svc, "ConversationRepository", ConvRepo)
    monkeypatch.setattr(svc, "resolve_conversation_workdir_binding", resolve_binding)
    monkeypatch.setattr(svc.agent_manager, "get_agent", lambda _backend_id: object())
    monkeypatch.setattr(
        svc,
        "intake_request",
        AsyncMock(side_effect=ValueError("智能体引用了当前用户不可访问的 Skill: revoked-skill")),
    )

    command = svc.RunSubmissionCommand(
        department_id=11,
        agent_slug="translator",
        thread_id="thread-1",
        request_id="req-resource-validation",
        input_message=build_chat_input_message("hello"),
        origin=svc.RunOrigin(source="chat", channel="web"),
    )
    db = SimpleNamespace(rollback=AsyncMock())

    with pytest.raises(HTTPException) as exc_info:
        await svc.submit_run_command(command=command, current_user=current_user, db=db)

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == "智能体引用了当前用户不可访问的 Skill: revoked-skill"
    db.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_final_agent_lock_failure_rolls_back_persisted_attachments(monkeypatch: pytest.MonkeyPatch):
    """附件落盘后 Agent 被删除时必须补偿附件副作用。"""
    current_user = SimpleNamespace(uid="user-1", role="user", department_id=11)
    conversation = SimpleNamespace(id=1, thread_id="thread-1", project_id="project-1")
    rollback = []

    class Db:
        rollback = AsyncMock()

    class AgentRepo:
        def __init__(self, _db):
            pass

        async def get_visible_by_slug(self, **_kwargs):
            return SimpleNamespace(slug="translator", backend_id="ChatbotAgent")

        async def get_visible_for_update_by_slug(self, **_kwargs):
            return None

    class ConvRepo:
        def __init__(self, _db):
            pass

        async def get_conversation_by_thread_id(self, _thread_id):
            return conversation

    async def persist(**_kwargs):
        return [{"file_id": "file-1"}]

    async def rollback_attachments(**kwargs):
        rollback.append(kwargs)

    async def resolve_binding(**_kwargs):
        return "projects/project-1", SimpleNamespace(id="project-1", directory_mode="managed")

    import yuxi.services.attachment_service as attachment_service

    monkeypatch.setattr(svc, "AgentRepository", AgentRepo)
    monkeypatch.setattr(svc, "AgentRunRequestRepository", _EmptyRequestRepo)
    monkeypatch.setattr(svc, "AgentRunRepository", _EmptyRunRepo)
    monkeypatch.setattr(svc, "ConversationRepository", ConvRepo)
    monkeypatch.setattr(svc, "resolve_conversation_workdir_binding", resolve_binding)
    monkeypatch.setattr(svc.agent_manager, "get_agent", lambda _backend_id: object())
    monkeypatch.setattr(attachment_service, "persist_run_submission_attachments", persist)
    monkeypatch.setattr(attachment_service, "rollback_run_submission_attachments", rollback_attachments)
    monkeypatch.setattr(svc, "_require_compensation_attachment_owner", AsyncMock())
    db = Db()

    command = svc.RunSubmissionCommand(
        department_id=11,
        agent_slug="translator",
        thread_id="thread-1",
        request_id="req-attachment",
        input_message=build_chat_input_message("hello"),
        origin=svc.RunOrigin(source="chat", channel="web"),
        attachments=(svc.RunSubmissionAttachment(file_name="a.txt", media_type="text/plain", content=b"a"),),
    )

    with pytest.raises(HTTPException, match="智能体不存在"):
        await svc.submit_run_command(command=command, current_user=current_user, db=db)

    db.rollback.assert_awaited_once()
    assert rollback[0]["records"] == [{"file_id": "file-1"}]
    assert rollback[0]["workdir_path"] == "projects/project-1"


@pytest.mark.asyncio
@pytest.mark.parametrize("agent_visible", [True, False])
async def test_existing_request_with_attachments_returns_without_new_file(
    monkeypatch: pytest.MonkeyPatch, agent_visible
):
    """相同 request_id 重试不能给已提交输入附加孤立文件。"""
    current_user = SimpleNamespace(uid="user-1", role="user", department_id=11)
    existing = SimpleNamespace(
        department_id=11,
        uid="user-1",
        agent_slug="translator",
        conversation_thread_id="thread-1",
        source="chat",
        channel="web",
        external_id=None,
        queue_policy="enqueue",
        request_id="req-existing",
        status="queued",
        input_message_id=11,
        dispatched_run_id=None,
    )

    class RequestRepo:
        def __init__(self, _db):
            pass

        async def get_by_request_id(self, _request_id):
            return existing

        async def get_queue_position(self, _request_id):
            return 1

    class AgentRepo:
        def __init__(self, _db):
            pass

        async def get_visible_by_slug(self, **_kwargs):
            return SimpleNamespace(slug="translator", backend_id="ChatbotAgent") if agent_visible else None

        async def get_visible_for_update_by_slug(self, **_kwargs):
            return SimpleNamespace(slug="translator", backend_id="ChatbotAgent")

    class ConvRepo:
        def __init__(self, _db):
            pass

        async def get_conversation_by_thread_id(self, _thread_id):
            return SimpleNamespace(id=1, thread_id="thread-1", project_id="project-1")

    class Db:
        commit = AsyncMock()

    persist = AsyncMock(return_value=[{"file_id": "orphan"}])
    intake = AsyncMock(
        return_value=IntakeResult(
            request_id="req-existing",
            status="queued",
            queue_policy="enqueue",
            message_id=11,
            thread_id="thread-1",
            queue_position=1,
        )
    )
    monkeypatch.setattr(svc, "AgentRepository", AgentRepo)
    monkeypatch.setattr(svc, "AgentRunRequestRepository", RequestRepo)
    monkeypatch.setattr(svc, "AgentRunRepository", _EmptyRunRepo)
    monkeypatch.setattr(svc, "ConversationRepository", ConvRepo)
    monkeypatch.setattr(
        svc,
        "resolve_conversation_workdir_binding",
        AsyncMock(return_value=("clients/acme", SimpleNamespace(directory_mode="linked"))),
    )
    monkeypatch.setattr(svc.agent_manager, "get_agent", lambda _backend_id: object())
    monkeypatch.setattr(svc, "intake_request", intake)
    monkeypatch.setattr(svc, "finalize_intake", AsyncMock())
    import yuxi.services.attachment_service as attachment_service

    monkeypatch.setattr(attachment_service, "persist_run_submission_attachments", persist)
    result = await svc.submit_run_command(
        command=svc.RunSubmissionCommand(
            department_id=11,
            agent_slug="translator",
            thread_id="thread-1",
            request_id="req-existing",
            input_message=build_chat_input_message("hello"),
            origin=svc.RunOrigin(source="chat", channel="web"),
            attachments=(svc.RunSubmissionAttachment(file_name="a.txt", media_type="text/plain", content=b"a"),),
        ),
        current_user=current_user,
        db=Db(),
    )
    assert result["message_id"] == 11
    assert result["queue_position"] == 1
    persist.assert_not_awaited()
    intake.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancelled_submission_rolls_back_attachments_and_created_workdir(monkeypatch: pytest.MonkeyPatch):
    """提交任务取消时仍需补偿已落盘附件和本次创建的 managed Workdir。"""
    current_user = SimpleNamespace(uid="user-1", role="user", department_id=11)
    project = SimpleNamespace(
        id="11111111-1111-4111-8111-111111111111",
        workdir_path="projects/11111111-1111-4111-8111-111111111111",
        directory_mode="managed",
    )
    conversation = SimpleNamespace(id=1, thread_id="thread-1", project_id=project.id)

    class Db:
        @asynccontextmanager
        async def begin_nested(self):
            yield

    class AgentRepo:
        def __init__(self, _db):
            pass

        async def get_visible_by_slug(self, **_kwargs):
            return SimpleNamespace(slug="translator", backend_id="ChatbotAgent")

        async def get_visible_for_update_by_slug(self, **_kwargs):
            return SimpleNamespace(slug="translator", backend_id="ChatbotAgent")

    class ConvRepo:
        def __init__(self, _db):
            pass

        async def get_conversation_by_thread_id(self, _thread_id):
            return None

        async def add_conversation(self, **_kwargs):
            return conversation

    async def create_project(**_kwargs):
        return project

    async def resolve_binding(**_kwargs):
        return project.workdir_path, project

    async def persist(**_kwargs):
        return [{"file_id": "file-1"}]

    async def cancel_intake(**_kwargs):
        raise asyncio.CancelledError

    rollback = AsyncMock()
    import yuxi.services.attachment_service as attachment_service

    monkeypatch.setattr(svc, "AgentRepository", AgentRepo)
    monkeypatch.setattr(svc, "AgentRunRequestRepository", _EmptyRequestRepo)
    monkeypatch.setattr(svc, "AgentRunRepository", _EmptyRunRepo)
    monkeypatch.setattr(svc, "ConversationRepository", ConvRepo)
    monkeypatch.setattr(svc, "create_implicit_project", create_project)
    monkeypatch.setattr(svc, "resolve_conversation_workdir_binding", resolve_binding)
    monkeypatch.setattr(svc.agent_manager, "get_agent", lambda _backend_id: object())
    monkeypatch.setattr(attachment_service, "persist_run_submission_attachments", persist)
    monkeypatch.setattr(svc, "intake_request", cancel_intake)
    monkeypatch.setattr(svc, "_rollback_submission_side_effects", rollback)

    command = svc.RunSubmissionCommand(
        department_id=11,
        agent_slug="translator",
        thread_id="thread-1",
        request_id="req-cancelled",
        input_message=build_chat_input_message("hello"),
        origin=svc.RunOrigin(source="agent_call", channel="api"),
        create_conversation=True,
        attachments=(svc.RunSubmissionAttachment(file_name="a.txt", media_type="text/plain", content=b"a"),),
    )

    with pytest.raises(asyncio.CancelledError):
        await svc.submit_run_command(command=command, current_user=current_user, db=Db())

    rollback.assert_awaited_once_with(
        db=ANY,
        uid="user-1",
        workdir_path=project.workdir_path,
        stored_attachments=[{"file_id": "file-1"}],
        delete_created_managed_workdir=True,
        owning_project_id=project.id,
    )


@pytest.mark.asyncio
async def test_submission_cancellation_preserves_cancel_when_compensation_fails(
    monkeypatch: pytest.MonkeyPatch,
):
    """补偿异常只能附加诊断，不能替换提交任务的原始取消。"""
    current_user = SimpleNamespace(uid="user-1", role="user", department_id=11)
    project = SimpleNamespace(
        id="11111111-1111-4111-8111-111111111111",
        workdir_path="projects/11111111-1111-4111-8111-111111111111",
        directory_mode="managed",
    )
    conversation = SimpleNamespace(id=1, thread_id="thread-1", project_id=project.id)

    class Db:
        @asynccontextmanager
        async def begin_nested(self):
            yield

    class AgentRepo:
        def __init__(self, _db):
            pass

        async def get_visible_by_slug(self, **_kwargs):
            return SimpleNamespace(slug="translator", backend_id="ChatbotAgent")

        async def get_visible_for_update_by_slug(self, **_kwargs):
            return SimpleNamespace(slug="translator", backend_id="ChatbotAgent")

    class ConvRepo:
        def __init__(self, _db):
            pass

        async def get_conversation_by_thread_id(self, _thread_id):
            return None

        async def add_conversation(self, **_kwargs):
            return conversation

    async def cancel_intake(**_kwargs):
        raise asyncio.CancelledError

    async def fail_compensation(**_kwargs):
        raise RuntimeError("rollback failed")

    monkeypatch.setattr(svc, "AgentRepository", AgentRepo)
    monkeypatch.setattr(svc, "AgentRunRequestRepository", _EmptyRequestRepo)
    monkeypatch.setattr(svc, "AgentRunRepository", _EmptyRunRepo)
    monkeypatch.setattr(svc, "ConversationRepository", ConvRepo)
    monkeypatch.setattr(svc, "create_implicit_project", AsyncMock(return_value=project))
    monkeypatch.setattr(
        svc,
        "resolve_conversation_workdir_binding",
        AsyncMock(return_value=(project.workdir_path, project)),
    )
    monkeypatch.setattr(svc.agent_manager, "get_agent", lambda _backend_id: object())
    monkeypatch.setattr(svc, "intake_request", cancel_intake)
    monkeypatch.setattr(svc, "_rollback_submission_side_effects", fail_compensation)

    command = svc.RunSubmissionCommand(
        department_id=11,
        agent_slug="translator",
        thread_id="thread-1",
        request_id="req-cancelled-compensation",
        input_message=build_chat_input_message("hello"),
        origin=svc.RunOrigin(source="agent_call", channel="api"),
        create_conversation=True,
    )

    with pytest.raises(asyncio.CancelledError) as exc_info:
        await svc.submit_run_command(command=command, current_user=current_user, db=Db())

    assert exc_info.value.__notes__ == ["Run submission compensation also failed: RuntimeError"]


@pytest.mark.asyncio
async def test_submission_compensation_attempts_all_steps_when_each_fails(monkeypatch: pytest.MonkeyPatch):
    """rollback、附件和 Workdir 清理必须互不短路。"""
    from yuxi.services.project_service import lock_project_workdir_changes

    monkeypatch.setattr(svc, "lock_project_workdir_changes", lock_project_workdir_changes)
    calls: list[str] = []

    class Db:
        async def rollback(self):
            calls.append("rollback")
            raise RuntimeError("database rollback failed")

        async def execute(self, _statement, params=None):
            calls.append("lock" if params else "read owners")
            return SimpleNamespace(all=lambda: [], scalars=lambda: SimpleNamespace(all=lambda: []))

    async def rollback_attachments(**_kwargs):
        calls.append("attachments")
        raise RuntimeError("attachment rollback failed")

    class FailingWorkspace:
        def __init__(self, uid: str):
            assert uid == "user-1"

        def remove_authorized_empty_directory(self, path: str, *, root: str):
            calls.append("workdir")
            raise RuntimeError("workdir rollback failed")

    import yuxi.services.attachment_service as attachment_service

    monkeypatch.setattr(attachment_service, "rollback_run_submission_attachments", rollback_attachments)
    monkeypatch.setattr(svc, "Workspace", FailingWorkspace)

    failures = await svc._rollback_submission_side_effects(
        db=Db(),
        uid="user-1",
        workdir_path="projects/11111111-1111-4111-8111-111111111111",
        stored_attachments=[{"file_id": "file-1"}],
        delete_created_managed_workdir=True,
    )

    assert calls == ["rollback", "lock", "read owners", "attachments", "lock", "read owners", "workdir"]
    assert [type(failure).__name__ for failure in failures] == [
        "RuntimeError",
        "RuntimeError",
        "RuntimeError",
    ]


@pytest.mark.asyncio
async def test_submission_compensation_preserves_managed_dir_with_overlapping_project(monkeypatch: pytest.MonkeyPatch):
    """linked Project 后来绑定相同目录时，补偿不能递归删除其 Workdir。"""
    from yuxi.services.project_service import lock_project_workdir_changes

    monkeypatch.setattr(svc, "lock_project_workdir_changes", lock_project_workdir_changes)
    calls: list[str] = []

    class QueryResult:
        def scalars(self):
            return self

        def all(self):
            return ["projects/11111111-1111-4111-8111-111111111111/nested"]

    class Db:
        async def rollback(self):
            calls.append("rollback")

        async def execute(self, _statement, params=None):
            calls.append("lock" if params else "read owners")
            return QueryResult()

    class WorkspaceWithOtherOwner:
        def __init__(self, _uid: str):
            pass

        def delete_authorized_path(self, _path: str, *, root: str):
            calls.append("recursive delete")

        def remove_authorized_empty_directory(self, _path: str, *, root: str):
            calls.append("empty delete")

    monkeypatch.setattr(svc, "Workspace", WorkspaceWithOtherOwner)

    failures = await svc._rollback_submission_side_effects(
        db=Db(),
        uid="user-1",
        workdir_path="projects/11111111-1111-4111-8111-111111111111",
        stored_attachments=[],
        delete_created_managed_workdir=True,
    )

    assert calls == ["rollback", "lock", "read owners"]
    assert len(failures) == 1
    assert "overlapping" in str(failures[0]).lower()


@pytest.mark.asyncio
async def test_submission_compensation_waits_through_repeated_cancellation(monkeypatch: pytest.MonkeyPatch):
    """补偿中再次取消不得让请求先于数据库清理任务退出。"""
    started = asyncio.Event()
    release = asyncio.Event()
    finished: list[str] = []

    async def blocking_compensation(**_kwargs):
        started.set()
        await release.wait()
        finished.append("rollback")
        return []

    monkeypatch.setattr(svc, "_rollback_submission_side_effects", blocking_compensation)
    primary = asyncio.CancelledError()
    task = asyncio.create_task(
        svc._compensate_submission_failure(
            primary,
            db=object(),
            uid="user-1",
            workdir_path=None,
            stored_attachments=[],
            delete_created_managed_workdir=False,
            owning_project_id="project-1",
        )
    )
    await started.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    await task
    assert finished == ["rollback"]


@pytest.mark.asyncio
async def test_submission_commit_ack_failure_preserves_persisted_run_and_files(monkeypatch: pytest.MonkeyPatch):
    """服务器已提交但确认丢失时不得清理其附件和 managed Workdir。"""
    current_user = SimpleNamespace(uid="user-1", role="user", department_id=11)
    project = SimpleNamespace(
        id="11111111-1111-4111-8111-111111111111",
        workdir_path="projects/11111111-1111-4111-8111-111111111111",
        directory_mode="managed",
    )
    conversation = SimpleNamespace(id=1, thread_id="thread-1", project_id=project.id)

    class Db:
        @asynccontextmanager
        async def begin_nested(self):
            yield

        async def commit(self):
            raise RuntimeError("commit acknowledgement lost")

    class AgentRepo:
        def __init__(self, _db):
            pass

        async def get_visible_by_slug(self, **_kwargs):
            return SimpleNamespace(slug="translator", backend_id="ChatbotAgent")

        async def get_visible_for_update_by_slug(self, **_kwargs):
            return SimpleNamespace(slug="translator", backend_id="ChatbotAgent")

    class ConvRepo:
        def __init__(self, _db):
            pass

        async def get_conversation_by_thread_id(self, _thread_id):
            return None

        async def add_conversation(self, **_kwargs):
            return conversation

    async def intake(**_kwargs):
        return SimpleNamespace(run_id="run-1")

    async def committed(**_kwargs):
        return True

    rollback = AsyncMock()
    monkeypatch.setattr(svc, "AgentRepository", AgentRepo)
    monkeypatch.setattr(svc, "AgentRunRequestRepository", _EmptyRequestRepo)
    monkeypatch.setattr(svc, "AgentRunRepository", _EmptyRunRepo)
    monkeypatch.setattr(svc, "ConversationRepository", ConvRepo)
    monkeypatch.setattr(svc, "create_implicit_project", AsyncMock(return_value=project))
    monkeypatch.setattr(
        svc,
        "resolve_conversation_workdir_binding",
        AsyncMock(return_value=(project.workdir_path, project)),
    )
    monkeypatch.setattr(svc.agent_manager, "get_agent", lambda _backend_id: object())
    monkeypatch.setattr(svc, "intake_request", intake)
    monkeypatch.setattr(svc, "_committed_submission_visible", committed, raising=False)
    monkeypatch.setattr(svc, "_rollback_submission_side_effects", rollback)
    command = svc.RunSubmissionCommand(
        department_id=11,
        agent_slug="translator",
        thread_id="thread-1",
        request_id="req-ack-lost",
        input_message=build_chat_input_message("hello"),
        origin=svc.RunOrigin(source="agent_call", channel="api"),
        create_conversation=True,
    )

    with pytest.raises(RuntimeError, match="acknowledgement lost"):
        await svc.submit_run_command(command=command, current_user=current_user, db=Db())

    rollback.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "commit_error",
    [RuntimeError("commit failed"), OSError("connection lost"), asyncio.CancelledError()],
)
async def test_submission_commit_failure_compensates_filesystem_side_effects(
    monkeypatch: pytest.MonkeyPatch,
    commit_error: BaseException,
):
    """数据库 commit 未成功时必须补偿附件与本次 managed Workdir。"""
    current_user = SimpleNamespace(uid="user-1", role="user", department_id=11)
    project = SimpleNamespace(
        id="11111111-1111-4111-8111-111111111111",
        workdir_path="projects/11111111-1111-4111-8111-111111111111",
        directory_mode="managed",
    )
    conversation = SimpleNamespace(id=1, thread_id="thread-1", project_id=project.id)

    class Db:
        @asynccontextmanager
        async def begin_nested(self):
            yield

        async def commit(self):
            raise commit_error

    class AgentRepo:
        def __init__(self, _db):
            pass

        async def get_visible_by_slug(self, **_kwargs):
            return SimpleNamespace(slug="translator", backend_id="ChatbotAgent")

        async def get_visible_for_update_by_slug(self, **_kwargs):
            return SimpleNamespace(slug="translator", backend_id="ChatbotAgent")

    class ConvRepo:
        def __init__(self, _db):
            pass

        async def get_conversation_by_thread_id(self, _thread_id):
            return None

        async def add_conversation(self, **_kwargs):
            return conversation

    async def intake(**_kwargs):
        return SimpleNamespace(
            request_id="req-commit",
            status="dispatched",
            queue_policy="enqueue",
            queue_position=0,
            message_id=1,
            run_id="run-1",
            thread_id="thread-1",
        )

    rollback = AsyncMock()
    finalize = AsyncMock()
    monkeypatch.setattr(svc, "AgentRepository", AgentRepo)
    monkeypatch.setattr(svc, "AgentRunRequestRepository", _EmptyRequestRepo)
    monkeypatch.setattr(svc, "AgentRunRepository", _EmptyRunRepo)
    monkeypatch.setattr(svc, "ConversationRepository", ConvRepo)
    monkeypatch.setattr(svc, "create_implicit_project", AsyncMock(return_value=project))
    monkeypatch.setattr(
        svc,
        "resolve_conversation_workdir_binding",
        AsyncMock(return_value=(project.workdir_path, project)),
    )
    monkeypatch.setattr(svc.agent_manager, "get_agent", lambda _backend_id: object())
    monkeypatch.setattr(svc, "intake_request", intake)
    monkeypatch.setattr(svc, "finalize_intake", finalize)
    monkeypatch.setattr(svc, "_rollback_submission_side_effects", rollback)
    monkeypatch.setattr(svc, "_committed_submission_visible", AsyncMock(return_value=False), raising=False)

    command = svc.RunSubmissionCommand(
        department_id=11,
        agent_slug="translator",
        thread_id="thread-1",
        request_id="req-commit",
        input_message=build_chat_input_message("hello"),
        origin=svc.RunOrigin(source="agent_call", channel="api"),
        create_conversation=True,
    )

    with pytest.raises(type(commit_error), match="commit failed" if isinstance(commit_error, RuntimeError) else None):
        await svc.submit_run_command(command=command, current_user=current_user, db=Db())

    if isinstance(commit_error, (asyncio.CancelledError, OSError)):
        rollback.assert_not_awaited()
    else:
        rollback.assert_awaited_once()
    finalize.assert_not_awaited()


@pytest.mark.asyncio
async def test_submission_post_commit_failure_keeps_committed_facts_for_recovery(monkeypatch: pytest.MonkeyPatch):
    """commit 成功后的物化或投递失败必须保留事实供 pending recovery 接管。"""
    current_user = SimpleNamespace(uid="user-1", role="user", department_id=11)
    conversation = SimpleNamespace(id=1, thread_id="thread-1", project_id="project-1")
    project = SimpleNamespace(directory_mode="managed")

    class Db:
        commit = AsyncMock()

    class AgentRepo:
        def __init__(self, _db):
            pass

        async def get_visible_by_slug(self, **_kwargs):
            return SimpleNamespace(slug="translator", backend_id="ChatbotAgent")

        async def get_visible_for_update_by_slug(self, **_kwargs):
            return SimpleNamespace(slug="translator", backend_id="ChatbotAgent")

    class ConvRepo:
        def __init__(self, _db):
            pass

        async def get_conversation_by_thread_id(self, _thread_id):
            return conversation

    async def intake(**_kwargs):
        return SimpleNamespace(
            request_id="req-finalize",
            status="dispatched",
            queue_policy="enqueue",
            queue_position=0,
            message_id=1,
            run_id="run-1",
            thread_id="thread-1",
        )

    monkeypatch.setattr(svc, "AgentRepository", AgentRepo)
    monkeypatch.setattr(svc, "AgentRunRequestRepository", _EmptyRequestRepo)
    monkeypatch.setattr(svc, "AgentRunRepository", _EmptyRunRepo)
    monkeypatch.setattr(svc, "ConversationRepository", ConvRepo)
    monkeypatch.setattr(
        svc,
        "resolve_conversation_workdir_binding",
        AsyncMock(return_value=("projects/project-1", project)),
    )
    monkeypatch.setattr(svc.agent_manager, "get_agent", lambda _backend_id: object())
    monkeypatch.setattr(svc, "intake_request", intake)
    monkeypatch.setattr(svc, "finalize_intake", AsyncMock(side_effect=RuntimeError("enqueue failed")))
    rollback = AsyncMock()
    monkeypatch.setattr(svc, "_rollback_submission_side_effects", rollback)

    command = svc.RunSubmissionCommand(
        department_id=11,
        agent_slug="translator",
        thread_id="thread-1",
        request_id="req-finalize",
        input_message=build_chat_input_message("hello"),
        origin=svc.RunOrigin(source="chat", channel="web"),
    )

    with pytest.raises(RuntimeError, match="enqueue failed"):
        await svc.submit_run_command(command=command, current_user=current_user, db=Db())

    Db.commit.assert_awaited_once()
    rollback.assert_not_awaited()


async def test_submit_rejects_department_mismatch(monkeypatch):
    """提交部门与当前有效部门不一致时拒绝，且不创建 request/run。"""
    from fastapi import HTTPException

    current_user = SimpleNamespace(uid="user-1", role="user", department_id=12)
    command = svc.RunSubmissionCommand(
        department_id=11,
        agent_slug="chatbot",
        thread_id="thread-1",
        request_id="req-dept-mismatch",
        input_message=build_chat_input_message("hello"),
        origin=svc.RunOrigin(source="chat", channel="web"),
    )
    with pytest.raises(HTTPException) as exc:
        await svc.submit_run_command(command=command, current_user=current_user, db=object())
    assert exc.value.status_code == 403
