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
        self.uploads.append(
            {
                "uid": uid,
                "agent_id": agent_id,
                "session_id": session_id,
                "source_path": source_path,
                "destination": destination,
            }
        )
        return destination


async def test_materialize_run_attachments_adds_workspace_paths_without_mutating_input():
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
                "storage_path": "/tmp/report.pdf",
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
    )

    assert input_message.content == original_content
    assert result == "请总结附件\n\n本次请求附件（workspace 路径）：\n- /workspace/uploads/file-1.pdf"
    assert repository.bound == ("request-1", ["file-1"])
    assert client.uploads == [
        {
            "uid": "user-1",
            "agent_id": "agent-1",
            "session_id": "session-1",
            "source_path": "/tmp/report.pdf",
            "destination": "/workspace/uploads/file-1.pdf",
        }
    ]


async def test_materialize_run_attachments_preserves_requested_order_and_parsed_suffix():
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
                "storage_path": "/tmp/notes.txt",
                "request_id": None,
            },
            {
                "file_id": "file-2",
                "file_name": "report.pdf",
                "status": "parsed",
                "storage_path": "/tmp/report.md",
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
