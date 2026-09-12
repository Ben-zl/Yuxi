"""AgentScope Team worker 生命周期投影测试。"""

from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from yuxi.agentscope import team_lifecycle
from yuxi.agentscope.team_lifecycle import TeamLifecycleModule, retain_latest_team_hint
from yuxi.services.agent_run_manifest_service import compute_manifest_fingerprint


@pytest.fixture
def manifest_runtime(monkeypatch):
    """隔离存储边界，保留创建、回复、清单校验与继承的真实流程。"""
    manifest = {
        "run_type": "chat",
        "agent": {"slug": "root", "backend_id": "backend"},
        "resource_snapshot": {"id": "parent-snapshot", "fingerprint": "snapshot-digest"},
    }
    parent = SimpleNamespace(
        id="parent-run",
        uid="u",
        agent_slug="root",
        status="running",
        conversation_id=1,
        manifest=manifest,
        manifest_fingerprint=compute_manifest_fingerprint(manifest),
        input_payload={"model_spec": "snapshot:model"},
    )
    rows = {parent.id: parent}
    binding = SimpleNamespace(
        runtime_active=True,
        active_run_id=None,
        last_reply_id=None,
        created_by_run_id=parent.id,
        parent_thread_id="parent-thread",
        child_thread_id="child-thread",
        subagent_slug="child",
        subagent_thread_relation_id=2,
        worker_agent_id="worker-agent",
        worker_session_id="worker-session",
        team_id="team",
    )
    db = SimpleNamespace(add=Mock(), flush=AsyncMock(), commit=AsyncMock())

    @asynccontextmanager
    async def db_context():
        yield db

    async def create_run(**kwargs):
        """保存测试运行行，后续回复读取同一份持久状态。"""
        run = SimpleNamespace(**{key: value for key, value in kwargs.items() if key != "run_id"})
        run.id = kwargs["run_id"]
        run.status = "running"
        run.worker_id = "worker-owner"
        run.manifest = None
        run.manifest_fingerprint = None
        rows[run.id] = run
        return run

    async def record_manifest(run_id, *, manifest, fingerprint, worker_id):
        """模拟仓储首次写入，禁止 fixture 覆盖既有清单。"""
        run = rows[run_id]
        assert worker_id == run.worker_id
        if run.manifest is not None:
            return run, False
        run.manifest = deepcopy(manifest)
        run.manifest_fingerprint = fingerprint
        return run, True

    runs = SimpleNamespace(
        get_run=AsyncMock(side_effect=lambda run_id: rows.get(run_id)),
        get_run_for_user=AsyncMock(side_effect=lambda run_id, uid: rows.get(run_id) if uid == "u" else None),
        get_active_run_by_thread_for_user=AsyncMock(return_value=parent),
        get_run_by_request_id=AsyncMock(return_value=None),
        create_run=AsyncMock(side_effect=create_run),
        mark_running=AsyncMock(side_effect=lambda run_id, **kwargs: (rows[run_id], True)),
        record_run_manifest=AsyncMock(side_effect=record_manifest),
    )
    bindings = SimpleNamespace(get_by_worker_session=AsyncMock(return_value=binding), create=AsyncMock())
    monkeypatch.setattr(team_lifecycle.pg_manager, "get_async_session_context", db_context)
    monkeypatch.setattr(team_lifecycle, "AgentRunRepository", lambda db: runs)
    monkeypatch.setattr(team_lifecycle, "AgentScopeTeamWorkerRepository", lambda db: bindings)
    monkeypatch.setattr(
        team_lifecycle,
        "SubagentThreadRepository",
        lambda db: SimpleNamespace(
            get_for_user=AsyncMock(return_value=SimpleNamespace(id=2)),
            get_by_child_thread_for_user=AsyncMock(return_value=SimpleNamespace(id=2)),
        ),
    )
    monkeypatch.setattr(
        team_lifecycle,
        "ConversationRepository",
        lambda db: SimpleNamespace(
            get_conversation_by_thread_id=AsyncMock(return_value=SimpleNamespace(id=3)),
        ),
    )
    monkeypatch.setattr(
        team_lifecycle,
        "AgentRepository",
        lambda db: SimpleNamespace(
            get_by_slug=AsyncMock(return_value=SimpleNamespace(is_subagent=True, name="Child")),
        ),
    )
    monkeypatch.setattr(
        team_lifecycle,
        "get_thread_session_by_agentscope_context",
        AsyncMock(return_value=SimpleNamespace(agent_slug="root", thread_id="parent-thread")),
    )
    load = AsyncMock(return_value=SimpleNamespace(model_spec="snapshot:model"))
    heartbeat = Mock()
    monkeypatch.setattr(team_lifecycle, "load_run_resources", load)
    monkeypatch.setattr(team_lifecycle, "start_run_lease_heartbeat", heartbeat)
    monkeypatch.setattr(team_lifecycle, "append_run_stream_event", AsyncMock())
    lifecycle = TeamLifecycleModule(
        storage=_worker_storage(), uid="u", agent_id="leader-agent", session_id="leader-session"
    )
    lifecycle.storage.get_session.return_value = SimpleNamespace(config=SimpleNamespace(workspace_id="workspace"))
    return SimpleNamespace(
        lifecycle=lifecycle,
        db=db,
        parent=parent,
        rows=rows,
        runs=runs,
        binding=binding,
        bindings=bindings,
        load=load,
        heartbeat=heartbeat,
    )


async def _project_first_child(runtime):
    """经 AgentCreate 差分入口创建首次子运行。"""
    runtime.bindings.get_by_worker_session.return_value = None
    await runtime.lifecycle.project_created_member(
        before=team_lifecycle.TeamRosterSnapshot(team_id="team", members={}),
        after=team_lifecycle.TeamRosterSnapshot(
            team_id="team", members={"worker-session": ("worker-agent", "created")}
        ),
        tool_call_id="create-child",
        tool_input={"subagent_type": "child", "prompt": "Task"},
    )


async def test_first_child_and_first_reply_reuse_persisted_parent_snapshot(manifest_runtime):
    """首次回复复用创建时清单，父配置变化不得触发重新捕获。"""
    rt = manifest_runtime
    parent_manifest = deepcopy(rt.parent.manifest)
    await _project_first_child(rt)
    child = next(row for row in rt.rows.values() if row.id != rt.parent.id)
    expected = {**parent_manifest, "run_type": "subagent", "agent": {**parent_manifest["agent"], "slug": "child"}}
    assert child.created_by_run_id == rt.parent.id
    assert child.manifest == expected
    assert child.manifest_fingerprint == compute_manifest_fingerprint(expected)
    rt.runs.get_run_for_user.assert_awaited_once_with("parent-run", "u")
    rt.load.assert_awaited_once_with(rt.db, uid="u", manifest=parent_manifest, agent_slug="child")
    persisted_manifest = child.manifest
    rt.parent.manifest = {**parent_manifest, "resource_snapshot": {"id": "changed-live-snapshot"}}
    rt.binding.active_run_id = child.id
    rt.bindings.get_by_worker_session.return_value = rt.binding

    assert await rt.lifecycle._open_worker_run("worker-session", "first-reply", {}) == (child.id, child.request_id)

    assert child.manifest is persisted_manifest
    assert child.manifest == expected
    assert child.manifest_fingerprint == compute_manifest_fingerprint(expected)
    rt.runs.record_run_manifest.assert_awaited_once()
    rt.runs.create_run.assert_awaited_once()
    rt.runs.get_run_for_user.assert_awaited_once()
    assert rt.load.await_args.kwargs["manifest"] is persisted_manifest
    assert rt.binding.last_reply_id == "first-reply"


@pytest.mark.parametrize(
    "invalid", ["missing_parent", "missing_manifest", "changed_fingerprint", "unavailable_snapshot"]
)
async def test_first_child_requires_valid_explicit_parent_snapshot(manifest_runtime, invalid):
    """创建时父运行缺失、清单篡改或快照不可加载必须阻止发布。"""
    rt = manifest_runtime
    error = "缺少父 Run 提交快照"
    if invalid == "missing_parent":
        rt.rows.clear()
    elif invalid == "missing_manifest":
        rt.parent.manifest = None
    elif invalid == "changed_fingerprint":
        rt.parent.manifest_fingerprint = "changed"
        error = "父 Run manifest 指纹不一致"
    else:
        rt.load.side_effect = ValueError("parent snapshot unavailable")
        error = "parent snapshot unavailable"

    with pytest.raises((RuntimeError, ValueError), match=error):
        await _project_first_child(rt)

    rt.runs.get_run_for_user.assert_awaited_once_with("parent-run", "u")
    rt.runs.record_run_manifest.assert_not_awaited()
    rt.bindings.create.assert_not_awaited()
    rt.db.commit.assert_not_awaited()
    rt.heartbeat.assert_not_called()
    if invalid != "unavailable_snapshot":
        rt.load.assert_not_awaited()


@pytest.mark.parametrize("invalid", ["changed_fingerprint", "unavailable_snapshot"])
async def test_first_reply_revalidates_persisted_child_snapshot(manifest_runtime, invalid):
    """首次回复必须重新校验已有清单及其可加载性，不能直接复用 lease。"""
    rt = manifest_runtime
    await _project_first_child(rt)
    child = next(row for row in rt.rows.values() if row.id != rt.parent.id)
    rt.binding.active_run_id = child.id
    rt.bindings.get_by_worker_session.return_value = rt.binding
    rt.db.commit.reset_mock()
    rt.heartbeat.reset_mock()
    rt.load.reset_mock()
    if invalid == "changed_fingerprint":
        child.manifest_fingerprint = "changed"
        error = "child Run manifest 指纹不一致"
    else:
        rt.load.side_effect = ValueError("child snapshot unavailable")
        error = "child snapshot unavailable"

    with pytest.raises((RuntimeError, ValueError), match=error):
        await rt.lifecycle._open_worker_run("worker-session", "first-reply", {})

    assert rt.binding.last_reply_id is None
    rt.db.commit.assert_not_awaited()
    rt.heartbeat.assert_not_called()
    rt.runs.record_run_manifest.assert_awaited_once()
    if invalid == "changed_fingerprint":
        rt.load.assert_not_awaited()


@pytest.mark.parametrize("parent_status", [None, "pending", "completed"])
async def test_continuation_requires_current_running_root(manifest_runtime, parent_status):
    """旧创建者存在也不能替代当前正在运行的根 Run。"""
    rt = manifest_runtime
    rt.runs.get_active_run_by_thread_for_user.return_value = (
        SimpleNamespace(status=parent_status) if parent_status else None
    )

    with pytest.raises(ValueError, match="缺少正在执行的父 Run"):
        await rt.lifecycle._open_worker_run("worker-session", "next-reply", {})

    rt.runs.get_active_run_by_thread_for_user.assert_awaited_once_with(
        agent_slug="root",
        conversation_thread_id="parent-thread",
        uid="u",
    )
    rt.runs.create_run.assert_not_awaited()
    rt.load.assert_not_awaited()
    rt.db.commit.assert_not_awaited()


async def test_continuation_inherits_current_root_not_original_creator(manifest_runtime):
    """后续回复绑定当前根运行的快照，不取最初创建者或 latest Run。"""
    rt = manifest_runtime
    current = deepcopy(rt.parent)
    current.id = "current-root"
    current.manifest["resource_snapshot"]["id"] = "current-snapshot"
    current.manifest_fingerprint = compute_manifest_fingerprint(current.manifest)
    rt.rows[current.id] = current
    rt.runs.get_active_run_by_thread_for_user.return_value = current

    child_id, _ = await rt.lifecycle._open_worker_run("worker-session", "next-reply", {})

    child = rt.rows[child_id]
    assert child.created_by_run_id == current.id
    assert child.manifest["resource_snapshot"] == current.manifest["resource_snapshot"]
    assert child.manifest["resource_snapshot"] != rt.parent.manifest["resource_snapshot"]
    assert child.manifest_fingerprint == compute_manifest_fingerprint(child.manifest)
    rt.runs.get_active_run_by_thread_for_user.assert_awaited_once_with(
        agent_slug="root",
        conversation_thread_id="parent-thread",
        uid="u",
    )
    rt.load.assert_awaited_once_with(rt.db, uid="u", manifest=current.manifest, agent_slug="child")


async def test_repeated_continuation_reply_reuses_existing_manifest(manifest_runtime):
    """相同 continuation reply 重入只校验既有清单，不创建第二个运行。"""
    rt = manifest_runtime
    first = await rt.lifecycle._open_worker_run("worker-session", "next-reply", {})
    child = rt.rows[first[0]]
    persisted = deepcopy(child.manifest)
    rt.runs.get_run_by_request_id.return_value = child

    assert await rt.lifecycle._open_worker_run("worker-session", "next-reply", {}) == first

    rt.runs.create_run.assert_awaited_once()
    rt.runs.record_run_manifest.assert_awaited_once()
    assert child.manifest == persisted
    assert child.manifest_fingerprint == compute_manifest_fingerprint(persisted)
    assert rt.load.await_count == 2
    assert rt.load.await_args.kwargs["manifest"] is child.manifest


@pytest.mark.parametrize("persisted", [None, SimpleNamespace(manifest_fingerprint="conflicting-fingerprint")])
async def test_first_child_rejects_missing_or_conflicting_persisted_manifest(manifest_runtime, persisted):
    """仓储拒绝写入或返回冲突指纹时，不得发布 Team binding。"""
    rt = manifest_runtime
    rt.runs.record_run_manifest.side_effect = None
    rt.runs.record_run_manifest.return_value = (persisted, False)
    error = "child Run 不存在" if persisted is None else "manifest 已变化"

    with pytest.raises(RuntimeError, match=error):
        await _project_first_child(rt)

    rt.bindings.create.assert_not_awaited()
    rt.db.commit.assert_not_awaited()
    rt.heartbeat.assert_not_called()


async def test_continuation_requires_original_creator_identity(manifest_runtime):
    """缺少创建者时无法确定根 Agent，不得猜测其他活跃运行。"""
    rt = manifest_runtime
    rt.rows.clear()

    with pytest.raises(ValueError, match="缺少创建者 Run"):
        await rt.lifecycle._open_worker_run("worker-session", "next-reply", {})

    rt.runs.get_active_run_by_thread_for_user.assert_not_awaited()
    rt.runs.create_run.assert_not_awaited()
    rt.db.commit.assert_not_awaited()


def _worker_storage() -> SimpleNamespace:
    """构造带真实 leader 关系的最小 AgentScope Storage 桩。"""
    return SimpleNamespace(
        get_session=AsyncMock(return_value=SimpleNamespace(team_id="team")),
        get_team=AsyncMock(
            return_value=SimpleNamespace(
                id="team",
                session_id="leader-session",
                leader_agent_id="leader-agent",
            )
        ),
        get_agent=AsyncMock(return_value=SimpleNamespace(data=SimpleNamespace(name="leader-name"))),
    )


async def test_failed_worker_reply_uses_native_notification_and_finishes_once(monkeypatch):
    """worker 失败由原生链路通知，Yuxi 只写一次 child 终态。"""
    order = []

    lifecycle = TeamLifecycleModule(
        storage=_worker_storage(),
        uid="u",
        agent_id="worker-agent",
        session_id="worker-session",
    )
    binding = SimpleNamespace(worker_session_id="worker-session", child_thread_id="child-thread")
    monkeypatch.setattr(lifecycle, "_wait_for_binding", AsyncMock(return_value=binding))
    monkeypatch.setattr(lifecycle, "_open_worker_run", AsyncMock(return_value=("child-run", "child-request")))

    async def _finish_worker_run(**kwargs) -> str:
        order.append(("finish", kwargs["terminal_status"]))
        return kwargs["terminal_status"]

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
    assert order == [("finish", "failed")]


async def test_worker_setup_failure_finishes_projected_child_run(monkeypatch):
    """Agent 尚未装配完成时，也必须结束 AgentCreate 已投影的 child Run。"""
    lifecycle = TeamLifecycleModule(
        storage=_worker_storage(),
        uid="u",
        agent_id="worker-agent",
        session_id="worker-session",
    )
    binding = SimpleNamespace(
        active_run_id="child-run",
        child_thread_id="child-thread",
    )
    monkeypatch.setattr(lifecycle, "_wait_for_binding", AsyncMock(return_value=binding))

    @asynccontextmanager
    async def _db_context():
        yield object()

    class FakeRunRepository:
        def __init__(self, _db):
            pass

        async def get_run(self, run_id: str):
            assert run_id == "child-run"
            return SimpleNamespace(status="running", request_id="child-request")

    monkeypatch.setattr(team_lifecycle.pg_manager, "get_async_session_context", _db_context)
    monkeypatch.setattr(team_lifecycle, "AgentRunRepository", FakeRunRepository)
    finish_run = AsyncMock(return_value="failed")
    monkeypatch.setattr(lifecycle, "_finish_worker_run", finish_run)
    append_event = AsyncMock()
    monkeypatch.setattr(team_lifecycle, "append_run_stream_event", append_event)

    assert await lifecycle.fail_setup() is True
    finish_run.assert_awaited_once_with(
        run_id="child-run",
        terminal_status="failed",
        error_type="agentscope_team_setup",
        error_message="Team worker 会话准备失败，请检查模型、工具、Skill 和知识库配置",
        text="",
        reasoning="",
        usage={},
        tool_calls=[],
    )
    append_event.assert_awaited_once()
    assert append_event.await_args.args[:2] == ("child-run", "end")
    assert append_event.await_args.args[2]["status"] == "failed"


async def test_top_level_setup_failure_does_not_wait_for_worker_binding(monkeypatch):
    """普通或 leader Session 装配失败时不得等待不存在的 worker binding。"""
    storage = _worker_storage()
    storage.get_session.return_value = SimpleNamespace(team_id=None)
    lifecycle = TeamLifecycleModule(
        storage=storage,
        uid="u",
        agent_id="leader-agent",
        session_id="leader-session",
    )
    wait_for_binding = AsyncMock()
    monkeypatch.setattr(lifecycle, "_wait_for_binding", wait_for_binding)

    assert await lifecycle.fail_setup() is False
    wait_for_binding.assert_not_awaited()


async def test_worker_finish_loser_does_not_persist_duplicate_output(monkeypatch):
    """并发终态竞争失败者不得再写重复消息或终态事件。"""
    lifecycle = TeamLifecycleModule(
        storage=_worker_storage(),
        uid="u",
        agent_id="worker-agent",
        session_id="worker-session",
    )
    db = SimpleNamespace(add=AsyncMock(), commit=AsyncMock())

    @asynccontextmanager
    async def _db_context():
        yield db

    run = SimpleNamespace(status="completed")
    repository = SimpleNamespace(
        set_terminal_status=AsyncMock(return_value=(run, False)),
    )
    monkeypatch.setattr(team_lifecycle.pg_manager, "get_async_session_context", _db_context)
    monkeypatch.setattr(team_lifecycle, "AgentRunRepository", lambda _db: repository)

    result = await lifecycle._finish_worker_run(
        run_id="child-run",
        terminal_status="completed",
        error_type=None,
        error_message=None,
        text="duplicate",
        reasoning="",
        usage={},
        tool_calls=[],
    )

    assert result is None
    db.add.assert_not_awaited()
    db.commit.assert_not_awaited()


async def test_worker_reply_projects_native_anthropic_cache_usage(monkeypatch):
    """worker 原生事件必须按实际 provider 口径写入缓存用量。"""
    lifecycle = TeamLifecycleModule(
        storage=_worker_storage(),
        uid="u",
        agent_id="worker-agent",
        session_id="worker-session",
    )
    binding = SimpleNamespace(worker_session_id="worker-session", child_thread_id="child-thread")
    monkeypatch.setattr(lifecycle, "_wait_for_binding", AsyncMock(return_value=binding))
    monkeypatch.setattr(lifecycle, "_open_worker_run", AsyncMock(return_value=("child-run", "child-request")))
    finish_run = AsyncMock(return_value="completed")
    monkeypatch.setattr(lifecycle, "_finish_worker_run", finish_run)
    monkeypatch.setattr(team_lifecycle, "append_run_stream_event", AsyncMock())

    async def _reply(**_kwargs):
        yield {"type": "REPLY_START", "reply_id": "reply-1"}
        yield {
            "type": "MODEL_CALL_START",
            "reply_id": "reply-1",
            "model_name": "claude-test",
        }
        yield {
            "type": "MODEL_CALL_END",
            "reply_id": "reply-1",
            "input_tokens": 39,
            "output_tokens": 5,
            "cache_input_tokens": 128,
            "cache_creation_input_tokens": 3,
        }
        yield {"type": "REPLY_END", "reply_id": "reply-1", "finished_reason": "completed"}

    assert [
        item
        async for item in lifecycle.project_worker_reply(
            {},
            _reply,
            cache_input_mode="additive",
        )
    ]
    usage = finish_run.await_args.kwargs["usage"]
    assert usage["input_tokens"] == 170
    assert usage["output_tokens"] == 5
    model_usage = usage["run"]["models"]["claude-test"]
    assert model_usage["cache_read_input_tokens"] == 128
    assert model_usage["cache_creation_input_tokens"] == 3
    assert model_usage["cache_observed_input_tokens"] == 170
    assert model_usage["cache_observed_call_count"] == 1
    assert model_usage["cache_hit_ratio"] == 128 / 170


async def test_intermediate_reply_ends_are_candidates_until_generator_exhausts(monkeypatch):
    """原生纠正循环吞掉的中间 REPLY_END 不得提前结束 child Run。"""
    order = []

    lifecycle = TeamLifecycleModule(
        storage=_worker_storage(),
        uid="u",
        agent_id="worker-agent",
        session_id="worker-session",
    )
    binding = SimpleNamespace(worker_session_id="worker-session", child_thread_id="child-thread")
    monkeypatch.setattr(lifecycle, "_wait_for_binding", AsyncMock(return_value=binding))
    monkeypatch.setattr(lifecycle, "_open_worker_run", AsyncMock(return_value=("child-run", "child-request")))

    async def _finish_worker_run(**kwargs) -> str:
        order.append(("finish", kwargs["terminal_status"]))
        return kwargs["terminal_status"]

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
        yield {"type": "HINT_BLOCK", "reply_id": "reply-1", "hint": "必须调用 TeamSay"}
        yield {"type": "TEXT_BLOCK_DELTA", "reply_id": "reply-1", "delta": "补充回报"}
        yield {"type": "REPLY_END", "reply_id": "reply-1", "finished_reason": "completed"}

    assert [item async for item in lifecycle.project_worker_reply({}, _reply)]
    assert order == [("finish", "completed")]
    end_events = [call for call in team_lifecycle.append_run_stream_event.await_args_list if call.args[1] == "end"]
    assert len(end_events) == 1


async def test_completed_worker_reply_with_successful_team_say_is_not_duplicated(monkeypatch):
    """worker 已成功 TeamSay 时，平台不得重复向 leader 投递结果。"""
    lifecycle = TeamLifecycleModule(
        storage=_worker_storage(),
        uid="u",
        agent_id="worker-agent",
        session_id="worker-session",
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


async def test_successful_team_say_finishes_before_final_reply_end_is_consumed(monkeypatch):
    """外层在最终 REPLY_END 停止消费时，child Run 必须已经完成。"""
    lifecycle = TeamLifecycleModule(
        storage=_worker_storage(),
        uid="u",
        agent_id="worker-agent",
        session_id="worker-session",
    )
    binding = SimpleNamespace(worker_session_id="worker-session", child_thread_id="child-thread")
    monkeypatch.setattr(lifecycle, "_wait_for_binding", AsyncMock(return_value=binding))
    monkeypatch.setattr(lifecycle, "_open_worker_run", AsyncMock(return_value=("child-run", "child-request")))
    finish_run = AsyncMock(return_value="completed")
    monkeypatch.setattr(lifecycle, "_finish_worker_run", finish_run)
    append_event = AsyncMock()
    monkeypatch.setattr(team_lifecycle, "append_run_stream_event", append_event)

    async def _reply(**_kwargs):
        yield {"type": "REPLY_START", "reply_id": "reply-1"}
        yield {
            "type": "HINT_BLOCK",
            "reply_id": "reply-1",
            "hint": '<team-message from="leader-name">\n任务\n</team-message>',
            "source": '{"label":"team_message","sublabel":"leader-name"}',
        }
        yield {
            "type": "TOOL_CALL_START",
            "reply_id": "reply-1",
            "tool_call_id": "team-say-1",
            "tool_call_name": "TeamSay",
        }
        yield {
            "type": "TOOL_CALL_DELTA",
            "reply_id": "reply-1",
            "tool_call_id": "team-say-1",
            "delta": '{"to":"leader-name","message":"done"}',
        }
        yield {
            "type": "TOOL_RESULT_END",
            "reply_id": "reply-1",
            "tool_call_id": "team-say-1",
            "state": "success",
        }
        yield {"type": "REPLY_END", "reply_id": "reply-1", "finished_reason": "completed"}

    stream = lifecycle.project_worker_reply({}, _reply)
    while (await anext(stream))["type"] != "REPLY_END":
        pass

    finish_run.assert_awaited_once()
    assert finish_run.await_args.kwargs["terminal_status"] == "completed"
    end_events = [call for call in append_event.await_args_list if call.args[1] == "end"]
    assert len(end_events) == 1
    await stream.aclose()
    finish_run.assert_awaited_once()


async def test_fourth_missing_team_say_finishes_as_failed_before_reply_end(monkeypatch):
    """三次纠正后仍未 TeamSay 时，第四个候选终态必须立即失败。"""
    lifecycle = TeamLifecycleModule(
        storage=_worker_storage(),
        uid="u",
        agent_id="worker-agent",
        session_id="worker-session",
    )
    binding = SimpleNamespace(worker_session_id="worker-session", child_thread_id="child-thread")
    monkeypatch.setattr(lifecycle, "_wait_for_binding", AsyncMock(return_value=binding))
    monkeypatch.setattr(lifecycle, "_open_worker_run", AsyncMock(return_value=("child-run", "child-request")))
    finish_run = AsyncMock(return_value="failed")
    monkeypatch.setattr(lifecycle, "_finish_worker_run", finish_run)
    monkeypatch.setattr(team_lifecycle, "append_run_stream_event", AsyncMock())

    async def _reply(**_kwargs):
        yield {"type": "REPLY_START", "reply_id": "reply-1"}
        for _ in range(4):
            yield {"type": "REPLY_END", "reply_id": "reply-1", "finished_reason": "completed"}

    stream = lifecycle.project_worker_reply({}, _reply)
    for _ in range(5):
        await anext(stream)

    finish_run.assert_awaited_once()
    assert finish_run.await_args.kwargs["terminal_status"] == "failed"
    assert finish_run.await_args.kwargs["error_message"] == ("Team worker 连续 3 次纠正后仍未通过 TeamSay 回报 leader")
    await stream.aclose()


async def test_worker_reply_stream_without_reply_end_still_finishes_child_run(monkeypatch):
    """取消导致事件流直接耗尽时，child Run 也必须进入终态。"""
    lifecycle = TeamLifecycleModule(
        storage=_worker_storage(),
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
        storage=_worker_storage(),
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
            "hint": '<team-message from="leader-name">\n旧任务\n</team-message>',
            "source": '{"label":"team","sublabel":"leader-name"}',
        }
        yield {
            "type": "HINT_BLOCK",
            "hint": '<team-message from="leader-name">\n最新任务正文\n</team-message>',
            "source": '{"label":"team","sublabel":"leader-name"}',
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


def test_retain_latest_team_hint_drops_peer_messages():
    """worker 推理前必须移除 peer 回报，只保留 leader 的最新任务。"""
    leader_hint = SimpleNamespace(
        source='{"label":"team","sublabel":"leader"}',
        hint="leader 新任务",
    )
    peer_hint = SimpleNamespace(
        source='{"label":"team","sublabel":"peer-worker"}',
        hint="peer 完成回报",
    )
    unrelated = SimpleNamespace(source='{"label":"system"}', hint="系统状态")
    message = SimpleNamespace(content=[leader_hint, peer_hint, unrelated])
    agent = SimpleNamespace(state=SimpleNamespace(context=[message]))

    retain_latest_team_hint(agent, leader_name="leader")

    assert message.content == [leader_hint, unrelated]


def test_broadcast_team_say_does_not_count_as_leader_report():
    """to=null 是全队广播，不能作为 worker 已定向回报 leader 的终态。"""
    calls = [
        {
            "name": "TeamSay",
            "status": "success",
            "args": {"content": "完成", "to": None},
        }
    ]

    assert team_lifecycle._reported_to_team_leader(calls, "leader") is False


def test_normalized_team_say_metadata_counts_as_leader_report():
    """工具执行事实表明已定向 leader 时，原始 null 参数仍可正常收口。"""
    calls = [
        {
            "name": "TeamSay",
            "status": "success",
            "args": {"content": "完成", "to": None},
            "metadata": {"team_target": "leader"},
        }
    ]

    assert team_lifecycle._reported_to_team_leader(calls, "leader") is True


async def test_peer_team_message_does_not_open_child_run(monkeypatch):
    """peer 回报即使触发 reply，也不能投影成新的任务型 child Run。"""
    lifecycle = TeamLifecycleModule(
        storage=_worker_storage(),
        uid="u",
        agent_id="worker-agent",
        session_id="worker-session",
    )
    binding = SimpleNamespace(worker_session_id="worker-session", child_thread_id="child-thread")
    monkeypatch.setattr(lifecycle, "_wait_for_binding", AsyncMock(return_value=binding))
    open_run = AsyncMock(return_value=("child-run", "child-request"))
    monkeypatch.setattr(lifecycle, "_open_worker_run", open_run)
    finish_run = AsyncMock()
    monkeypatch.setattr(lifecycle, "_finish_worker_run", finish_run)

    async def _reply(**_kwargs):
        yield {"type": "REPLY_START", "reply_id": "reply-peer"}
        yield {
            "type": "HINT_BLOCK",
            "hint": '<team-message from="peer-worker">\n完成确认\n</team-message>',
            "source": '{"label":"team","sublabel":"peer-worker"}',
        }
        yield {"type": "MODEL_CALL_START", "reply_id": "reply-peer"}
        yield {"type": "REPLY_END", "reply_id": "reply-peer", "finished_reason": "completed"}

    assert [item async for item in lifecycle.project_worker_reply({}, _reply)]
    open_run.assert_not_awaited()
    finish_run.assert_not_awaited()
