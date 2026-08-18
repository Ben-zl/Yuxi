"""AgentScope 生命周期 Middleware 边界测试。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agentscope.message import TextBlock
from agentscope.tool import ToolChunk

from yuxi.agentscope.middleware import (
    ContextObservabilityMiddleware,
    SteerMiddleware,
    TeamLifecycleMiddleware,
)


async def _collect(middleware, items):
    async def next_handler(**_kwargs):
        for item in items:
            yield item

    result = []
    async for item in middleware.on_reasoning(None, {}, next_handler):
        result.append(item)
    return result


async def test_steer_stops_before_model_when_already_pending():
    async def should_stop():
        return True

    with pytest.raises(asyncio.CancelledError):
        await _collect(SteerMiddleware(should_stop), [])


async def test_steer_stops_after_no_tool_model_response():
    checks = iter([False, True])

    async def should_stop():
        return next(checks)

    with pytest.raises(asyncio.CancelledError):
        await _collect(SteerMiddleware(should_stop), [SimpleNamespace(type="text")])


async def test_steer_waits_for_tool_batch_and_stops_at_next_model_boundary():
    pending = False

    async def should_stop():
        return pending

    middleware = SteerMiddleware(should_stop)
    tool_call = SimpleNamespace(type="tool_call")
    assert await _collect(middleware, [tool_call]) == [tool_call]

    pending = True
    with pytest.raises(asyncio.CancelledError):
        await _collect(middleware, [])


async def test_context_observability_publishes_token_context_before_model_call():
    events = []

    class Bus:
        async def session_publish_event(self, session_id, event):
            events.append((session_id, event))

    class Message:
        def __init__(self, role, content):
            self.role = role
            self.content = content

        def model_dump(self, mode):
            assert mode == "json"
            return {"role": self.role, "content": self.content}

    model = SimpleNamespace(context_size=1000, count_tokens=AsyncMock(return_value=120))
    agent = SimpleNamespace(
        state=SimpleNamespace(
            context=[Message("user", "历史问题")],
            summary="已有摘要",
        ),
        context_config=SimpleNamespace(trigger_ratio=0.8),
    )

    async def next_handler(**_kwargs):
        return "response"

    middleware = ContextObservabilityMiddleware(Bus(), "session-1")
    result = await middleware.on_model_call(
        agent,
        {
            "current_model": model,
            "messages": [Message("system", "系统提示"), Message("user", "问题")],
            "tools": [{"type": "function", "function": {"name": "Read"}}],
        },
        next_handler,
    )

    assert result == "response"
    assert events[0][0] == "session-1"
    assert events[0][1]["name"] == "token_context"
    snapshot = events[0][1]["value"]
    assert snapshot["llm_input_tokens"] == 120
    assert snapshot["context_window"] == 1000
    assert snapshot["summary_trigger_tokens"] == 800
    assert snapshot["summary_active"] is True
    assert snapshot["tool_count"] == 1


async def test_context_observability_publishes_compression_lifecycle():
    events = []

    class Bus:
        async def session_publish_event(self, _session_id, event):
            events.append(event)

    agent = SimpleNamespace(state=SimpleNamespace(summary=""))

    async def next_handler(**_kwargs):
        agent.state.summary = "压缩后的摘要"

    middleware = ContextObservabilityMiddleware(Bus(), "session-1")
    await middleware.on_compress_context(agent, {}, next_handler)

    assert [event["value"]["status"] for event in events] == ["started", "completed"]
    assert events[-1]["value"]["summary_active"] is True


async def test_context_observability_does_not_report_noop_compression_check():
    events = []

    class Bus:
        async def session_publish_event(self, _session_id, event):
            events.append(event)

    agent = SimpleNamespace(state=SimpleNamespace(summary="", context=[]))

    async def next_handler(**_kwargs):
        return None

    await ContextObservabilityMiddleware(Bus(), "session-1").on_compress_context(
        agent,
        {},
        next_handler,
    )

    assert events == []


async def _collect_acting(middleware, tool_call, items):
    async def next_handler(**_kwargs):
        for item in items:
            yield item

    return [item async for item in middleware.on_acting(None, {"tool_call": tool_call}, next_handler)]


async def test_team_delete_is_retained_without_calling_agentscope_delete():
    lifecycle = SimpleNamespace()
    middleware = TeamLifecycleMiddleware(lifecycle, is_worker=False)
    tool_call = SimpleNamespace(name="TeamDelete", input="{}", id="delete-1")

    result = await _collect_acting(middleware, tool_call, [])

    assert len(result) == 1
    assert "retained" in result[0].content[0].text


async def test_agent_create_projects_the_single_roster_delta_before_returning_result():
    before = SimpleNamespace(team_id="team", members={})
    after = SimpleNamespace(team_id="team", members={"worker-session": ("worker-agent", "created")})
    lifecycle = SimpleNamespace(
        snapshot=AsyncMock(side_effect=[before, after]),
        project_created_member=AsyncMock(),
    )
    middleware = TeamLifecycleMiddleware(lifecycle, is_worker=False)
    tool_call = SimpleNamespace(
        name="AgentCreate",
        id="call-1",
        input='{"prompt":"调查问题","subagent_type":"researcher"}',
    )
    response = ToolChunk(content=[TextBlock(text="created")])

    assert await _collect_acting(middleware, tool_call, [response]) == [response]
    lifecycle.project_created_member.assert_awaited_once_with(
        before=before,
        after=after,
        tool_call_id="call-1",
        tool_input={"prompt": "调查问题", "subagent_type": "researcher"},
    )


async def test_only_worker_reply_enters_team_lifecycle_projection():
    calls = []

    async def project_worker_reply(input_kwargs, next_handler):
        calls.append(input_kwargs)
        async for item in next_handler(**input_kwargs):
            yield item

    lifecycle = SimpleNamespace(project_worker_reply=project_worker_reply)

    async def next_handler(**_kwargs):
        yield "reply"

    leader = TeamLifecycleMiddleware(lifecycle, is_worker=False)
    worker = TeamLifecycleMiddleware(lifecycle, is_worker=True)
    assert [item async for item in leader.on_reply(None, {"inputs": "leader"}, next_handler)] == ["reply"]
    assert calls == []
    assert [item async for item in worker.on_reply(None, {"inputs": "worker"}, next_handler)] == ["reply"]
    assert calls == [{"inputs": "worker"}]
