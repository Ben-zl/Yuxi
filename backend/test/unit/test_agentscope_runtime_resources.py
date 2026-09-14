"""AgentScope 会话到统一运行时投影的解析测试。"""

from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock

import pytest

from yuxi.agentscope import runtime_resources
from yuxi.services.agent_run_manifest_service import compute_manifest_fingerprint

pytestmark = pytest.mark.unit


@pytest.fixture
def active_run(monkeypatch):
    """提供当前运行及其提交时快照，不读取会话中的旧模型配置。"""
    manifest = {
        "manifest_version": 1,
        "resource_snapshot": {"id": "current-run-snapshot", "fingerprint": "snapshot-digest"},
    }
    run = SimpleNamespace(
        id="current-run",
        status="running",
        manifest=manifest,
        manifest_fingerprint=compute_manifest_fingerprint(manifest),
    )
    lookup = AsyncMock(return_value=run)
    monkeypatch.setattr(
        runtime_resources,
        "AgentRunRepository",
        lambda db: SimpleNamespace(get_active_run_by_thread_for_user=lookup),
    )
    return run, lookup


async def test_direct_session_uses_exact_mapping(monkeypatch, active_run):
    """主线程必须通过 uid、agent、session 的精确映射消费统一投影。"""
    mapping = SimpleNamespace(agent_slug="leader", model_spec="p:m", thread_id="thread-1")
    lookup = AsyncMock(return_value=mapping)
    projection = SimpleNamespace(agent_slug="leader")
    project = AsyncMock(return_value=projection)
    monkeypatch.setattr(runtime_resources, "get_thread_session_by_agentscope_context", lookup)
    monkeypatch.setattr(runtime_resources, "load_run_resources", project)

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
        manifest=active_run[0].manifest,
    )
    assert project.await_args.kwargs["manifest"] is active_run[0].manifest
    active_run[1].assert_awaited_once_with(
        uid="u",
        agent_slug="leader",
        conversation_thread_id="thread-1",
    )


async def test_team_worker_uses_subagent_resources_with_parent_run_context(monkeypatch, active_run):
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
    monkeypatch.setattr(runtime_resources, "load_run_resources", project)
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
        manifest=active_run[0].manifest,
    )
    assert project.await_args.kwargs["manifest"] is active_run[0].manifest
    active_run[1].assert_awaited_once_with(
        uid="u",
        agent_slug="leader",
        conversation_thread_id="thread-1",
    )


@pytest.mark.parametrize("invalid", ["missing_run", "pending", "completed", "missing_manifest", "changed_fingerprint"])
async def test_runtime_rejects_missing_or_invalid_current_run(monkeypatch, active_run, invalid):
    """无正在执行的有效快照时必须拒绝，不能改用会话或最新配置。"""
    run, lookup = active_run
    if invalid == "missing_run":
        lookup.return_value = None
    elif invalid == "missing_manifest":
        run.manifest = None
    elif invalid == "changed_fingerprint":
        run.manifest = {**run.manifest, "resource_snapshot": {"id": "another-run", "fingerprint": "snapshot-digest"}}
    else:
        run.status = invalid
    monkeypatch.setattr(
        runtime_resources,
        "get_thread_session_by_agentscope_context",
        AsyncMock(return_value=SimpleNamespace(agent_slug="leader", thread_id="thread-1", model_spec="stale:model")),
    )
    load_resources = AsyncMock()
    monkeypatch.setattr(runtime_resources, "load_run_resources", load_resources)

    error = "指纹不一致" if invalid == "changed_fingerprint" else "缺少正在执行"
    with pytest.raises(ValueError, match=error):
        await runtime_resources.resolve_runtime_projection(
            SimpleNamespace(),
            SimpleNamespace(),
            user_id="u",
            agent_id="a",
            session_id="s",
        )

    load_resources.assert_not_awaited()


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


async def test_runtime_skills_refresh_existing_projected_skill(tmp_path):
    """同名 workspace Skill 必须覆盖为当前 Yuxi 配置源版本。"""
    skill_dir = tmp_path / "auto-platform-query"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("current manifest", encoding="utf-8")
    workspace = SimpleNamespace(
        list_skills=AsyncMock(return_value=[SimpleNamespace(name="auto-platform-query", markdown="stale manifest")]),
        remove_skill=AsyncMock(),
        add_skill=AsyncMock(),
    )
    projection = SimpleNamespace(
        skills=[
            {
                "slug": "auto-platform-query",
                "source_dir": str(skill_dir),
            }
        ]
    )

    await runtime_resources.sync_runtime_skills(
        workspace,
        projection,
        agent_id="worker-agent",
    )

    workspace.remove_skill.assert_awaited_once_with(
        "auto-platform-query",
        agent_id="worker-agent",
    )
    workspace.add_skill.assert_awaited_once_with(
        str(skill_dir),
        agent_id="worker-agent",
    )


async def test_runtime_skills_keep_current_projected_skill(tmp_path):
    """配置源 manifest 未变化时不重复复制 Skill。"""
    skill_dir = tmp_path / "auto-platform-query"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("current manifest", encoding="utf-8")
    workspace = SimpleNamespace(
        list_skills=AsyncMock(return_value=[SimpleNamespace(name="auto-platform-query", markdown="current manifest")]),
        remove_skill=AsyncMock(),
        add_skill=AsyncMock(),
    )
    projection = SimpleNamespace(
        skills=[
            {
                "slug": "auto-platform-query",
                "source_dir": str(skill_dir),
            }
        ]
    )

    await runtime_resources.sync_runtime_skills(
        workspace,
        projection,
        agent_id="worker-agent",
    )

    workspace.remove_skill.assert_not_awaited()
    workspace.add_skill.assert_not_awaited()


async def test_runtime_snapshot_reinstalls_skill_when_non_manifest_files_may_differ():
    """完整 Run 快照不能因 SKILL.md 相同而复用上一轮可能不同的脚本。"""
    workspace = SimpleNamespace(
        list_skills=AsyncMock(return_value=[SimpleNamespace(name="reporter", markdown="# same\n")]),
        remove_skill=AsyncMock(),
        add_skill=AsyncMock(),
    )
    projection = SimpleNamespace(
        skills=[
            {
                "slug": "reporter",
                "name": "Reporter",
                "snapshot_directories": ["scripts"],
                "snapshot_files": [
                    {
                        "path": "SKILL.md",
                        "content_base64": "IyBzYW1lCg==",
                        "executable": False,
                    },
                    {
                        "path": "scripts/run.sh",
                        "content_base64": "ZWNobyBzdWJtaXR0ZWQK",
                        "executable": True,
                    },
                ],
            }
        ]
    )

    await runtime_resources.sync_runtime_skills(workspace, projection, agent_id="runtime-agent")

    workspace.remove_skill.assert_awaited_once_with("reporter", agent_id="runtime-agent")
    workspace.add_skill.assert_awaited_once()


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
