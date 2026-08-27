"""AgentScope Team worker 生命周期投影测试。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from yuxi.agentscope import team_lifecycle
from yuxi.agentscope.team_lifecycle import TeamLifecycleModule, retain_latest_team_hint


async def test_failed_worker_reply_notifies_leader_before_terminal(monkeypatch):
    """worker 失败时必须先通知 leader，再结束 child Run。"""
    order = []

    async def _notify(message: str) -> None:
        order.append(("notify", message))

    lifecycle = TeamLifecycleModule(
        storage=SimpleNamespace(),
        uid="u",
        agent_id="worker-agent",
        session_id="worker-session",
        leader_notifier=_notify,
    )
    binding = SimpleNamespace(worker_session_id="worker-session", child_thread_id="child-thread")
    monkeypatch.setattr(lifecycle, "_wait_for_binding", AsyncMock(return_value=binding))
    monkeypatch.setattr(lifecycle, "_open_worker_run", AsyncMock(return_value=("child-run", "child-request")))

    async def _finish_worker_run(**kwargs) -> None:
        order.append(("finish", kwargs["terminal_status"]))

    monkeypatch.setattr(lifecycle, "_finish_worker_run", _finish_worker_run)
    monkeypatch.setattr(team_lifecycle, "append_run_stream_event", AsyncMock())

    async def _reply(**_kwargs):
        yield {"type": "REPLY_START", "reply_id": "reply-1"}
        yield {
            "type": "REPLY_END",
            "reply_id": "reply-1",
            "finished_reason": "exceed_max_iters",
        }

    assert [item async for item in lifecycle.project_worker_reply({}, _reply)]
    assert order == [
        ("notify", "子智能体执行失败，未能返回结果：运行失败: exceed_max_iters"),
        ("finish", "failed"),
    ]


async def test_completed_worker_reply_without_team_say_notifies_leader(monkeypatch):
    """worker 未主动 TeamSay 时，平台必须把完整结果代发给 leader。"""
    order = []

    async def _notify(message: str) -> None:
        order.append(("notify", message))

    lifecycle = TeamLifecycleModule(
        storage=SimpleNamespace(),
        uid="u",
        agent_id="worker-agent",
        session_id="worker-session",
        leader_notifier=_notify,
    )
    binding = SimpleNamespace(worker_session_id="worker-session", child_thread_id="child-thread")
    monkeypatch.setattr(lifecycle, "_wait_for_binding", AsyncMock(return_value=binding))
    monkeypatch.setattr(lifecycle, "_open_worker_run", AsyncMock(return_value=("child-run", "child-request")))

    async def _finish_worker_run(**kwargs) -> None:
        order.append(("finish", kwargs["terminal_status"]))

    monkeypatch.setattr(lifecycle, "_finish_worker_run", _finish_worker_run)
    monkeypatch.setattr(team_lifecycle, "append_run_stream_event", AsyncMock())

    async def _reply(**_kwargs):
        yield {"type": "REPLY_START", "reply_id": "reply-1"}
        yield {"type": "TEXT_BLOCK_DELTA", "reply_id": "reply-1", "delta": "真实分析结果"}
        yield {
            "type": "TOOL_CALL_START",
            "reply_id": "reply-1",
            "tool_call_id": "bash-1",
            "tool_call_name": "Bash",
        }
        yield {
            "type": "TOOL_RESULT_TEXT_DELTA",
            "reply_id": "reply-1",
            "tool_call_id": "bash-1",
            "delta": '{"perfeye_file":"/workspace/outputs/tmp/perfeye_169724.json"}',
        }
        yield {
            "type": "TOOL_RESULT_END",
            "reply_id": "reply-1",
            "tool_call_id": "bash-1",
            "state": "success",
        }
        yield {"type": "REPLY_END", "reply_id": "reply-1", "finished_reason": "completed"}

    assert [item async for item in lifecycle.project_worker_reply({}, _reply)]
    assert order == [
        (
            "notify",
            "子智能体已完成，但未主动调用 TeamSay。以下为其完整结果：\n\n"
            "真实分析结果\n\n可供后续子智能体复用的共享中间文件：\n"
            "- /workspace/outputs/tmp/perfeye_169724.json",
        ),
        ("finish", "completed"),
    ]


async def test_completed_worker_reply_with_successful_team_say_is_not_duplicated(monkeypatch):
    """worker 已成功 TeamSay 时，平台不得重复向 leader 投递结果。"""
    notify = AsyncMock()
    lifecycle = TeamLifecycleModule(
        storage=SimpleNamespace(),
        uid="u",
        agent_id="worker-agent",
        session_id="worker-session",
        leader_notifier=notify,
    )
    binding = SimpleNamespace(worker_session_id="worker-session", child_thread_id="child-thread")
    monkeypatch.setattr(lifecycle, "_wait_for_binding", AsyncMock(return_value=binding))
    monkeypatch.setattr(lifecycle, "_open_worker_run", AsyncMock(return_value=("child-run", "child-request")))
    monkeypatch.setattr(lifecycle, "_finish_worker_run", AsyncMock())
    monkeypatch.setattr(team_lifecycle, "append_run_stream_event", AsyncMock())

    async def _reply(**_kwargs):
        yield {"type": "REPLY_START", "reply_id": "reply-1"}
        yield {
            "type": "TOOL_CALL_START",
            "reply_id": "reply-1",
            "tool_call_id": "team-say-1",
            "tool_call_name": "TeamSay",
        }
        yield {
            "type": "TOOL_RESULT_END",
            "reply_id": "reply-1",
            "tool_call_id": "team-say-1",
            "state": "success",
        }
        yield {"type": "REPLY_END", "reply_id": "reply-1", "finished_reason": "completed"}

    assert [item async for item in lifecycle.project_worker_reply({}, _reply)]
    notify.assert_not_awaited()


async def test_worker_reply_stream_without_reply_end_still_finishes_child_run(monkeypatch):
    """取消导致事件流直接耗尽时，child Run 也必须进入终态。"""
    lifecycle = TeamLifecycleModule(
        storage=SimpleNamespace(),
        uid="u",
        agent_id="worker-agent",
        session_id="worker-session",
    )
    binding = SimpleNamespace(worker_session_id="worker-session", child_thread_id="child-thread")
    monkeypatch.setattr(lifecycle, "_wait_for_binding", AsyncMock(return_value=binding))
    monkeypatch.setattr(lifecycle, "_open_worker_run", AsyncMock(return_value=("child-run", "child-request")))
    finish_run = AsyncMock(return_value="cancelled")
    monkeypatch.setattr(lifecycle, "_finish_worker_run", finish_run)
    append_event = AsyncMock()
    monkeypatch.setattr(team_lifecycle, "append_run_stream_event", append_event)

    async def _reply(**_kwargs):
        yield {"type": "REPLY_START", "reply_id": "reply-1"}
        yield {"type": "TEXT_BLOCK_DELTA", "reply_id": "reply-1", "delta": "部分结果"}

    assert [item async for item in lifecycle.project_worker_reply({}, _reply)]
    finish_run.assert_awaited_once()
    finish_kwargs = finish_run.await_args.kwargs
    assert finish_kwargs["run_id"] == "child-run"
    assert finish_kwargs["terminal_status"] == "failed"
    assert finish_kwargs["error_message"] == "AgentScope worker reply stream ended without REPLY_END"
    assert finish_kwargs["text"] == "部分结果"
    assert finish_kwargs["usage"]["complete"] is False
    assert append_event.await_args_list[-1].args[:2] == ("child-run", "end")
    assert append_event.await_args_list[-1].args[2]["status"] == "cancelled"


async def test_worker_reply_projects_latest_team_message_as_child_input(monkeypatch):
    """wakeup 必须以最新 TeamSay 正文创建 child Run，而不是通用占位文本。"""
    lifecycle = TeamLifecycleModule(
        storage=SimpleNamespace(),
        uid="u",
        agent_id="worker-agent",
        session_id="worker-session",
    )
    binding = SimpleNamespace(worker_session_id="worker-session", child_thread_id="child-thread")
    monkeypatch.setattr(lifecycle, "_wait_for_binding", AsyncMock(return_value=binding))
    open_run = AsyncMock(return_value=("child-run", "child-request"))
    monkeypatch.setattr(lifecycle, "_open_worker_run", open_run)
    monkeypatch.setattr(lifecycle, "_finish_worker_run", AsyncMock())
    monkeypatch.setattr(team_lifecycle, "append_run_stream_event", AsyncMock())

    async def _reply(**_kwargs):
        yield {"type": "REPLY_START", "reply_id": "reply-2"}
        yield {
            "type": "HINT_BLOCK",
            "hint": '<team-message from="leader">\n旧任务\n</team-message>',
            "source": '{"label":"team","sublabel":"leader"}',
        }
        yield {
            "type": "HINT_BLOCK",
            "hint": '<team-message from="leader">\n最新任务正文\n</team-message>',
            "source": '{"label":"team","sublabel":"leader"}',
        }
        yield {"type": "MODEL_CALL_START", "reply_id": "reply-2"}
        yield {"type": "REPLY_END", "reply_id": "reply-2", "finished_reason": "completed"}

    assert [item async for item in lifecycle.project_worker_reply({}, _reply)]
    open_run.assert_awaited_once_with(
        "worker-session",
        "reply-2",
        {},
        prompt="最新任务正文",
    )


def test_retain_latest_team_hint_removes_stale_instructions():
    """模型推理前只保留最新 TeamSay，避免失败重试注入旧任务。"""
    old_hint = SimpleNamespace(source='{"label":"team"}', hint="旧任务")
    unrelated = SimpleNamespace(source='{"label":"system"}', hint="系统状态")
    latest_hint = SimpleNamespace(source='{"label":"team"}', hint="最新任务")
    message = SimpleNamespace(content=[old_hint, unrelated, latest_hint])
    agent = SimpleNamespace(state=SimpleNamespace(context=[message]))

    retain_latest_team_hint(agent)

    assert message.content == [unrelated, latest_hint]
