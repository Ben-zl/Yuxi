"""AgentScope 线程状态视图单元测试。"""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from yuxi.services import thread_state_service as service

pytestmark = pytest.mark.unit


class _ConversationRepository:
    def __init__(self, _db):
        pass

    async def get_conversation_by_thread_id(self, thread_id: str):
        if thread_id == "missing":
            return None
        return SimpleNamespace(
            id=20,
            thread_id=thread_id,
            uid="user-1",
            agent_id="main",
            status="active",
        )

    async def get_conversation_by_id(self, conversation_id: int):
        assert conversation_id == 11
        return SimpleNamespace(
            id=11,
            thread_id="parent-thread",
            uid="user-1",
            status="active",
        )

    async def get_messages_by_thread_id(self, thread_id: str):
        assert thread_id == "thread-1"
        return [
            SimpleNamespace(
                id=1,
                role="user",
                content="hello",
                message_type="text",
                image_content=None,
                extra_metadata={"request_id": "req-1"},
                run_id="run-1",
                request_id="req-1",
                delivery_status="complete",
                tool_calls=[],
            ),
            SimpleNamespace(
                id=2,
                role="assistant",
                content="done",
                message_type="text",
                image_content=None,
                extra_metadata={},
                run_id="run-0",
                request_id="req-0",
                delivery_status="complete",
                tool_calls=[
                    SimpleNamespace(
                        tool_name="present_artifacts",
                        tool_input={"filepaths": ["/workspace/outputs/old-report.md"]},
                        status="success",
                        to_dict=lambda: {},
                    )
                ],
            ),
        ]


class _RunRepository:
    def __init__(self, _db):
        pass

    async def get_latest_run_by_thread_for_user(self, thread_id: str, uid: str):
        assert thread_id == "thread-1"
        assert uid == "user-1"
        return SimpleNamespace(
            id="run-1",
            status="interrupted",
            error_type="human_approval_required",
            token_usage={"total_tokens": 7},
        )

    async def list_child_runs_by_thread_for_user(self, thread_id: str, uid: str):
        assert thread_id == "thread-1"
        assert uid == "user-1"
        return [
            SimpleNamespace(
                id=f"child-{index}",
                input_payload={"runtime": {"tool_call_id": f"tool-{index}"}},
                agent_slug=f"worker-{index}",
                conversation_thread_id=f"child-thread-{index}",
                status="completed",
                created_at=None,
                finished_at=None,
                error_message=None,
            )
            for index in (1, 2)
        ] + [
            SimpleNamespace(
                id="legacy-child",
                input_payload={"runtime": {"subagent_name": "Legacy worker"}},
                agent_slug="legacy-worker",
                conversation_thread_id="legacy-child-thread",
                status="completed",
                created_at=None,
                finished_at=None,
                error_message=None,
            )
        ]

    async def list_run_usages_by_thread_for_user(self, thread_id: str, uid: str):
        assert thread_id == "thread-1"
        assert uid == "user-1"
        return [{"total_tokens": 7, "summary_active": True}]


class _ThreadRepository:
    def __init__(self, _db):
        pass

    async def get_by_child_conversation_for_user(self, conversation_id: int, uid: str):
        assert conversation_id == 20
        assert uid == "user-1"
        return None


async def test_load_agentscope_state_does_not_expose_workspace_listing(monkeypatch):
    """状态面板只恢复 Todo 和交付物，不把 AgentScope workspace 当作状态文件。"""

    async def get_mapping(*_args, **_kwargs):
        return SimpleNamespace(agentscope_agent_id="agent-1", agentscope_session_id="session-1")

    class Client:
        async def get_session(self, uid, agent_id, session_id):
            assert (uid, agent_id, session_id) == ("user-1", "agent-1", "session-1")
            return {"state": {"tasks_context": {"tasks": []}}}

    async def list_artifacts(*_args, **_kwargs):
        return ["/home/gem/user-data/outputs/report.html"]

    monkeypatch.setattr(service, "get_thread_session", get_mapping)
    monkeypatch.setattr(service, "AgentScopeServiceClient", lambda *_args, **_kwargs: Client())
    monkeypatch.setattr(service, "list_artifacts", list_artifacts)

    assert await service._load_agentscope_state(None, uid="user-1", thread_id="thread-1") == (
        [],
        {},
        False,
        ["/home/gem/user-data/outputs/report.html"],
        False,
    )


async def test_thread_state_uses_persisted_run_message_and_pending_confirm(monkeypatch):
    """状态视图不读取 checkpoint，直接返回业务事实与审批缓存。"""
    monkeypatch.setattr(service, "ConversationRepository", _ConversationRepository)
    monkeypatch.setattr(service, "AgentRunRepository", _RunRepository)
    monkeypatch.setattr(service, "SubagentThreadRepository", _ThreadRepository)

    async def load_runtime_state(*_args, **_kwargs):
        return (
            [{"id": "task-1", "content": "整理资料", "status": "completed"}],
            {"/workspace/report.md": {"path": "/workspace/report.md", "name": "report.md"}},
            False,
            ["/home/gem/user-data/outputs/report.md"],
            False,
        )

    monkeypatch.setattr(service, "_load_agentscope_state", load_runtime_state)

    async def load_pending(_thread_id: str):
        return {
            "type": "REQUIRE_USER_CONFIRM",
            "tool_calls": [{"id": "call-1", "name": "execute", "input": '{"command":"pytest -q"}'}],
        }

    monkeypatch.setattr(service, "load_pending_confirm", load_pending)

    result = await service.get_thread_state_view(
        thread_id="thread-1",
        current_user=SimpleNamespace(uid="user-1"),
        db=object(),
        include_messages=True,
    )

    assert result["agent_state"] == {
        "todos": [{"id": "task-1", "content": "整理资料", "status": "completed"}],
        "files": {"/workspace/report.md": {"path": "/workspace/report.md", "name": "report.md"}},
        "files_truncated": False,
        "artifacts": [
            "/home/gem/user-data/outputs/old-report.md",
            "/home/gem/user-data/outputs/report.md",
        ],
        "subagent_runs": [
            {
                "id": "tool-1",
                "run_id": "child-1",
                "subagent_slug": "worker-1",
                "child_thread_id": "child-thread-1",
                "status": "completed",
                "events_url": "/api/agent/runs/child-1/events",
                "result_url": "/api/agent/runs/child-1/result",
            },
            {
                "id": "tool-2",
                "run_id": "child-2",
                "subagent_slug": "worker-2",
                "child_thread_id": "child-thread-2",
                "status": "completed",
                "events_url": "/api/agent/runs/child-2/events",
                "result_url": "/api/agent/runs/child-2/result",
            },
            {
                "id": "run:legacy-child",
                "run_id": "legacy-child",
                "subagent_slug": "legacy-worker",
                "subagent_name": "Legacy worker",
                "child_thread_id": "legacy-child-thread",
                "status": "completed",
                "events_url": "/api/agent/runs/legacy-child/events",
                "result_url": "/api/agent/runs/legacy-child/result",
            },
        ],
        "token_usage": {
            "total_tokens": 7,
            "thread": {
                "total": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 7},
                "models": {},
            },
            "summary_active": True,
        },
    }
    assert result["interrupt"]["status"] == "human_approval_required"
    assert result["interrupt"]["run_id"] == "run-1"
    assert result["interrupt"]["approval"]["action_requests"] == [
        {"action": "execute", "args": {"command": "pytest -q"}}
    ]
    assert result["messages"][0]["type"] == "human"
    assert result["messages"][0]["content"] == "hello"


async def test_thread_state_rejects_missing_or_cross_user_thread(monkeypatch):
    """线程不存在或不属于当前用户时统一返回 404。"""
    monkeypatch.setattr(service, "ConversationRepository", _ConversationRepository)

    with pytest.raises(HTTPException) as missing:
        await service.get_thread_state_view(
            thread_id="missing",
            current_user=SimpleNamespace(uid="user-1"),
            db=object(),
        )
    assert missing.value.status_code == 404

    with pytest.raises(HTTPException) as cross_user:
        await service.get_thread_state_view(
            thread_id="thread-1",
            current_user=SimpleNamespace(uid="user-2"),
            db=object(),
        )
    assert cross_user.value.status_code == 404
