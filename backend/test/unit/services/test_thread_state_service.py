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
            )
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

    async def list_child_runs_for_user(self, created_by_run_id: str, uid: str):
        assert created_by_run_id == "run-1"
        assert uid == "user-1"
        return []


class _ThreadRepository:
    def __init__(self, _db):
        pass

    async def get_by_child_conversation_for_user(self, conversation_id: int, uid: str):
        assert conversation_id == 20
        assert uid == "user-1"
        return None


async def test_thread_state_uses_persisted_run_message_and_pending_confirm(monkeypatch):
    """状态视图不读取 checkpoint，直接返回业务事实与审批缓存。"""
    monkeypatch.setattr(service, "ConversationRepository", _ConversationRepository)
    monkeypatch.setattr(service, "AgentRunRepository", _RunRepository)
    monkeypatch.setattr(service, "SubagentThreadRepository", _ThreadRepository)
    monkeypatch.setattr(service, "_list_artifacts", lambda *_args: [])

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
        "todos": [],
        "files": {},
        "artifacts": [],
        "subagent_runs": [],
        "token_usage": {"total_tokens": 7},
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
