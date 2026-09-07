"""网关 team 静默收束与多工具审批补全的单元测试（迁移工单 14 缺口修复）。"""

import asyncio

import pytest

from yuxi.agentscope import gateway


class _StubClient:
    """按脚本吐事件的会话客户端桩：事件耗尽后挂起模拟静默。"""

    def __init__(self, events, messages=None):
        self._events = events
        self._messages = messages or []
        self.triggered = None

    async def trigger_chat(self, uid, agent_id, session_id, text, image_content=None):
        self.triggered = text

    async def interrupt_session(self, uid, agent_id, session_id):
        return None

    async def list_messages(self, uid, agent_id, session_id):
        return self._messages

    async def stream_events(self, uid, agent_id, session_id, read_timeout=180.0):
        for event in self._events:
            yield event
        await asyncio.Event().wait()  # 挂起直至被取消/超时


@pytest.fixture
def capture_events(monkeypatch):
    calls: list[tuple[str, dict]] = []

    async def _append(run_id, event, payload, thread_id=None):
        calls.append((event, payload))

    monkeypatch.setattr(gateway, "append_run_stream_event", _append)
    monkeypatch.setattr(gateway, "TEAM_QUIESCE_SECONDS", 0.3)
    monkeypatch.setattr(gateway, "TEAM_CHILD_WAIT_SEGMENT_SECONDS", 3.0)
    return calls


async def test_team_round_collects_wakeup_continuation(capture_events):
    """team 轮次 REPLY_END 后等待续写：静默窗口内的回报文本被聚合。"""
    events = [
        {"type": "REPLY_START", "reply_id": "r1"},
        {"type": "TOOL_CALL_START", "reply_id": "r1", "tool_call_id": "t1", "tool_call_name": "TeamCreate"},
        {"type": "TEXT_BLOCK_DELTA", "reply_id": "r1", "delta": "已派发。"},
        {"type": "REPLY_END", "reply_id": "r1", "finished_reason": "completed"},
        # 成员回报会触发一个新的 leader reply，而不是续用上一条 reply_id。
        {"type": "REPLY_START", "reply_id": "r2"},
        {"type": "TEXT_BLOCK_DELTA", "reply_id": "r2", "delta": "综合结论：X。"},
        {"type": "REPLY_END", "reply_id": "r2", "finished_reason": "completed"},
    ]
    client = _StubClient(events)
    result = await gateway.stream_round_to_run_events(
        client,
        uid="u",
        agent_id="a",
        session_id="s",
        text="任务",
        run_id="run-1",
        request_id="req-1",
        thread_id="th-1",
        read_timeout=5.0,
    )
    assert result.run_status == "completed"
    assert "已派发。" in result.text and "综合结论：X。" in result.text
    # end 帧只在静默收束时写一次
    end_frames = [p for name, p in capture_events if name == "end"]
    assert len(end_frames) == 1 and end_frames[0]["status"] == "completed"


async def test_team_continuation_without_new_team_tool_finishes_immediately(
    capture_events,
    monkeypatch,
):
    """成员回报后的最终续写没有新 Team 工具时，不再等待静默窗口。"""
    monkeypatch.setattr(gateway, "TEAM_QUIESCE_SECONDS", 5.0)
    queue = asyncio.Queue()
    for event in [
        {"type": "REPLY_START", "reply_id": "r1"},
        {
            "type": "TOOL_CALL_START",
            "reply_id": "r1",
            "tool_call_id": "t1",
            "tool_call_name": "TeamSay",
        },
        {"type": "REPLY_END", "reply_id": "r1", "finished_reason": "completed"},
        {"type": "REPLY_START", "reply_id": "r2"},
        {"type": "TEXT_BLOCK_DELTA", "reply_id": "r2", "delta": "最终结论"},
        {"type": "REPLY_END", "reply_id": "r2", "finished_reason": "completed"},
    ]:
        queue.put_nowait(event)

    result = await asyncio.wait_for(
        gateway.collect_run_events(
            queue,
            _StubClient([]),
            uid="u",
            agent_id="a",
            session_id="s",
            run_id="run-final",
            request_id="req-final",
            thread_id="th-final",
            read_timeout=5.0,
        ),
        timeout=0.2,
    )

    assert result.run_status == "completed"
    assert result.text == "最终结论"


async def test_team_round_waits_while_child_run_is_active(capture_events):
    """静默窗到期但 child Run 仍活跃时，父 Run 不得提前结束。"""
    queue = asyncio.Queue()
    for event in [
        {"type": "REPLY_START", "reply_id": "r1"},
        {
            "type": "TOOL_CALL_START",
            "reply_id": "r1",
            "tool_call_id": "t1",
            "tool_call_name": "AgentCreate",
        },
        {"type": "TEXT_BLOCK_DELTA", "reply_id": "r1", "delta": "已派发。"},
        {"type": "REPLY_END", "reply_id": "r1", "finished_reason": "completed"},
    ]:
        queue.put_nowait(event)

    checks = 0

    async def _has_active_child_runs() -> bool:
        nonlocal checks
        checks += 1
        return checks == 1

    async def _publish_leader_result() -> None:
        while checks < 1:
            await asyncio.sleep(0.01)
        for event in [
            {"type": "REPLY_START", "reply_id": "r2"},
            {"type": "TEXT_BLOCK_DELTA", "reply_id": "r2", "delta": "子任务结果已返回。"},
            {"type": "REPLY_END", "reply_id": "r2", "finished_reason": "completed"},
        ]:
            queue.put_nowait(event)

    publisher = asyncio.create_task(_publish_leader_result())
    try:
        result = await gateway.collect_run_events(
            queue,
            _StubClient([]),
            uid="u",
            agent_id="a",
            session_id="s",
            run_id="run-active-child",
            request_id="req-active-child",
            thread_id="th-active-child",
            read_timeout=5.0,
            has_active_child_runs=_has_active_child_runs,
        )
    finally:
        publisher.cancel()

    assert checks >= 2
    assert result.run_status == "completed"
    assert result.text == "已派发。子任务结果已返回。"


async def test_team_round_keeps_waiting_when_active_child_exceeds_extension_budget(
    capture_events,
    monkeypatch,
):
    """child Run 仍活跃时不得因父 Run 的单段等待预算而丢失回报。"""
    monkeypatch.setattr(gateway, "TEAM_CHILD_WAIT_SEGMENT_SECONDS", 0.05)
    queue = asyncio.Queue()
    for event in [
        {"type": "REPLY_START", "reply_id": "r1"},
        {
            "type": "TOOL_CALL_START",
            "reply_id": "r1",
            "tool_call_id": "t1",
            "tool_call_name": "AgentCreate",
        },
        {"type": "REPLY_END", "reply_id": "r1", "finished_reason": "completed"},
    ]:
        queue.put_nowait(event)

    child_active = True

    async def _has_active_child_runs() -> bool:
        return child_active

    async def _publish_late_child_result() -> None:
        nonlocal child_active
        await asyncio.sleep(0.08)
        child_active = False
        for event in [
            {"type": "REPLY_START", "reply_id": "r2"},
            {"type": "TEXT_BLOCK_DELTA", "reply_id": "r2", "delta": "迟到的子任务结果。"},
            {"type": "REPLY_END", "reply_id": "r2", "finished_reason": "completed"},
        ]:
            queue.put_nowait(event)

    publisher = asyncio.create_task(_publish_late_child_result())
    try:
        result = await gateway.collect_run_events(
            queue,
            _StubClient([]),
            uid="u",
            agent_id="a",
            session_id="s",
            run_id="run-timeout",
            request_id="req-timeout",
            thread_id="th-timeout",
            read_timeout=5.0,
            has_active_child_runs=_has_active_child_runs,
        )
    finally:
        publisher.cancel()

    assert result.run_status == "completed"
    assert result.text == "迟到的子任务结果。"


async def test_team_round_ignores_unscoped_heartbeat_during_quiesce(capture_events):
    """team 终态后的无归属心跳不得清除静默收束状态。"""
    client = _StubClient(
        [
            {"type": "REPLY_START", "reply_id": "r1"},
            {
                "type": "TOOL_CALL_START",
                "reply_id": "r1",
                "tool_call_id": "t1",
                "tool_call_name": "TeamCreate",
            },
            {"type": "TEXT_BLOCK_DELTA", "reply_id": "r1", "delta": "已派发。"},
            {"type": "REPLY_END", "reply_id": "r1", "finished_reason": "completed"},
            {"type": "HEARTBEAT"},
        ]
    )

    result = await gateway.stream_round_to_run_events(
        client,
        uid="u",
        agent_id="a",
        session_id="s",
        text="任务",
        run_id="run-heartbeat",
        request_id="req-heartbeat",
        thread_id="th-heartbeat",
        read_timeout=0.05,
    )

    assert result.run_status == "completed"
    assert result.text == "已派发。"
    assert len([payload for name, payload in capture_events if name == "end"]) == 1


async def test_plain_round_unchanged_first_reply_end(capture_events):
    """普通轮次（无 team 工具）首个 REPLY_END 即收束，行为不变。"""
    events = [
        {"type": "REPLY_START", "reply_id": "r1"},
        {"type": "TEXT_BLOCK_DELTA", "reply_id": "r1", "delta": "普通回复"},
        {"type": "REPLY_END", "reply_id": "r1", "finished_reason": "completed"},
        # 不应被消费的事件
        {"type": "TEXT_BLOCK_DELTA", "reply_id": "r1", "delta": "不应出现"},
    ]
    client = _StubClient(events)
    result = await gateway.stream_round_to_run_events(
        client,
        uid="u",
        agent_id="a",
        session_id="s",
        text="问题",
        run_id="run-2",
        request_id="req-2",
        thread_id="th-2",
        read_timeout=5.0,
    )
    assert result.run_status == "completed"
    assert result.text == "普通回复"
    assert "不应出现" not in result.text
    assert len([p for name, p in capture_events if name == "end"]) == 1


async def test_replyless_state_update_emits_agent_state(capture_events):
    """AgentScope 独立 state_updated 事件必须驱动实时 Todo/文件面板。"""
    client = _StubClient(
        [
            {"type": "REPLY_START", "reply_id": "r1"},
            {
                "type": "CUSTOM",
                "name": "state_updated",
                "value": {
                    "tasks_context": {"tasks": [{"id": "task-1", "subject": "整理资料", "state": "in_progress"}]}
                },
            },
            {"type": "REPLY_END", "reply_id": "r1", "finished_reason": "completed"},
        ]
    )

    await gateway.stream_round_to_run_events(
        client,
        uid="u",
        agent_id="a",
        session_id="s",
        text="任务",
        run_id="run-state",
        request_id="req-state",
        thread_id="th-state",
        read_timeout=5.0,
    )

    state_chunks = [
        item
        for event, payload in capture_events
        if event == "messages"
        for item in payload.get("items", [])
        if item.get("status") == "agent_state"
    ]
    assert state_chunks[0]["agent_state"]["todos"] == [{"id": "task-1", "content": "整理资料", "status": "in_progress"}]
    assert state_chunks[0]["agent_state"]["files"] == {}
    assert state_chunks[0]["agent_state"]["files_truncated"] is False


async def test_round_result_collects_tool_call_for_history(capture_events):
    """运行结果应保留工具参数、输出和失败状态，供线程历史持久化。"""
    client = _StubClient(
        [
            {"type": "REPLY_START", "reply_id": "r1"},
            {
                "type": "TOOL_CALL_START",
                "reply_id": "r1",
                "tool_call_id": "tc1",
                "tool_call_name": "query_kb",
            },
            {
                "type": "TOOL_CALL_DELTA",
                "reply_id": "r1",
                "tool_call_id": "tc1",
                "delta": '{"query":"退款',
            },
            {
                "type": "TOOL_CALL_DELTA",
                "reply_id": "r1",
                "tool_call_id": "tc1",
                "delta": '政策"}',
            },
            {"type": "TOOL_CALL_END", "reply_id": "r1", "tool_call_id": "tc1"},
            {
                "type": "TOOL_RESULT_TEXT_DELTA",
                "reply_id": "r1",
                "tool_call_id": "tc1",
                "delta": "知识库不可用",
            },
            {
                "type": "TOOL_RESULT_END",
                "reply_id": "r1",
                "tool_call_id": "tc1",
                "state": "error",
                "metadata": {"message": "连接失败"},
            },
            {"type": "TEXT_BLOCK_DELTA", "reply_id": "r1", "delta": "查询失败"},
            {"type": "REPLY_END", "reply_id": "r1", "finished_reason": "completed"},
        ]
    )

    result = await gateway.stream_round_to_run_events(
        client,
        uid="u",
        agent_id="a",
        session_id="s",
        text="查询退款政策",
        run_id="run-tools",
        request_id="req-tools",
        thread_id="th-tools",
        read_timeout=5.0,
    )

    assert result.tool_calls == [
        {
            "id": "tc1",
            "name": "query_kb",
            "args": {"query": "退款政策"},
            "output": "知识库不可用",
            "status": "error",
            "error_message": "连接失败",
            "metadata": {"message": "连接失败"},
        }
    ]


async def test_failed_round_preserves_provider_error(capture_events):
    """AgentScope 的可展示错误应进入运行结果，供 AgentRun 持久化。"""
    client = _StubClient(
        [
            {"type": "REPLY_START", "reply_id": "r-error"},
            {
                "type": "REPLY_END",
                "reply_id": "r-error",
                "finished_reason": "error",
                "error": {"type": "connection", "message": "模型服务连接超时"},
            },
        ]
    )

    result = await gateway.stream_round_to_run_events(
        client,
        uid="u",
        agent_id="a",
        session_id="s",
        text="问题",
        run_id="run-error",
        request_id="req-error",
        thread_id="th-error",
        read_timeout=5.0,
    )

    assert result.run_status == "failed"
    assert result.error_message == "模型服务连接超时"


async def test_max_iters_preserves_final_text_while_run_remains_failed(capture_events):
    """达到最大迭代时展示最终文本，但终态仍保留 exceed_max_iters。"""
    client = _StubClient(
        [
            {"type": "REPLY_START", "reply_id": "r-max"},
            {"type": "TEXT_BLOCK_DELTA", "reply_id": "r-max", "delta": "已完成的部分结果"},
            {"type": "REPLY_END", "reply_id": "r-max", "finished_reason": "exceed_max_iters"},
        ]
    )

    result = await gateway.stream_round_to_run_events(
        client,
        uid="u",
        agent_id="a",
        session_id="s",
        text="问题",
        run_id="run-max",
        request_id="req-max",
        thread_id="th-max",
        read_timeout=5.0,
    )

    assert result.text == "已完成的部分结果"
    assert result.run_status == "failed"
    assert result.error_type == "exceed_max_iters"


async def test_new_round_ignores_previous_reply_replay(capture_events):
    """新一轮订阅不得把 replay 中上一轮终态当成本轮结果。"""
    client = _StubClient(
        [
            {"type": "REPLY_START", "reply_id": "old-reply"},
            {
                "type": "REPLY_END",
                "reply_id": "old-reply",
                "finished_reason": "error",
                "error": {"type": "connection", "message": "old error"},
            },
            {"type": "REPLY_START", "reply_id": "new-reply"},
            {"type": "TEXT_BLOCK_DELTA", "reply_id": "new-reply", "delta": "新回复"},
            {"type": "REPLY_END", "reply_id": "new-reply", "finished_reason": "completed"},
        ],
        messages=[{"id": "old-reply", "role": "assistant"}],
    )

    result = await gateway.stream_round_to_run_events(
        client,
        uid="u",
        agent_id="a",
        session_id="s",
        text="重试问题",
        run_id="run-replay",
        request_id="req-replay",
        thread_id="th-replay",
        read_timeout=5.0,
    )

    assert result.run_status == "completed"
    assert result.text == "新回复"


async def test_collect_asking_tool_calls_from_session(monkeypatch):
    """并行多工具审批：以会话消息中的 asking 完整集合替代事件载荷。"""
    from yuxi.agentscope import worker_job

    class _MsgClient:
        async def list_messages(self, uid, agent_id, session_id):
            return [
                {
                    "id": "r1",
                    "content": [
                        {"type": "text", "text": "hi"},
                        {"type": "tool_call", "id": "t1", "name": "Write", "input": "{}", "state": "finished"},
                        {"type": "tool_call", "id": "t2", "name": "Write", "input": "{}", "state": "asking"},
                    ],
                },
                {"id": "older", "content": []},
            ]

    asking = await worker_job._collect_asking_tool_calls(
        _MsgClient(), uid="u", agent_id="a", session_id="s", reply_id="r1"
    )
    assert asking == [{"id": "t2", "name": "Write", "input": "{}"}]

    class _FailClient:
        async def list_messages(self, uid, agent_id, session_id):
            raise RuntimeError("down")

    with pytest.raises(RuntimeError, match="down"):
        await worker_job._collect_asking_tool_calls(_FailClient(), uid="u", agent_id="a", session_id="s", reply_id="r1")


async def test_external_execution_parks_as_question(capture_events):
    """外部执行事件应立即挂起，并写出前端问答 chunk。"""
    client = _StubClient(
        [
            {"type": "REPLY_START", "reply_id": "r1"},
            {
                "type": "REQUIRE_EXTERNAL_EXECUTION",
                "reply_id": "r1",
                "tool_calls": [
                    {
                        "id": "t1",
                        "name": "ask_user_question",
                        "input": '{"questions":[{"question":"继续吗？"}]}',
                    }
                ],
            },
        ]
    )
    result = await gateway.stream_round_to_run_events(
        client,
        uid="u",
        agent_id="a",
        session_id="s",
        text="问题",
        run_id="run-external",
        request_id="req-external",
        thread_id="th-external",
        read_timeout=5.0,
    )
    assert result.parked == "external"
    assert result.pending_confirm["reply_id"] == "r1"
    message_payload = next(payload for name, payload in capture_events if name == "messages")
    assert message_payload["items"][0]["status"] == "init"
    question_payload = [
        payload
        for name, payload in capture_events
        if name == "messages" and payload["items"][0]["status"] == "ask_user_question_required"
    ]
    assert len(question_payload) == 1


async def test_client_preserves_mixed_confirmation_decisions(monkeypatch):
    """客户端按位置发送混合审批决定，并拒绝数量不匹配。"""
    from yuxi.agentscope.client import AgentScopeServiceClient

    client = AgentScopeServiceClient("http://agentscope")
    captured = {}

    async def _request(method, path, uid, **kwargs):
        captured.update(kwargs["json"])

    monkeypatch.setattr(client, "_request", _request)
    calls = [
        {"id": "t1", "name": "Write", "input": "{}"},
        {"id": "t2", "name": "Execute", "input": "{}"},
    ]
    await client.resume_confirm("u", "a", "s", reply_id="r", tool_calls=calls, confirmed=[True, False])
    results = captured["input"]["confirm_results"]
    assert [item["confirmed"] for item in results] == [True, False]
    with pytest.raises(ValueError, match="数量不一致"):
        await client.resume_confirm("u", "a", "s", reply_id="r", tool_calls=calls, confirmed=[True])

async def test_cancel_watcher_unblocks_event_collection_without_reply_end(monkeypatch):
    """AgentScope 中断不回 REPLY_END 时，取消监听仍须立即唤醒收集器。"""
    from unittest.mock import AsyncMock

    from yuxi.services import run_queue_service

    monkeypatch.setattr(run_queue_service, "has_cancel_signal", AsyncMock(return_value=True))
    client = _StubClient([])
    queue = asyncio.Queue()
    watcher = gateway.start_cancel_watcher(
        client,
        uid="u",
        agent_id="a",
        session_id="s",
        run_id="run-cancel",
        poll_seconds=0.001,
        event_queue=queue,
    )
    try:
        event = await asyncio.wait_for(queue.get(), timeout=0.1)
    finally:
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)

    assert isinstance(event, RuntimeError)
    assert str(event) == "运行任务已取消"
