from pathlib import Path
from types import SimpleNamespace

import pytest

from yuxi.agentscope.worker_job import _materialize_run_attachments

pytestmark = pytest.mark.asyncio


class _FakeConversationRepository:
    def __init__(self, attachments: list[dict]):
        self.attachments = attachments
        self.bound: tuple[str, list[str]] | None = None

    async def get_attachments(self, conversation_id: int) -> list[dict]:
        assert conversation_id == 1
        return self.attachments

    async def bind_attachments_to_request(self, conversation_id: int, request_id: str, file_ids: list[str]):
        assert conversation_id == 1
        assert request_id == "request-1"
        self.bound = (request_id, file_ids)
        return [item for item in self.attachments if item["file_id"] in file_ids]


class _FakeAgentScopeClient:
    def __init__(self):
        self.uploads: list[dict] = []

    async def upload_workspace_file(
        self,
        uid: str,
        agent_id: str,
        session_id: str,
        *,
        source_path: str,
        destination: str,
    ) -> str:
        upload_source = Path(source_path)
        self.uploads.append(
            {
                "uid": uid,
                "agent_id": agent_id,
                "session_id": session_id,
                "source_path": source_path,
                "content": upload_source.read_bytes(),
                "destination": destination,
            }
        )
        return destination


async def test_attachment_source_rejects_existing_absolute_path_outside_workdir(tmp_path):
    from yuxi.agentscope.worker_job import _resolve_attachment_source_path

    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")

    with pytest.raises(ValueError, match="Workdir"):
        _resolve_attachment_source_path(
            "user-1",
            str(outside),
            workdir_path="projects/project-1",
        )


async def test_attachment_source_rejects_absolute_symlink_outside_workdir(tmp_path):
    from yuxi.agentscope.worker_job import _resolve_attachment_source_path

    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    link = tmp_path / "attachment.txt"
    link.symlink_to(outside)

    with pytest.raises(ValueError, match="Workdir"):
        _resolve_attachment_source_path(
            "user-1",
            str(link),
            workdir_path="projects/project-1",
        )


async def test_materialize_run_attachments_adds_workspace_paths_without_mutating_input(
    monkeypatch,
    tmp_path,
):
    from yuxi.agentscope import worker_job

    source = tmp_path / "shared" / "user-1" / "workspace" / "projects" / "project-1" / "uploads" / "report.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"pdf")
    monkeypatch.setattr(worker_job, "get_user_data_dir", lambda: tmp_path)
    original_content = "请总结附件"
    input_message = SimpleNamespace(
        content=original_content,
        extra_metadata={"attachment_file_ids": ["file-1"]},
    )
    repository = _FakeConversationRepository(
        [
            {
                "file_id": "file-1",
                "file_name": "report.pdf",
                "status": "uploaded",
                "storage_path": "/home/gem/user-data/projects/project-1/uploads/report.pdf",
                "request_id": None,
            }
        ]
    )
    client = _FakeAgentScopeClient()
    run = SimpleNamespace(conversation_id=1, request_id="request-1", uid="user-1")
    mapping = SimpleNamespace(agentscope_agent_id="agent-1", agentscope_session_id="session-1")

    result = await _materialize_run_attachments(
        repository,
        client,
        run=run,
        input_message=input_message,
        mapping=mapping,
        workdir_path="projects/project-1",
    )

    assert input_message.content == original_content
    assert result == "请总结附件\n\n本次请求附件（workspace 路径）：\n- /workspace/uploads/file-1.pdf"
    assert repository.bound == ("request-1", ["file-1"])
    assert len(client.uploads) == 1
    assert client.uploads[0]["uid"] == "user-1"
    assert client.uploads[0]["agent_id"] == "agent-1"
    assert client.uploads[0]["session_id"] == "session-1"
    assert client.uploads[0]["content"] == b"pdf"
    assert client.uploads[0]["destination"] == "/workspace/uploads/file-1.pdf"
    assert client.uploads[0]["source_path"] != str(source)
    assert not Path(client.uploads[0]["source_path"]).exists()


async def test_materialize_run_attachments_preserves_requested_order_and_parsed_suffix(
    monkeypatch,
    tmp_path,
):
    from yuxi.agentscope import worker_job

    uploads = tmp_path / "shared" / "user-1" / "workspace" / "projects" / "project-1" / "uploads"
    uploads.mkdir(parents=True)
    (uploads / "notes.txt").write_text("notes", encoding="utf-8")
    (uploads / "report.md").write_text("report", encoding="utf-8")
    monkeypatch.setattr(worker_job, "get_user_data_dir", lambda: tmp_path)
    input_message = SimpleNamespace(
        content="比较两个文件",
        extra_metadata={"attachment_file_ids": ["file-2", "file-1"]},
    )
    repository = _FakeConversationRepository(
        [
            {
                "file_id": "file-1",
                "file_name": "notes.txt",
                "status": "uploaded",
                "storage_path": "/home/gem/user-data/projects/project-1/uploads/notes.txt",
                "request_id": None,
            },
            {
                "file_id": "file-2",
                "file_name": "report.pdf",
                "status": "parsed",
                "storage_path": "/home/gem/user-data/projects/project-1/uploads/report.md",
                "request_id": None,
            },
        ]
    )
    client = _FakeAgentScopeClient()
    run = SimpleNamespace(conversation_id=1, request_id="request-1", uid="user-1")
    mapping = SimpleNamespace(agentscope_agent_id="agent-1", agentscope_session_id="session-1")

    result = await _materialize_run_attachments(
        repository,
        client,
        run=run,
        input_message=input_message,
        mapping=mapping,
        workdir_path="projects/project-1",
    )

    assert result.endswith("- /workspace/uploads/file-2.md\n- /workspace/uploads/file-1.txt")
    assert [item["destination"] for item in client.uploads] == [
        "/workspace/uploads/file-2.md",
        "/workspace/uploads/file-1.txt",
    ]


async def test_materialize_run_attachments_ignores_requests_without_attachment_ids():
    input_message = SimpleNamespace(content="继续", extra_metadata={})
    repository = _FakeConversationRepository([])
    client = _FakeAgentScopeClient()

    result = await _materialize_run_attachments(
        repository,
        client,
        run=SimpleNamespace(conversation_id=1, request_id="request-1", uid="user-1"),
        input_message=input_message,
        mapping=SimpleNamespace(agentscope_agent_id="agent-1", agentscope_session_id="session-1"),
    )

    assert result == "继续"
    assert client.uploads == []


async def test_materialize_run_attachments_maps_project_runtime_path_to_worker_host_path(monkeypatch, tmp_path):
    """Project Workdir runtime 路径必须映射到 worker 共享挂载中的真实文件。"""
    from yuxi.agentscope import worker_job

    project_file = tmp_path / "shared" / "user-1" / "workspace" / "projects" / "project-1" / "uploads" / "report.txt"
    project_file.parent.mkdir(parents=True)
    project_file.write_text("内容", encoding="utf-8")
    monkeypatch.setattr(worker_job, "get_user_data_dir", lambda: tmp_path)

    original_content = "请读取附件"
    input_message = SimpleNamespace(
        content=original_content,
        extra_metadata={"attachment_file_ids": ["file-1"]},
    )
    repository = _FakeConversationRepository(
        [
            {
                "file_id": "file-1",
                "file_name": "report.txt",
                "status": "uploaded",
                "storage_path": "/home/gem/user-data/projects/project-1/uploads/report.txt",
                "request_id": None,
            }
        ]
    )
    client = _FakeAgentScopeClient()
    run = SimpleNamespace(conversation_id=1, request_id="request-1", uid="user-1")
    mapping = SimpleNamespace(agentscope_agent_id="agent-1", agentscope_session_id="session-1")

    await worker_job._materialize_run_attachments(
        repository,
        client,
        run=run,
        input_message=input_message,
        mapping=mapping,
        workdir_path="projects/project-1",
    )

    assert client.uploads[0]["content"] == "内容".encode()
    assert client.uploads[0]["source_path"] != str(project_file)
    assert not Path(client.uploads[0]["source_path"]).exists()


async def test_materialize_run_attachments_maps_linked_workdir_runtime_path(monkeypatch, tmp_path):
    """linked Workdir 附件必须映射到 worker 的用户共享挂载。"""
    from yuxi.agentscope import worker_job

    linked_file = tmp_path / "shared" / "user-1" / "workspace" / "clients" / "acme" / "uploads" / "report.txt"
    linked_file.parent.mkdir(parents=True)
    linked_file.write_text("内容", encoding="utf-8")
    monkeypatch.setattr(worker_job, "get_user_data_dir", lambda: tmp_path)

    repository = _FakeConversationRepository(
        [
            {
                "file_id": "file-1",
                "file_name": "report.txt",
                "status": "uploaded",
                "storage_path": "/home/gem/user-data/clients/acme/uploads/report.txt",
                "request_id": None,
            }
        ]
    )
    client = _FakeAgentScopeClient()

    await worker_job._materialize_run_attachments(
        repository,
        client,
        run=SimpleNamespace(conversation_id=1, request_id="request-1", uid="user-1"),
        input_message=SimpleNamespace(
            content="请读取附件",
            extra_metadata={"attachment_file_ids": ["file-1"]},
        ),
        mapping=SimpleNamespace(agentscope_agent_id="agent-1", agentscope_session_id="session-1"),
        workdir_path="clients/acme",
    )

    assert client.uploads[0]["content"] == "内容".encode()
    assert client.uploads[0]["source_path"] != str(linked_file)
    assert not Path(client.uploads[0]["source_path"]).exists()


async def test_materialize_run_attachment_upload_uses_opened_inode_when_source_is_replaced(
    monkeypatch,
    tmp_path,
):
    """校验后替换源路径为 symlink 时，上传内容仍来自已打开的原始 inode。"""
    from yuxi.agentscope import worker_job

    source = tmp_path / "shared" / "user-1" / "workspace" / "projects" / "project-1" / "uploads" / "report.txt"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"trusted")
    attacker = tmp_path / "attacker.txt"
    attacker.write_bytes(b"attacker")
    monkeypatch.setattr(worker_job, "get_user_data_dir", lambda: tmp_path)

    repository = _FakeConversationRepository(
        [
            {
                "file_id": "file-1",
                "file_name": "report.txt",
                "status": "uploaded",
                "storage_path": "/home/gem/user-data/projects/project-1/uploads/report.txt",
                "request_id": None,
            }
        ]
    )
    uploaded: dict[str, object] = {}

    class ReplacingClient:
        async def upload_workspace_file(
            self,
            uid: str,
            agent_id: str,
            session_id: str,
            *,
            source_path: str,
            destination: str,
        ) -> str:
            del uid, agent_id, session_id
            source.unlink()
            source.symlink_to(attacker)
            upload_source = Path(source_path)
            uploaded["path"] = upload_source
            uploaded["content"] = upload_source.read_bytes()
            return destination

    await worker_job._materialize_run_attachments(
        repository,
        ReplacingClient(),
        run=SimpleNamespace(
            conversation_id=1,
            conversation_thread_id="thread-1",
            request_id="request-1",
            uid="user-1",
        ),
        input_message=SimpleNamespace(
            content="read",
            extra_metadata={"attachment_file_ids": ["file-1"]},
        ),
        mapping=SimpleNamespace(
            agentscope_agent_id="agent-1",
            agentscope_session_id="session-1",
        ),
        workdir_path="projects/project-1",
    )

    assert uploaded["content"] == b"trusted"
    assert uploaded["path"] != source
    assert not uploaded["path"].exists()


async def test_materialize_run_attachments_rejects_runtime_path_outside_bound_workdir(monkeypatch, tmp_path):
    """附件记录即使属于同一用户，也不能越过 Conversation 绑定的 Workdir。"""
    from yuxi.agentscope import worker_job

    other_file = tmp_path / "shared" / "user-1" / "workspace" / "clients" / "other" / "uploads" / "report.txt"
    other_file.parent.mkdir(parents=True)
    other_file.write_text("越权内容", encoding="utf-8")
    monkeypatch.setattr(worker_job, "get_user_data_dir", lambda: tmp_path)

    repository = _FakeConversationRepository(
        [
            {
                "file_id": "file-1",
                "file_name": "report.txt",
                "status": "uploaded",
                "storage_path": "/home/gem/user-data/clients/other/uploads/report.txt",
                "request_id": None,
            }
        ]
    )
    client = _FakeAgentScopeClient()

    with pytest.raises(ValueError, match="Workdir"):
        await worker_job._materialize_run_attachments(
            repository,
            client,
            run=SimpleNamespace(conversation_id=1, request_id="request-1", uid="user-1"),
            input_message=SimpleNamespace(
                content="请读取附件",
                extra_metadata={"attachment_file_ids": ["file-1"]},
            ),
            mapping=SimpleNamespace(agentscope_agent_id="agent-1", agentscope_session_id="session-1"),
            workdir_path="clients/acme",
        )

    assert client.uploads == []
