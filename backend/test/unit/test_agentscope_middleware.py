"""AgentScope 生命周期 Middleware 边界测试。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agentscope.agent import Agent
from agentscope.event import ToolResultEndEvent, ToolResultStartEvent
from agentscope.message import TextBlock, ToolCallBlock, ToolResultState
from agentscope.model import ChatResponse, ChatUsage
from agentscope.permission import PermissionMode
from agentscope.tool import FunctionTool, Toolkit, ToolChunk, ToolResponse

from yuxi.agentscope.middleware import (
    ContextObservabilityMiddleware,
    RuntimeSystemPromptMiddleware,
    SteerMiddleware,
    TeamLifecycleMiddleware,
    build_team_lifecycle_middleware,
)


async def _collect(middleware, items):
    async def next_handler(**_kwargs):
        for item in items:
            yield item

    result = []
    async for item in middleware.on_reasoning(None, {}, next_handler):
        result.append(item)
    return result


async def test_runtime_system_prompt_replaces_stale_agent_prompt_and_keeps_runtime_instructions():
    """长期 Team worker 必须使用当前投影，同时保留 AgentScope 动态指令。"""
    middleware = RuntimeSystemPromptMiddleware("当前平台提示\n中间文件：/workspace/outputs/tmp")
    stale_prompt = (
        "旧子智能体提示\n\n"
        "<system-notification>session attachment</system-notification>\n"
        "activated skill instructions\nworkspace instructions"
    )

    prompt = await middleware.on_system_prompt(None, stale_prompt)

    assert prompt.startswith("当前平台提示\n中间文件：/workspace/outputs/tmp")
    assert "旧子智能体提示" not in prompt
    assert "<system-notification>session attachment</system-notification>" in prompt
    assert "activated skill instructions" in prompt
    assert "workspace instructions" in prompt


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


@pytest.mark.parametrize("stream", [False, True])
async def test_context_observability_publishes_complete_model_usage(stream, monkeypatch):
    """模型观测保留普通输入与 Anthropic 缓存输入的原始分类。"""
    events = []
    monkeypatch.setattr("yuxi.agentscope.middleware.cache_input_mode_for_model", lambda _model: "additive")

    class Bus:
        async def session_publish_event(self, _session_id, event):
            events.append(event)

    response = ChatResponse(
        content=[],
        is_last=True,
        usage=ChatUsage(
            input_tokens=39,
            output_tokens=5,
            time=0.1,
            cache_creation_input_tokens=3,
            cache_input_tokens=128,
        ),
    )

    async def response_stream():
        yield ChatResponse(content=[], is_last=False)
        yield response

    async def next_handler(**_kwargs):
        return response_stream() if stream else response

    model = SimpleNamespace(context_size=1000, count_tokens=AsyncMock(return_value=170))
    agent = SimpleNamespace(
        state=SimpleNamespace(context=[], summary="", reply_id="reply-1"),
        context_config=SimpleNamespace(trigger_ratio=0.8),
    )
    result = await ContextObservabilityMiddleware(Bus(), "session-1").on_model_call(
        agent,
        {"current_model": model, "messages": [], "tools": []},
        next_handler,
    )
    if stream:
        assert [item async for item in result][-1] is response
    else:
        assert result is response

    usage_event = events[-1]
    assert usage_event["name"] == "model_usage"
    assert usage_event["value"] == {
        "reply_id": "reply-1",
        "cache_input_mode": "additive",
        "raw_input_tokens": 39,
        "input_tokens": 170,
        "output_tokens": 5,
        "cache_creation_input_tokens": 3,
        "cache_read_input_tokens": 128,
        "complete": True,
    }


async def test_context_observability_publishes_usage_when_stream_has_no_final_marker(monkeypatch):
    """Anthropic 流结束时发布最后一个 usage，即使响应块未标记 is_last。"""
    events = []
    monkeypatch.setattr("yuxi.agentscope.middleware.cache_input_mode_for_model", lambda _model: "additive")

    class Bus:
        async def session_publish_event(self, _session_id, event):
            events.append(event)

    usage = ChatUsage(
        input_tokens=39,
        output_tokens=5,
        time=0.1,
        cache_creation_input_tokens=3,
        cache_input_tokens=128,
    )

    async def response_stream():
        yield ChatResponse(content=[], is_last=False, usage=usage)

    async def next_handler(**_kwargs):
        return response_stream()

    model = SimpleNamespace(context_size=1000, count_tokens=AsyncMock(return_value=170))
    agent = SimpleNamespace(
        state=SimpleNamespace(context=[], summary="", reply_id="reply-1"),
        context_config=SimpleNamespace(trigger_ratio=0.8),
    )
    result = await ContextObservabilityMiddleware(Bus(), "session-1").on_model_call(
        agent,
        {"current_model": model, "messages": [], "tools": []},
        next_handler,
    )

    assert [item async for item in result]
    usage_events = [event for event in events if event["name"] == "model_usage"]
    assert len(usage_events) == 1
    assert usage_events[0]["value"] == {
        "reply_id": "reply-1",
        "cache_input_mode": "additive",
        "raw_input_tokens": 39,
        "input_tokens": 170,
        "output_tokens": 5,
        "cache_creation_input_tokens": 3,
        "cache_read_input_tokens": 128,
        "complete": True,
    }


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
    assert isinstance(result[0], ToolResponse)
    assert "retained" in result[0].content[0].text
    assert result[0].state == ToolResultState.SUCCESS


async def test_team_delete_emits_one_terminal_tool_result_event():
    """TeamDelete 必须完成 AgentScope 工具生命周期，且不执行原删除工具。"""

    async def TeamDelete() -> ToolResponse:  # noqa: N802 - 匹配 AgentScope 内置工具名
        """删除当前 Team。"""
        raise AssertionError("保留模式不应执行 AgentScope TeamDelete")

    agent = Agent(
        name="team-leader",
        system_prompt="test",
        model=SimpleNamespace(count_tokens=AsyncMock(return_value=1)),
        toolkit=Toolkit(tools=[FunctionTool(TeamDelete)]),
        middlewares=[TeamLifecycleMiddleware(SimpleNamespace(), is_worker=False)],
    )
    agent.state.permission_context.mode = PermissionMode.BYPASS
    tool_call = ToolCallBlock(id="delete-1", name="TeamDelete", input="{}")

    events = [event async for event in agent._execute_tool_call(tool_call)]  # noqa: SLF001

    assert len([event for event in events if isinstance(event, ToolResultStartEvent)]) == 1
    assert len([event for event in events if isinstance(event, ToolResultEndEvent)]) == 1


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


@pytest.mark.parametrize(
    ("is_worker", "expected"),
    [
        (False, ["旧任务", "系统状态", "最新任务"]),
        (True, ["系统状态", "最新任务"]),
    ],
)
async def test_only_worker_reasoning_removes_stale_team_hints(is_worker, expected):
    """只有 worker 推理会丢弃旧 Team 指令，leader 上下文必须保持不变。"""
    content = [
        SimpleNamespace(source='{"label":"team"}', hint="旧任务"),
        SimpleNamespace(source='{"label":"system"}', hint="系统状态"),
        SimpleNamespace(source='{"label":"team"}', hint="最新任务"),
    ]
    agent = SimpleNamespace(state=SimpleNamespace(context=[SimpleNamespace(content=content)]))

    async def next_handler(**_kwargs):
        yield "reasoning"

    lifecycle = SimpleNamespace(resolve_leader_name=AsyncMock(return_value=None))
    middleware = TeamLifecycleMiddleware(lifecycle, is_worker=is_worker)

    assert [item async for item in middleware.on_reasoning(agent, {}, next_handler)] == ["reasoning"]
    assert [block.hint for block in content] == expected


async def test_worker_lifecycle_does_not_install_duplicate_leader_notifier():
    """worker 成败通知完全交给 AgentScope 原生 Team 链路。"""
    worker_session = SimpleNamespace(team_id="team-1")
    team = SimpleNamespace(session_id="leader-session")
    class _Storage:
        async def get_session(self, user_id, agent_id, session_id):
            if session_id == "worker-session":
                return worker_session
            return None

        async def get_team(self, user_id, team_id):
            return team

    middleware = await build_team_lifecycle_middleware(
        _Storage(),
        SimpleNamespace(),
        SimpleNamespace(),
        "u",
        "worker-agent",
        "worker-session",
    )

    assert middleware._is_worker is True
    assert not hasattr(middleware._lifecycle, "leader_notifier")


async def test_native_schedule_tools_filtered_from_model_call():
    """任务中心工单 01：原生 Schedule 工具不得进入模型请求的工具面。"""
    from yuxi.agentscope.middleware import NativeScheduleBlockMiddleware

    mw = NativeScheduleBlockMiddleware()
    seen: dict = {}

    async def next_handler(**kwargs):
        seen.update(kwargs)
        return "model-response"

    result = await mw.on_model_call(
        None,
        {
            "messages": [],
            "tools": [
                {"type": "function", "function": {"name": "Bash"}},
                {"type": "function", "function": {"name": "ScheduleCreate"}},
                {"type": "function", "function": {"name": "ScheduleList"}},
                {"type": "function", "function": {"name": "ScheduleView"}},
                {"type": "function", "function": {"name": "ScheduleDelete"}},
                {"type": "function", "function": {"name": "TeamSay"}},
            ],
            "tool_choice": None,
        },
        next_handler,
    )
    names = [t["function"]["name"] for t in seen["tools"]]
    assert result == "model-response"
    assert names == ["Bash", "TeamSay"]
    assert not any(n.startswith("Schedule") for n in names)


async def test_native_schedule_block_keeps_calls_without_tools():
    """无 tools 或空 tools 的模型调用原样透传。"""
    from yuxi.agentscope.middleware import NativeScheduleBlockMiddleware

    mw = NativeScheduleBlockMiddleware()
    seen: dict = {}

    async def next_handler(**kwargs):
        seen.update(kwargs)
        return "ok"

    r1 = await mw.on_model_call(None, {"messages": [], "tools": None, "tool_choice": None}, next_handler)
    r2 = await mw.on_model_call(None, {"messages": [], "tools": [], "tool_choice": None}, next_handler)
    assert r1 == r2 == "ok"
    assert seen["tools"] in (None, [])
