"""网关 team 静默收束与多工具审批补全的单元测试（迁移工单 14 缺口修复）。"""

import asyncio
import contextlib

import pytest

from yuxi.agentscope import gateway


class _StubClient:
    """按脚本吐事件的会话客户端桩：事件耗尽后挂起模拟静默。"""

    def __init__(self, events):
        self._events = events
        self.triggered = None

    async def trigger_chat(self, uid, agent_id, session_id, text):
        self.triggered = text

    async def interrupt_session(self, uid, agent_id, session_id):
        return None

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
    monkeypatch.setattr(gateway, "TEAM_EXTENSION_BUDGET_SECONDS", 3.0)
    return calls


async def test_team_round_collects_wakeup_continuation(capture_events):
    """team 轮次 REPLY_END 后等待续写：静默窗口内的回报文本被聚合。"""
    events = [
        {"type": "REPLY_START", "reply_id": "r1"},
        {"type": "TOOL_CALL_START", "reply_id": "r1", "tool_call_id": "t1", "tool_call_name": "TeamCreate"},
        {"type": "TEXT_BLOCK_DELTA", "reply_id": "r1", "delta": "已派发。"},
        {"type": "REPLY_END", "reply_id": "r1", "finished_reason": "completed"},
        # 静默窗口内的续写（成员回报驱动）
        {"type": "REPLY_START", "reply_id": "r1"},
        {"type": "TEXT_BLOCK_DELTA", "reply_id": "r1", "delta": "综合结论：X。"},
        {"type": "REPLY_END", "reply_id": "r1", "finished_reason": "completed"},
    ]
    client = _StubClient(events)
    result = await gateway.stream_round_to_run_events(
        client, uid="u", agent_id="a", session_id="s", text="任务",
        run_id="run-1", request_id="req-1", thread_id="th-1", read_timeout=5.0,
    )
    assert result.run_status == "completed"
    assert "已派发。" in result.text and "综合结论：X。" in result.text
    # end 帧只在静默收束时写一次
    end_frames = [p for name, p in capture_events if name == "end"]
    assert len(end_frames) == 1 and end_frames[0]["status"] == "completed"


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
        client, uid="u", agent_id="a", session_id="s", text="问题",
        run_id="run-2", request_id="req-2", thread_id="th-2", read_timeout=5.0,
    )
    assert result.run_status == "completed"
    assert result.text == "普通回复"
    assert "不应出现" not in result.text
    assert len([p for name, p in capture_events if name == "end"]) == 1


async def test_collect_asking_tool_calls_from_session(monkeypatch):
    """并行多工具审批：以会话消息中的 asking 完整集合替代事件载荷。"""
    from yuxi.agentscope import worker_job

    class _MsgClient:
        async def list_messages(self, uid, agent_id, session_id):
            return [
                {"id": "r1", "content": [
                    {"type": "text", "text": "hi"},
                    {"type": "tool_call", "id": "t1", "name": "Write", "input": "{}", "state": "finished"},
                    {"type": "tool_call", "id": "t2", "name": "Write", "input": "{}", "state": "asking"},
                ]},
                {"id": "older", "content": []},
            ]

    asking = await worker_job._collect_asking_tool_calls(
        _MsgClient(), uid="u", agent_id="a", session_id="s", reply_id="r1"
    )
    assert asking == [{"id": "t2", "name": "Write", "input": "{}"}]

    class _FailClient:
        async def list_messages(self, uid, agent_id, session_id):
            raise RuntimeError("down")

    empty = await worker_job._collect_asking_tool_calls(
        _FailClient(), uid="u", agent_id="a", session_id="s", reply_id="r1"
    )
    assert empty == []
