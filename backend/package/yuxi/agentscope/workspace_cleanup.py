"""按 Yuxi 线程事实安全清理 AgentScope 物理 Workspace。"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.repositories.agentscope_team_workers import AgentScopeTeamWorkerRepository
from yuxi.repositories.agentscope_thread_sessions import get_thread_session


class WorkspaceCleanupConflict(RuntimeError):
    """Workspace 清理前置条件尚未满足。"""


def _safe_existing_path(base: Path, *parts: str) -> Path | None:
    """返回 base 内可删除的现存普通路径，并拒绝逃逸和符号链接。"""
    root = base.resolve()
    candidate = root.joinpath(*parts)
    resolved = candidate.resolve(strict=False)
    if resolved == root or not resolved.is_relative_to(root):
        raise WorkspaceCleanupConflict(f"Workspace 路径越界: {candidate}")

    relative = candidate.relative_to(root)
    current = root
    for part in relative.parts:
        current = current / part
        try:
            current.lstat()
        except FileNotFoundError:
            return None
        if current.is_symlink():
            raise WorkspaceCleanupConflict(f"拒绝删除符号链接 Workspace: {current}")
    return candidate


async def _remove_labeled_containers(workspace_ids: set[str]) -> set[str]:
    """强制删除带权威 workspace 标签的残留 Docker 容器。"""
    import aiodocker

    removed: set[str] = set()
    client = aiodocker.Docker()
    try:
        for workspace_id in workspace_ids:
            containers = await client.containers.list(
                all=True,
                filters={"label": [f"agentscope.workspace.id={workspace_id}"]},
            )
            for container in containers:
                await container.delete(force=True)
                removed.add(workspace_id)
            remaining = await client.containers.list(
                all=True,
                filters={"label": [f"agentscope.workspace.id={workspace_id}"]},
            )
            if remaining:
                raise RuntimeError(f"Workspace 容器删除后仍存在: {workspace_id}")
    finally:
        await client.close()
    return removed


async def destroy_thread_workspaces(
    db: AsyncSession,
    *,
    storage,
    workspace_manager,
    uid: str,
    thread_id: str,
    base_dir: str,
    backend: str,
) -> dict[str, list[str]]:
    """校验 Session 已删除后，清理 parent 和全部历史 worker Workspace。"""
    mapping = await get_thread_session(db, uid=uid, thread_id=thread_id)
    if mapping is None:
        raise FileNotFoundError("线程不存在 AgentScope 映射")
    bindings = await AgentScopeTeamWorkerRepository(db).list_for_parent_thread(
        uid=uid,
        parent_thread_id=thread_id,
    )
    records = [
        (
            mapping.agentscope_agent_id,
            mapping.agentscope_session_id,
            mapping.agentscope_workspace_id,
        ),
        *[
            (binding.worker_agent_id, binding.worker_session_id, binding.agentscope_workspace_id)
            for binding in bindings
        ],
    ]
    missing_ids = [session_id for _, session_id, workspace_id in records if not workspace_id]
    if missing_ids and backend != "docker":
        raise WorkspaceCleanupConflict(f"Session 缺少已持久化 workspace_id: {', '.join(missing_ids)}")
    for agent_id, session_id, _ in records:
        if await storage.get_session(uid, agent_id, session_id) is not None:
            raise WorkspaceCleanupConflict(f"AgentScope Session 仍存在: {session_id}")

    workspace_ids = {str(workspace_id) for _, _, workspace_id in records if workspace_id}
    for workspace_id in workspace_ids:
        await workspace_manager.close(workspace_id)

    removed_ids: set[str] = set()
    if backend == "docker":
        removed_ids.update(await _remove_labeled_containers(workspace_ids))

    base = Path(base_dir)
    for agent_id, _, workspace_id in records:
        if backend == "docker":
            candidates = [(uid, agent_id)]
            if workspace_id:
                candidates.insert(0, (str(workspace_id),))
        else:
            candidates = [(agent_id,)]
        for parts in candidates:
            target = _safe_existing_path(base, *parts)
            if target is None:
                continue
            await asyncio.to_thread(shutil.rmtree, target)
            if workspace_id:
                removed_ids.add(str(workspace_id))

    return {
        "destroyed_workspace_ids": sorted(removed_ids),
        "already_absent_workspace_ids": sorted(workspace_ids - removed_ids),
    }
