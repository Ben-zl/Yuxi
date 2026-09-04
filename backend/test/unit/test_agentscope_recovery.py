"""AgentScope 持久化 reply 恢复 Yuxi Run 的测试。"""

from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

from yuxi.agentscope import recovery
from yuxi.agentscope.recovery import (
    RecoveredReply,
    _latest_finished_reply,
    parse_recovered_reply,
    update_existing_tool_calls,
)
from yuxi.storage.postgres.models_business import Message, ToolCall


def test_team_worker_recovery_prefers_successful_team_say_and_sanitizes_output():
    """TeamSay 已送达时，后续本地持久化错误不能把 child 留在 running。"""
    message = {
        "role": "assistant",
        "finished_reason": "error",
        "error": {"type": "unknown", "message": "reply failed"},
        "usage": {"input_tokens": 12, "output_tokens": 3},
        "content": [
            {"type": "text", "text": "分析完成"},
            {
                "type": "tool_call",
                "id": "read-1",
                "name": "Read",
                "input": '{"path":"a"}',
            },
            {
                "type": "tool_result",
                "id": "read-1",
                "name": "Read",
                "state": "success",
                "output": [{"type": "text", "text": "binary\x00payload"}],
            },
            {
                "type": "tool_call",
                "id": "say-1",
                "name": "TeamSay",
                "input": '{"content":"done","to":"leader"}',
            },
            {
                "type": "tool_result",
                "id": "say-1",
                "name": "TeamSay",
                "state": "success",
                "output": [{"type": "text", "text": "Delivered"}],
            },
        ],
    }

    recovered = parse_recovered_reply(message, request_id="request-1", team_worker=True)

    assert recovered.status == "completed"
    assert recovered.error_type is None
    assert recovered.tool_calls[0]["output"] == "binary�payload"
    assert recovered.usage["total_tokens"] == 15


def test_latest_finished_reply_uses_finished_time_not_reply_creation_time():
    """审批恢复可继续原 reply，归属判断必须使用 finished_at。"""
    messages = [
        {
            "id": "continued",
            "role": "assistant",
            "created_at": "2026-09-03T17:03:18",
            "finished_at": "2026-09-03T17:04:07",
            "finished_reason": "completed",
        },
        {
            "id": "latest",
            "role": "assistant",
            "created_at": "2026-09-03T17:21:01",
            "finished_at": "2026-09-03T17:21:59",
            "finished_reason": "completed",
        },
    ]

    result = _latest_finished_reply(
        messages,
        started_at=datetime.fromisoformat("2026-09-03T17:03:40"),
    )

    assert result["id"] == "latest"


def test_latest_finished_reply_ignores_empty_setup_error_from_stale_reenqueue():
    """恢复扫描不得让后发的空 setup 错误覆盖已经完成的最终答复。"""
    messages = [
        {
            "id": "final",
            "role": "assistant",
            "finished_at": "2026-09-03T17:21:59",
            "finished_reason": "completed",
            "content": [{"type": "text", "text": "最终报告"}],
        },
        {
            "id": "duplicate-retry",
            "role": "assistant",
            "finished_at": "2026-09-04T09:54:53",
            "finished_reason": "error",
            "error": {"type": "setup", "message": "could not prepare"},
            "content": [],
        },
    ]

    result = _latest_finished_reply(
        messages,
        started_at=datetime.fromisoformat("2026-09-03T17:03:40"),
    )

    assert result["id"] == "final"


async def test_update_existing_tool_calls_closes_consumed_pending_record(monkeypatch):
    """恢复已完成 reply 时必须同步原 pending ToolCall，页面不能继续转圈。"""
    existing = SimpleNamespace(
        message_id=41,
        tool_name="ask_user_question",
        tool_input={},
        tool_output="",
        status="pending",
        error_message=None,
    )
    db = SimpleNamespace()
    repository = SimpleNamespace(
        get_pending_tool_call_for_conversation=AsyncMock(return_value=existing),
    )
    monkeypatch.setattr(recovery, "ConversationRepository", lambda _db: repository)

    updated_ids, message_ids = await update_existing_tool_calls(
        db,
        [
            {
                "id": "question-1",
                "name": "ask_user_question",
                "args": {"questions": []},
                "output": "用户已回答",
                "status": "success",
                "error_message": None,
            }
        ],
        conversation_id=17,
    )

    assert updated_ids == {"question-1"}
    assert message_ids == {existing.message_id}
    assert existing.status == "success"
    assert existing.tool_output == "用户已回答"
    repository.get_pending_tool_call_for_conversation.assert_awaited_once_with(
        "question-1",
        17,
    )


async def test_update_existing_tool_calls_does_not_cross_conversation(monkeypatch):
    """相同 tool_call_id 不得更新其他用户或线程的工具记录。"""
    repository = SimpleNamespace(
        get_pending_tool_call_for_conversation=AsyncMock(return_value=None),
    )
    monkeypatch.setattr(recovery, "ConversationRepository", lambda _db: repository)

    updated_ids, message_ids = await update_existing_tool_calls(
        SimpleNamespace(),
        [{"id": "shared-id", "name": "ask_user_question", "status": "success"}],
        conversation_id=23,
    )

    assert updated_ids == set()
    assert message_ids == set()
    repository.get_pending_tool_call_for_conversation.assert_awaited_once_with(
        "shared-id",
        23,
    )


async def test_pending_key_is_kept_when_recovered_run_transaction_loses(monkeypatch):
    """Run 终态未成功提交时不得提前清除审批恢复上下文。"""
    from yuxi.agentscope import thread_guard

    run = SimpleNamespace(
        id="run-1",
        uid="u",
        status="running",
        run_type="resume",
        request_id="request-1",
        conversation_id=17,
        conversation_thread_id="thread-1",
        agent_slug="agent-1",
        started_at=datetime.fromisoformat("2026-09-04T09:00:00"),
    )
    query_result = SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [run]))
    dbs = iter(
        (
            SimpleNamespace(execute=AsyncMock(return_value=query_result)),
            SimpleNamespace(),
        )
    )

    @asynccontextmanager
    async def session_context():
        yield next(dbs)

    monkeypatch.setattr(recovery.pg_manager, "get_async_session_context", session_context)
    monkeypatch.setattr(recovery, "reconcile_active_team_child_runs", AsyncMock(return_value=False))
    monkeypatch.setattr(
        recovery,
        "get_thread_session",
        AsyncMock(
            return_value=SimpleNamespace(
                agentscope_agent_id="agent-id",
                agentscope_session_id="session-id",
            )
        ),
    )
    pending = {"reply_id": "pending-reply"}
    monkeypatch.setattr(thread_guard, "load_pending_confirm", AsyncMock(return_value=pending))
    clear_pending = AsyncMock()
    monkeypatch.setattr(thread_guard, "clear_pending_confirm", clear_pending)
    persist = AsyncMock(return_value=False)
    monkeypatch.setattr(recovery, "_persist_recovered_run", persist)
    messages = [
        {
            "id": "pending-reply",
            "role": "assistant",
            "finished_at": "2026-09-04T09:01:00",
            "finished_reason": "completed",
            "content": [],
        },
        {
            "id": "final-reply",
            "role": "assistant",
            "finished_at": "2026-09-04T09:02:00",
            "finished_reason": "completed",
            "content": [{"type": "text", "text": "done"}],
        },
    ]
    client = SimpleNamespace(
        get_session_status=AsyncMock(return_value="idle"),
        list_messages=AsyncMock(return_value=messages),
    )

    assert await recovery.reconcile_stale_running_runs(client) == 0
    persist.assert_awaited_once()
    assert persist.await_args.kwargs["existing_tool_calls"] == []
    clear_pending.assert_not_awaited()


async def test_resume_recovery_deduplicates_existing_prefix_and_tool_call(monkeypatch):
    """恢复完整续写 reply 时只新增审批后的正文和新工具调用。"""
    existing_message = SimpleNamespace(
        content="审批前正文",
        extra_metadata={"additional_kwargs": {"reasoning_content": "审批前推理"}},
    )
    existing_call = SimpleNamespace(
        message_id=41,
        tool_name="ask_user_question",
        tool_input={},
        tool_output="",
        status="pending",
        error_message=None,
    )
    added = []

    class _Db:
        def add(self, item):
            added.append(item)

        async def flush(self):
            for item in added:
                if isinstance(item, Message) and item.id is None:
                    item.id = 99

        async def execute(self, _statement):
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [existing_message]))

        async def commit(self):
            return None

    db = _Db()

    @asynccontextmanager
    async def session_context():
        yield db

    run = SimpleNamespace(
        id="resume-run",
        status="running",
        conversation_id=17,
        input_message_id=52,
        request_id="request-1",
        conversation_thread_id="thread-1",
    )
    run_repo = SimpleNamespace(
        get_run=AsyncMock(return_value=run),
        set_terminal_status=AsyncMock(return_value=(run, True)),
        set_output_message=AsyncMock(),
    )
    conversation_repo = SimpleNamespace(
        get_pending_tool_call_for_conversation=AsyncMock(return_value=existing_call),
        set_message_delivery_status=AsyncMock(),
    )
    monkeypatch.setattr(recovery.pg_manager, "get_async_session_context", session_context)
    monkeypatch.setattr(recovery, "AgentRunRepository", lambda _db: run_repo)
    monkeypatch.setattr(recovery, "ConversationRepository", lambda _db: conversation_repo)
    monkeypatch.setattr(recovery, "append_run_stream_event", AsyncMock())
    recovered = RecoveredReply(
        status="completed",
        text="审批前正文审批后正文",
        reasoning="审批前推理审批后推理",
        tool_calls=[
            {
                "id": "question-1",
                "name": "ask_user_question",
                "args": {},
                "output": "用户已回答",
                "status": "success",
                "error_message": None,
            },
            {
                "id": "new-tool",
                "name": "Read",
                "args": {"path": "result.md"},
                "output": "done",
                "status": "success",
                "error_message": None,
            },
        ],
        usage={},
        error_type=None,
        error_message=None,
    )

    assert await recovery._persist_recovered_run(
        run.id,
        recovered,
        existing_tool_calls=[recovered.tool_calls[0]],
    )

    [output_message] = [item for item in added if isinstance(item, Message)]
    persisted_tools = [item for item in added if isinstance(item, ToolCall)]
    assert output_message.content == "审批后正文"
    assert output_message.extra_metadata["additional_kwargs"]["reasoning_content"] == "审批后推理"
    assert [item.langgraph_tool_call_id for item in persisted_tools] == ["new-tool"]
    assert existing_call.status == "success"
