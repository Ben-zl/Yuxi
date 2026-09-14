"""AgentScope Workspace 物理清理边界测试。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from yuxi.agentscope import workspace_cleanup
from yuxi.agentscope.workspace_cleanup import WorkspaceCleanupConflict, destroy_thread_workspaces


async def test_destroy_docker_parent_and_worker_new_and_legacy_paths(tmp_path, monkeypatch):
    """parent 与历史 worker 的新旧目录和缓存实例都必须清理。"""
    mapping = SimpleNamespace(
        agentscope_agent_id="parent-agent",
        agentscope_session_id="parent-session",
        agentscope_workspace_id="parent-workspace",
    )
    binding = SimpleNamespace(
        worker_agent_id="worker-agent",
        worker_session_id="worker-session",
        agentscope_workspace_id="worker-workspace",
    )
    monkeypatch.setattr(workspace_cleanup, "get_thread_session", AsyncMock(return_value=mapping))
    monkeypatch.setattr(
        workspace_cleanup,
        "AgentScopeTeamWorkerRepository",
        lambda _db: SimpleNamespace(list_for_parent_thread=AsyncMock(return_value=[binding])),
    )
    monkeypatch.setattr(
        workspace_cleanup,
        "_remove_labeled_containers",
        AsyncMock(return_value={"worker-workspace"}),
    )
    for relative in (
        "parent-workspace",
        "worker-workspace",
        "u/parent-agent",
        "u/worker-agent",
    ):
        path = tmp_path / relative
        path.mkdir(parents=True)
        (path / "file.txt").write_text("data", encoding="utf-8")
    manager = SimpleNamespace(close=AsyncMock())
    storage = SimpleNamespace(get_session=AsyncMock(return_value=None))

    result = await destroy_thread_workspaces(
        SimpleNamespace(),
        storage=storage,
        workspace_manager=manager,
        uid="u",
        thread_id="thread",
        base_dir=str(tmp_path),
        backend="docker",
    )

    assert result == {
        "destroyed_workspace_ids": ["parent-workspace", "worker-workspace"],
        "already_absent_workspace_ids": [],
    }
    assert not any((tmp_path / relative).exists() for relative in ("parent-workspace", "worker-workspace"))
    assert manager.close.await_count == 2


async def test_destroy_rejects_existing_session_and_missing_workspace_id(tmp_path, monkeypatch):
    """物理删除只接受已持久化 ID 且 Session 已逻辑删除的映射。"""
    mapping = SimpleNamespace(
        agentscope_agent_id="parent-agent",
        agentscope_session_id="parent-session",
        agentscope_workspace_id=None,
    )
    monkeypatch.setattr(workspace_cleanup, "get_thread_session", AsyncMock(return_value=mapping))
    monkeypatch.setattr(
        workspace_cleanup,
        "AgentScopeTeamWorkerRepository",
        lambda _db: SimpleNamespace(list_for_parent_thread=AsyncMock(return_value=[])),
    )
    kwargs = {
        "storage": SimpleNamespace(get_session=AsyncMock(return_value=object())),
        "workspace_manager": SimpleNamespace(close=AsyncMock()),
        "uid": "u",
        "thread_id": "thread",
        "base_dir": str(tmp_path),
        "backend": "local",
    }
    with pytest.raises(WorkspaceCleanupConflict, match="缺少已持久化"):
        await destroy_thread_workspaces(SimpleNamespace(), **kwargs)

    mapping.agentscope_workspace_id = "workspace"
    with pytest.raises(WorkspaceCleanupConflict, match="Session 仍存在"):
        await destroy_thread_workspaces(SimpleNamespace(), **kwargs)


async def test_destroy_legacy_docker_path_without_workspace_id_after_session_deleted(tmp_path, monkeypatch):
    """历史 Docker 映射缺少 workspace_id 时仍按 uid/agent_id 回收目录。"""
    mapping = SimpleNamespace(
        agentscope_agent_id="legacy-agent",
        agentscope_session_id="legacy-session",
        agentscope_workspace_id=None,
    )
    monkeypatch.setattr(workspace_cleanup, "get_thread_session", AsyncMock(return_value=mapping))
    monkeypatch.setattr(
        workspace_cleanup,
        "AgentScopeTeamWorkerRepository",
        lambda _db: SimpleNamespace(list_for_parent_thread=AsyncMock(return_value=[])),
    )
    remove_labeled = AsyncMock(return_value=set())
    monkeypatch.setattr(workspace_cleanup, "_remove_labeled_containers", remove_labeled)
    legacy_path = tmp_path / "u" / "legacy-agent"
    legacy_path.mkdir(parents=True)
    (legacy_path / "file.txt").write_text("data", encoding="utf-8")
    manager = SimpleNamespace(close=AsyncMock())

    result = await destroy_thread_workspaces(
        SimpleNamespace(),
        storage=SimpleNamespace(get_session=AsyncMock(return_value=None)),
        workspace_manager=manager,
        uid="u",
        thread_id="thread",
        base_dir=str(tmp_path),
        backend="docker",
    )

    assert result == {
        "destroyed_workspace_ids": [],
        "already_absent_workspace_ids": [],
    }
    assert not legacy_path.exists()
    manager.close.assert_not_awaited()
    remove_labeled.assert_awaited_once_with(set())


async def test_destroy_rejects_symlink_workspace(tmp_path, monkeypatch):
    """清理不得跟随符号链接删除 base 之外的数据。"""
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    target = tmp_path / "agent"
    target.symlink_to(outside, target_is_directory=True)
    mapping = SimpleNamespace(
        agentscope_agent_id="agent",
        agentscope_session_id="session",
        agentscope_workspace_id="workspace",
    )
    monkeypatch.setattr(workspace_cleanup, "get_thread_session", AsyncMock(return_value=mapping))
    monkeypatch.setattr(
        workspace_cleanup,
        "AgentScopeTeamWorkerRepository",
        lambda _db: SimpleNamespace(list_for_parent_thread=AsyncMock(return_value=[])),
    )

    with pytest.raises(WorkspaceCleanupConflict, match="路径越界|符号链接"):
        await destroy_thread_workspaces(
            SimpleNamespace(),
            storage=SimpleNamespace(get_session=AsyncMock(return_value=None)),
            workspace_manager=SimpleNamespace(close=AsyncMock()),
            uid="u",
            thread_id="thread",
            base_dir=str(tmp_path),
            backend="local",
        )
    assert outside.exists()


def test_safe_existing_path_rejects_nested_symlink_and_escape(tmp_path):
    """路径中的任一符号链接和显式越界都必须在删除前失败。"""
    real_uid = tmp_path / "real-user"
    real_uid.mkdir()
    (tmp_path / "u").symlink_to(real_uid, target_is_directory=True)
    (real_uid / "agent").mkdir()

    with pytest.raises(WorkspaceCleanupConflict, match="符号链接"):
        workspace_cleanup._safe_existing_path(tmp_path, "u", "agent")
    with pytest.raises(WorkspaceCleanupConflict, match="路径越界"):
        workspace_cleanup._safe_existing_path(tmp_path, "..", "outside")
