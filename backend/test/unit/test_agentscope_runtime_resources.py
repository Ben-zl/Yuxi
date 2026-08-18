"""AgentScope 会话到统一运行时投影的解析测试。"""

from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock

import pytest

from yuxi.agentscope import runtime_resources

pytestmark = pytest.mark.unit


async def test_direct_session_uses_exact_mapping(monkeypatch):
    """主线程必须通过 uid、agent、session 的精确映射消费统一投影。"""
    mapping = SimpleNamespace(agent_slug="leader", model_spec="p:m", thread_id="thread-1")
    lookup = AsyncMock(return_value=mapping)
    projection = SimpleNamespace(agent_slug="leader")
    project = AsyncMock(return_value=projection)
    monkeypatch.setattr(runtime_resources, "get_thread_session_by_agentscope_context", lookup)
    monkeypatch.setattr(runtime_resources, "project_runtime", project)

    result = await runtime_resources.resolve_runtime_projection(
        SimpleNamespace(),
        SimpleNamespace(),
        user_id="u",
        agent_id="a",
        session_id="s",
    )

    assert result is projection
    lookup.assert_awaited_once_with(
        ANY,
        uid="u",
        agentscope_agent_id="a",
        agentscope_session_id="s",
    )
    project.assert_awaited_once_with(
        ANY,
        uid="u",
        agent_slug="leader",
        model_spec="p:m",
        thread_id="thread-1",
    )


async def test_team_worker_resolves_leader_mapping(monkeypatch):
    """AgentScope 动态 worker 没有直接映射时继承 Team leader 的线程投影。"""
    mapping = SimpleNamespace(agent_slug="leader", model_spec="p:m", thread_id="thread-1")
    lookup = AsyncMock(side_effect=[None, mapping])
    project = AsyncMock(return_value=SimpleNamespace(agent_slug="leader"))
    storage = SimpleNamespace(
        get_session=AsyncMock(
            side_effect=[
                SimpleNamespace(team_id="team-1"),
                SimpleNamespace(id="leader-session", agent_id="leader-agent"),
            ]
        ),
        get_team=AsyncMock(return_value=SimpleNamespace(session_id="leader-session")),
    )
    monkeypatch.setattr(runtime_resources, "get_thread_session_by_agentscope_context", lookup)
    monkeypatch.setattr(runtime_resources, "project_runtime", project)

    await runtime_resources.resolve_runtime_projection(
        SimpleNamespace(),
        storage,
        user_id="u",
        agent_id="worker-agent",
        session_id="worker-session",
    )

    assert lookup.await_count == 2
    project.assert_awaited_once_with(
        ANY,
        uid="u",
        agent_slug="leader",
        model_spec="p:m",
        thread_id="thread-1",
    )


async def test_unmapped_non_team_session_fails_explicitly(monkeypatch):
    """伪造或孤立 session 不得静默获得空工具集。"""
    monkeypatch.setattr(
        runtime_resources,
        "get_thread_session_by_agentscope_context",
        AsyncMock(return_value=None),
    )
    storage = SimpleNamespace(get_session=AsyncMock(return_value=None))

    with pytest.raises(ValueError, match="不存在有效"):
        await runtime_resources.resolve_runtime_projection(
            SimpleNamespace(),
            storage,
            user_id="u",
            agent_id="forged",
            session_id="forged",
        )
