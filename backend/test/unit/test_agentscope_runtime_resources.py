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


async def test_team_worker_uses_subagent_resources_with_parent_run_context(monkeypatch):
    """动态 worker 使用子智能体资源，同时沿用父线程与本轮模型。"""
    mapping = SimpleNamespace(agent_slug="leader", model_spec="p:m", thread_id="thread-1")
    lookup = AsyncMock(side_effect=[None, mapping])
    project = AsyncMock(return_value=SimpleNamespace(agent_slug="automation-analysis-agent"))
    binding_lookup = AsyncMock(
        side_effect=[
            None,
            SimpleNamespace(
            subagent_slug="automation-analysis-agent",
            parent_thread_id="thread-1",
            ),
        ]
    )
    sleep = AsyncMock()
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
    monkeypatch.setattr(
        runtime_resources,
        "asyncio",
        SimpleNamespace(sleep=sleep),
        raising=False,
    )
    monkeypatch.setattr(
        runtime_resources,
        "AgentScopeTeamWorkerRepository",
        lambda db: SimpleNamespace(get_by_worker_session=binding_lookup),
        raising=False,
    )

    await runtime_resources.resolve_runtime_projection(
        SimpleNamespace(),
        storage,
        user_id="u",
        agent_id="worker-agent",
        session_id="worker-session",
    )

    assert lookup.await_count == 2
    assert binding_lookup.await_count == 2
    binding_lookup.assert_awaited_with(uid="u", worker_session_id="worker-session")
    sleep.assert_awaited_once()
    project.assert_awaited_once_with(
        ANY,
        uid="u",
        agent_slug="automation-analysis-agent",
        model_spec="p:m",
        thread_id="thread-1",
    )


async def test_runtime_skills_reconcile_to_subagent_projection():
    """worker workspace 必须移除旧 Skill，并安装子智能体选择的 Skill。"""
    workspace = SimpleNamespace(
        list_skills=AsyncMock(return_value=[SimpleNamespace(name="leader-skill")]),
        remove_skill=AsyncMock(),
        add_skill=AsyncMock(),
    )
    projection = SimpleNamespace(
        skills=[
            {
                "slug": "auto-platform-query",
                "source_dir": "/skills/auto-platform-query",
            }
        ]
    )

    await runtime_resources.sync_runtime_skills(
        workspace,
        projection,
        agent_id="worker-agent",
    )

    workspace.remove_skill.assert_awaited_once_with(
        "leader-skill",
        agent_id="worker-agent",
    )
    workspace.add_skill.assert_awaited_once_with(
        "/skills/auto-platform-query",
        agent_id="worker-agent",
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
