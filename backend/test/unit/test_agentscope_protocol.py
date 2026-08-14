"""agentscope 事件 → 前端 chunk 协议转换单测（迁移工单 04）。

事件夹具驱动，不依赖真实模型与外部服务；断言与前端
useAgentStreamHandler 消费契约逐类等价。
"""

import pytest

from yuxi.agentscope.protocol import (
    event_to_chunks,
    init_chunk,
    reply_end_to_terminal,
)

pytestmark = pytest.mark.unit

REQUEST_ID = "req-1"


def _event(event_type: str, **fields) -> dict:
    return {"type": event_type, "reply_id": "reply-1", **fields}


def test_reply_start_opens_assistant_message():
    chunks = event_to_chunks(_event("REPLY_START"), request_id=REQUEST_ID)
    assert chunks == [
        {
            "request_id": REQUEST_ID,
            "response": None,
            "thread_id": None,
            "status": "loading",
            "msg": {"id": "reply-1", "type": "AIMessageChunk", "content": ""},
        }
    ]


def test_text_delta_maps_to_content():
    chunks = event_to_chunks(
        _event("TEXT_BLOCK_DELTA", delta="你好"), request_id=REQUEST_ID
    )
    assert chunks[0]["status"] == "loading"
    assert chunks[0]["msg"]["content"] == "你好"
    assert "reasoning_content" not in chunks[0]["msg"]


def test_thinking_delta_maps_to_reasoning_content():
    chunks = event_to_chunks(
        _event("THINKING_BLOCK_DELTA", delta="想一想"), request_id=REQUEST_ID
    )
    assert chunks[0]["msg"]["content"] == ""
    assert chunks[0]["msg"]["reasoning_content"] == "想一想"


def test_unmapped_events_are_ignored():
    assert event_to_chunks(_event("HINT_BLOCK"), request_id=REQUEST_ID) == []
    assert event_to_chunks({"type": "MODEL_CALL_START"}, request_id=REQUEST_ID) == []
    assert event_to_chunks({}, request_id=REQUEST_ID) == []


def test_init_chunk_carries_user_message():
    chunk = init_chunk(REQUEST_ID, text="在吗")
    assert chunk["status"] == "init"
    assert chunk["msg"]["type"] == "human"
    assert chunk["msg"]["content"] == "在吗"


def test_reply_end_completed():
    terminal = reply_end_to_terminal(
        _event("REPLY_END", finished_reason="completed"), request_id=REQUEST_ID
    )
    assert terminal.run_status == "completed"
    assert terminal.chunk["status"] == "finished"


def test_reply_end_error_carries_message():
    terminal = reply_end_to_terminal(
        _event(
            "REPLY_END",
            finished_reason="error",
            error={"type": "provider_error", "message": "供应商超时"},
        ),
        request_id=REQUEST_ID,
    )
    assert terminal.run_status == "failed"
    assert terminal.chunk["status"] == "error"
    assert terminal.chunk["error_type"] == "error"
    assert terminal.chunk["error_message"] == "供应商超时"


def test_reply_end_interrupted_and_max_iters():
    interrupted = reply_end_to_terminal(
        _event("REPLY_END", finished_reason="interrupted"), request_id=REQUEST_ID
    )
    assert interrupted.run_status == "interrupted"
    assert interrupted.chunk["status"] == "interrupted"

    max_iters = reply_end_to_terminal(
        _event("REPLY_END", finished_reason="exceed_max_iters"), request_id=REQUEST_ID
    )
    assert max_iters.run_status == "failed"
    assert max_iters.chunk["error_type"] == "exceed_max_iters"


def test_tool_event_converter_streams_call_and_result():
    from yuxi.agentscope.protocol import ToolEventConverter

    converter = ToolEventConverter(REQUEST_ID)
    start = converter.feed(
        {"type": "TOOL_CALL_START", "tool_call_id": "tc1", "tool_call_name": "query_kb"}
    )
    assert start[0]["status"] == "loading"
    assert start[0]["msg"]["tool_call_chunks"][0]["name"] == "query_kb"

    delta = converter.feed({"type": "TOOL_CALL_DELTA", "tool_call_id": "tc1", "delta": '{"kb_'})
    assert delta[0]["msg"]["tool_call_chunks"][0]["args"] == '{"kb_'

    end = converter.feed({"type": "TOOL_CALL_END", "tool_call_id": "tc1"})
    assert end[0]["msg"]["tool_calls"][0]["args"] == '{"kb_'

    assert converter.feed({"type": "TOOL_RESULT_TEXT_DELTA", "tool_call_id": "tc1", "delta": "结果"}) == []
    finished = converter.feed({"type": "TOOL_RESULT_END", "tool_call_id": "tc1"})
    assert finished[0]["status"] == "stream_event"
    assert finished[0]["event"]["data"]["event"] == "tool-finished"
    assert finished[0]["event"]["data"]["output"]["content"] == "结果"

    assert converter.feed({"type": "MODEL_CALL_START", "reply_id": "r1"}) == []
