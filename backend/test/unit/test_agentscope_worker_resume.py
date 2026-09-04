"""AgentScope worker 的恢复流程与附件边界测试。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from yuxi.agentscope import gateway, worker_job
from yuxi.agentscope.gateway import GatewayRoundResult


class _MinimalClient:
    """仅实现 resume 路径所需接口的最小桩。"""

    async def set_permission_mode(self, uid, agent_id, session_id, mode):
        self.permission_mode = mode


pytestmark = pytest.mark.unit


async def test_worker_preserves_mixed_approval_decisions(monkeypatch):
    """worker 公共恢复路径必须按原顺序保留 approve/reject。"""
    run = SimpleNamespace(
        uid="u",
        id="run",
        conversation_thread_id="thread",
        agent_slug="agent",
        input_payload={},
    )
    message = SimpleNamespace(extra_metadata={"resume": {"decisions": [{"type": "approve"}, {"type": "reject"}]}})
    mapping = SimpleNamespace(
        agentscope_agent_id="agent-id",
        agentscope_session_id="session-id",
    )
    pending = {
        "type": "REQUIRE_USER_CONFIRM",
        "reply_id": "reply",
        "tool_calls": [{"id": "one"}, {"id": "two"}],
    }
    captured: list[bool] = []

    async def _resume(client, current_run, current_mapping, event, approved):
        captured.extend(approved)
        return GatewayRoundResult("completed", "ok", "", 1)

    monkeypatch.setattr(worker_job, "ensure_thread_session", AsyncMock(return_value=mapping))
    monkeypatch.setattr(worker_job, "load_pending_confirm", AsyncMock(return_value=pending))
    monkeypatch.setattr(worker_job, "_resume_and_collect", _resume)
    monkeypatch.setattr(worker_job, "finalize_run", AsyncMock())
    clear = AsyncMock()
    monkeypatch.setattr(worker_job, "clear_pending_confirm", clear)

    await worker_job._execute_resume(SimpleNamespace(), _MinimalClient(), run, message)

    assert captured == [True, False]
    clear.assert_awaited_once_with("thread")


async def test_resume_failure_keeps_pending_confirmation(monkeypatch):
    """会话读取/恢复失败时不得清除 Redis 挂起事实。"""
    run = SimpleNamespace(
        uid="u",
        id="run",
        conversation_thread_id="thread",
        agent_slug="agent",
        input_payload={},
    )
    message = SimpleNamespace(extra_metadata={"resume": {"decisions": [{"type": "approve"}]}})
    mapping = SimpleNamespace(
        agentscope_agent_id="agent-id",
        agentscope_session_id="session-id",
    )
    pending = {
        "type": "REQUIRE_USER_CONFIRM",
        "reply_id": "reply",
        "tool_calls": [{"id": "one"}],
    }
    monkeypatch.setattr(worker_job, "ensure_thread_session", AsyncMock(return_value=mapping))
    monkeypatch.setattr(worker_job, "load_pending_confirm", AsyncMock(return_value=pending))
    monkeypatch.setattr(
        worker_job,
        "_resume_and_collect",
        AsyncMock(side_effect=RuntimeError("session read failed")),
    )
    clear = AsyncMock()
    monkeypatch.setattr(worker_job, "clear_pending_confirm", clear)

    with pytest.raises(RuntimeError, match="session read failed"):
        await worker_job._execute_resume(SimpleNamespace(), _MinimalClient(), run, message)
    clear.assert_not_awaited()


async def test_resume_streams_reasoning_tools_and_terminal_event(monkeypatch):
    """审批恢复后应继续输出完整事件，并返回可持久化的工具结果。"""
    run = SimpleNamespace(
        uid="u",
        id="run",
        request_id="request",
        conversation_thread_id="thread",
    )
    mapping = SimpleNamespace(
        agentscope_agent_id="agent-id",
        agentscope_session_id="session-id",
        model_spec="provider:model",
    )
    confirm_event = {
        "reply_id": "reply",
        "tool_calls": [
            {"id": "tc1", "name": "query_kb", "input": '{"query":"退款"}'},
        ],
    }
    events = [
        {
            "type": "TOOL_RESULT_START",
            "reply_id": "reply",
            "tool_call_id": "tc1",
            "tool_call_name": "query_kb",
        },
        {
            "type": "TOOL_RESULT_TEXT_DELTA",
            "reply_id": "reply",
            "tool_call_id": "tc1",
            "delta": "知识库结果",
        },
        {
            "type": "TOOL_RESULT_END",
            "reply_id": "reply",
            "tool_call_id": "tc1",
            "state": "success",
        },
        {"type": "THINKING_BLOCK_DELTA", "reply_id": "reply", "delta": "整理结果"},
        {"type": "TEXT_BLOCK_DELTA", "reply_id": "reply", "delta": "最终答案"},
        {"type": "REPLY_END", "reply_id": "reply", "finished_reason": "completed"},
    ]
    queue = asyncio.Queue()
    for event in events:
        queue.put_nowait(event)
    emitted: list[tuple[str, dict]] = []

    async def _append(run_id, event, payload, thread_id=None):
        emitted.append((event, payload))

    client = SimpleNamespace(resume_confirm=AsyncMock())
    monkeypatch.setattr(worker_job, "start_event_pump", lambda *args, **kwargs: (queue, None))
    monkeypatch.setattr(worker_job, "start_cancel_watcher", lambda *args, **kwargs: None)
    monkeypatch.setattr(worker_job, "cancel_tasks", AsyncMock())
    monkeypatch.setattr(worker_job, "has_active_child_runs", AsyncMock(return_value=False))
    monkeypatch.setattr(worker_job, "SUBSCRIBE_SETTLE_SECONDS", 0)
    monkeypatch.setattr(worker_job, "_collect_asking_tool_calls", AsyncMock(return_value=confirm_event["tool_calls"]))
    monkeypatch.setattr(gateway, "append_run_stream_event", _append)

    result = await worker_job._resume_and_collect(
        client,
        run,
        mapping,
        confirm_event,
        [True],
    )

    assert result.text == "最终答案"
    assert result.reasoning == "整理结果"
    assert result.tool_calls == [
        {
            "id": "tc1",
            "name": "query_kb",
            "args": {"query": "退款"},
            "output": "知识库结果",
            "status": "success",
            "error_message": None,
        }
    ]
    assert any(name == "messages" for name, _ in emitted)
    assert emitted[-1] == (
        "end",
        {
            "status": "completed",
            "chunk": {
                "request_id": "request",
                "response": None,
                "thread_id": None,
                "status": "finished",
            },
        },
    )


async def test_external_resume_waits_for_team_worker_wakeup(monkeypatch):
    """用户回答后创建子智能体时，父 Run 必须收齐 worker 回报再结束。"""
    run = SimpleNamespace(
        uid="u",
        id="run",
        request_id="request",
        conversation_thread_id="thread",
    )
    mapping = SimpleNamespace(
        agentscope_agent_id="agent-id",
        agentscope_session_id="session-id",
        model_spec="provider:model",
    )
    pending_event = {
        "reply_id": "question-reply",
        "tool_calls": [{"id": "question", "name": "ask_user_question", "input": {}}],
    }
    events = [
        {"type": "REPLY_START", "reply_id": "leader-1"},
        {
            "type": "TOOL_CALL_START",
            "reply_id": "leader-1",
            "tool_call_id": "create-worker",
            "tool_call_name": "AgentCreate",
        },
        {"type": "TEXT_BLOCK_DELTA", "reply_id": "leader-1", "delta": "已派发。"},
        {"type": "REPLY_END", "reply_id": "leader-1", "finished_reason": "completed"},
        {"type": "REPLY_START", "reply_id": "leader-2"},
        {
            "type": "TEXT_BLOCK_DELTA",
            "reply_id": "leader-2",
            "delta": "子智能体分析完成。",
        },
        {"type": "REPLY_END", "reply_id": "leader-2", "finished_reason": "completed"},
    ]
    queue = asyncio.Queue()
    for event in events:
        queue.put_nowait(event)
    emitted: list[tuple[str, dict]] = []

    async def _append(run_id, event, payload, thread_id=None):
        emitted.append((event, payload))

    client = SimpleNamespace(resume_external_execution=AsyncMock())
    monkeypatch.setattr(worker_job, "start_event_pump", lambda *args, **kwargs: (queue, None))
    monkeypatch.setattr(worker_job, "start_cancel_watcher", lambda *args, **kwargs: None)
    monkeypatch.setattr(worker_job, "cancel_tasks", AsyncMock())
    monkeypatch.setattr(worker_job, "has_active_child_runs", AsyncMock(return_value=False))
    monkeypatch.setattr(worker_job, "SUBSCRIBE_SETTLE_SECONDS", 0)
    monkeypatch.setattr(gateway, "append_run_stream_event", _append)
    monkeypatch.setattr(gateway, "TEAM_QUIESCE_SECONDS", 0.01)
    monkeypatch.setattr(gateway, "TEAM_CHILD_WAIT_SEGMENT_SECONDS", 1.0)

    result = await worker_job._resume_external_and_collect(
        client,
        run,
        mapping,
        pending_event,
        "开始分析",
    )

    assert result.text == "已派发。子智能体分析完成。"
    assert len([payload for event, payload in emitted if event == "end"]) == 1


async def test_permission_resume_requires_decisions(monkeypatch):
    """审批挂起不能因缺少 decisions 静默退化为普通用户消息。"""
    run = SimpleNamespace(
        uid="u",
        id="run",
        conversation_thread_id="thread",
        agent_slug="agent",
        input_payload={},
    )
    mapping = SimpleNamespace(agentscope_agent_id="agent-id", agentscope_session_id="session-id")
    monkeypatch.setattr(worker_job, "ensure_thread_session", AsyncMock(return_value=mapping))
    monkeypatch.setattr(
        worker_job,
        "load_pending_confirm",
        AsyncMock(return_value={"type": "REQUIRE_USER_CONFIRM"}),
    )
    execute = AsyncMock()
    monkeypatch.setattr(worker_job, "execute_run", execute)

    with pytest.raises(ValueError, match="缺少有效 decisions"):
        await worker_job._execute_resume(
            SimpleNamespace(),
            _MinimalClient(),
            run,
            SimpleNamespace(extra_metadata={"resume": {}}),
        )
    execute.assert_not_awaited()


async def test_new_pending_event_replaces_old_one(monkeypatch):
    """恢复后再次挂起时必须覆盖旧事件，不能随后清空。"""
    event = {"type": "REQUIRE_EXTERNAL_EXECUTION", "reply_id": "new"}
    result = GatewayRoundResult("interrupted", "", "", 1, parked="external", pending_confirm=event)
    store = AsyncMock()
    clear = AsyncMock()
    monkeypatch.setattr(worker_job, "store_pending_confirm", store)
    monkeypatch.setattr(worker_job, "clear_pending_confirm", clear)

    await worker_job._replace_pending_confirm("thread", result)

    store.assert_awaited_once_with("thread", event)
    clear.assert_not_awaited()


class _AttachmentRepository:
    """附件仓储测试替身。"""

    def __init__(self, attachments):
        self.attachments = attachments
        self.bind = AsyncMock(return_value=attachments)

    async def get_attachments(self, conversation_id):
        return self.attachments

    async def bind_attachments_to_request(self, conversation_id, request_id, file_ids):
        return await self.bind(conversation_id, request_id, file_ids)


def _attachment_run():
    return SimpleNamespace(uid="u", conversation_id=1, request_id="request"), SimpleNamespace(
        agentscope_agent_id="agent", agentscope_session_id="session"
    )


async def test_attachments_bind_only_after_all_uploads_succeed():
    """附件上传全部成功后才绑定，并保持用户选择顺序。"""
    repo = _AttachmentRepository(
        [
            {"file_id": "a", "file_name": "a.txt", "storage_path": "/tmp/a"},
            {"file_id": "b", "file_name": "b.pdf", "storage_path": "/tmp/b"},
        ]
    )
    client = SimpleNamespace(
        upload_workspace_file=AsyncMock(side_effect=["/workspace/uploads/b.pdf", "/workspace/uploads/a.txt"])
    )
    run, mapping = _attachment_run()
    message = SimpleNamespace(content="read", extra_metadata={"attachment_file_ids": ["b", "a"]})

    text = await worker_job._materialize_run_attachments(repo, client, run=run, input_message=message, mapping=mapping)

    assert text.endswith("- /workspace/uploads/b.pdf\n- /workspace/uploads/a.txt")
    repo.bind.assert_awaited_once_with(1, "request", ["b", "a"])


@pytest.mark.parametrize(
    ("file_ids", "attachments", "message"),
    [
        (["missing"], [], "附件不存在"),
        (["a"], [{"file_id": "a", "request_id": "other"}], "已绑定其他请求"),
        (["a", "a"], [{"file_id": "a"}], "重复值"),
        ([str(index) for index in range(11)], [], "最多 10 项"),
    ],
)
async def test_attachment_validation_fails_before_binding(file_ids, attachments, message):
    """缺失、已绑定、重复和超量附件均不得产生绑定副作用。"""
    repo = _AttachmentRepository(attachments)
    client = SimpleNamespace(upload_workspace_file=AsyncMock())
    run, mapping = _attachment_run()
    input_message = SimpleNamespace(content="read", extra_metadata={"attachment_file_ids": file_ids})

    with pytest.raises(ValueError, match=message):
        await worker_job._materialize_run_attachments(
            repo, client, run=run, input_message=input_message, mapping=mapping
        )
    repo.bind.assert_not_awaited()


async def test_upload_failure_does_not_bind_attachment():
    """源文件缺失或远端上传失败时保留附件供重试。"""
    repo = _AttachmentRepository([{"file_id": "a", "file_name": "a.txt", "storage_path": "/missing/a"}])
    client = SimpleNamespace(upload_workspace_file=AsyncMock(side_effect=FileNotFoundError("missing")))
    run, mapping = _attachment_run()
    message = SimpleNamespace(content="read", extra_metadata={"attachment_file_ids": ["a"]})

    with pytest.raises(FileNotFoundError, match="missing"):
        await worker_job._materialize_run_attachments(repo, client, run=run, input_message=message, mapping=mapping)
    repo.bind.assert_not_awaited()


async def test_projection_value_error_marks_run_failed(monkeypatch):
    """配置投影拒绝请求时 worker 必须结束 Run，不能遗留 running。"""
    from yuxi.services import run_queue_service

    run = SimpleNamespace(
        id="run",
        uid="u",
        status="dispatched",
        input_message_id=1,
        run_type="chat",
        input_payload={},
        conversation_id=1,
        request_id="request",
        conversation_thread_id="thread",
        agent_slug="agent",
    )
    run_repo = SimpleNamespace(
        get_run=AsyncMock(return_value=run),
        mark_running=AsyncMock(),
        set_terminal_status=AsyncMock(return_value=(SimpleNamespace(status="failed"), True)),
    )
    db = SimpleNamespace(
        execute=AsyncMock(
            return_value=SimpleNamespace(scalar_one_or_none=lambda: SimpleNamespace(content="hello", extra_metadata={}))
        ),
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )

    class _SessionContext:
        async def __aenter__(self):
            return db

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    monkeypatch.setattr(worker_job.pg_manager, "get_async_session_context", lambda: _SessionContext())
    monkeypatch.setattr(worker_job, "AgentRunRepository", lambda current_db: run_repo)
    monkeypatch.setattr(
        worker_job,
        "ConversationRepository",
        lambda current_db: SimpleNamespace(
            get_message_by_id=AsyncMock(return_value=SimpleNamespace(content="hello", extra_metadata={})),
            set_message_delivery_status=AsyncMock(),
        ),
    )
    monkeypatch.setattr(
        worker_job,
        "ensure_thread_session",
        AsyncMock(side_effect=ValueError("模型供应商 disabled 未启用")),
    )
    emit = AsyncMock()
    monkeypatch.setattr(worker_job, "_emit_end_event", emit)
    dispatch = AsyncMock()
    monkeypatch.setattr(worker_job, "dispatch_next_request", dispatch)
    monkeypatch.setattr(run_queue_service, "has_cancel_signal", AsyncMock(return_value=False))

    await worker_job.execute_agent_run_job("run")

    run_repo.set_terminal_status.assert_awaited_once_with(
        "run", status="failed", error_message="执行失败: 模型供应商 disabled 未启用"
    )
    emit.assert_awaited_once()
    dispatch.assert_awaited_once_with(uid="u", agent_slug="agent", thread_id="thread")


async def test_worker_persists_reasoning_and_tool_calls(monkeypatch):
    """公共 worker 路径应保存推理内容与工具生命周期，刷新后仍可恢复。"""
    run = SimpleNamespace(
        id="run",
        uid="u",
        status="dispatched",
        input_message_id=1,
        run_type="chat",
        input_payload={},
        conversation_id=1,
        request_id="request",
        conversation_thread_id="thread",
        agent_slug="agent",
    )
    input_message = SimpleNamespace(content="查询", extra_metadata={}, image_content=None)
    output_message = SimpleNamespace(id=2)
    run_repo = SimpleNamespace(
        get_run=AsyncMock(return_value=run),
        mark_running=AsyncMock(),
        set_output_message=AsyncMock(),
    )
    conv_repo = SimpleNamespace(
        get_message_by_id=AsyncMock(return_value=input_message),
        add_message_by_thread_id=AsyncMock(return_value=output_message),
        add_tool_call=AsyncMock(),
        set_message_delivery_status=AsyncMock(),
    )
    db = SimpleNamespace(commit=AsyncMock())

    class _SessionContext:
        async def __aenter__(self):
            return db

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    result = GatewayRoundResult(
        "completed",
        "最终答案",
        "检索知识库",
        7,
        usage={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        tool_calls=[
            {
                "id": "tc1",
                "name": "query_kb",
                "args": {"query": "退款"},
                "output": "结果",
                "status": "success",
                "error_message": None,
            }
        ],
    )
    mapping = SimpleNamespace(agentscope_agent_id="agent-id", agentscope_session_id="session-id")

    monkeypatch.setattr(worker_job.pg_manager, "get_async_session_context", lambda: _SessionContext())
    monkeypatch.setattr(worker_job, "AgentRunRepository", lambda current_db: run_repo)
    monkeypatch.setattr(worker_job, "ConversationRepository", lambda current_db: conv_repo)
    monkeypatch.setattr(worker_job, "ensure_thread_session", AsyncMock(return_value=mapping))
    monkeypatch.setattr(worker_job, "_apply_permission_mode", AsyncMock())
    monkeypatch.setattr(worker_job, "execute_run", AsyncMock(return_value=result))
    monkeypatch.setattr(worker_job, "dispatch_next_request", AsyncMock())

    await worker_job.execute_agent_run_job("run")

    message_kwargs = conv_repo.add_message_by_thread_id.await_args.kwargs
    assert message_kwargs["content"] == "最终答案"
    assert message_kwargs["extra_metadata"]["additional_kwargs"] == {"reasoning_content": "检索知识库"}
    conv_repo.add_tool_call.assert_awaited_once_with(
        message_id=2,
        tool_name="query_kb",
        tool_input={"query": "退款"},
        tool_output="结果",
        status="success",
        error_message=None,
        langgraph_tool_call_id="tc1",
    )
    run_repo.set_output_message.assert_awaited_once_with("run", 2)


async def test_apply_permission_mode_maps_always_trust_to_bypass():
    """完全信任映射 bypass 并写入会话；default 保持逐次审批。"""
    run = SimpleNamespace(uid="u", input_payload={"tool_approval_mode": "always_trust"})
    mapping = SimpleNamespace(agentscope_agent_id="a", agentscope_session_id="s")
    client = _MinimalClient()
    await worker_job._apply_permission_mode(client, run, mapping)
    assert client.permission_mode == "bypass"

    run_default = SimpleNamespace(uid="u", input_payload={"tool_approval_mode": "default"})
    await worker_job._apply_permission_mode(client, run_default, mapping)
    assert client.permission_mode == "default"
