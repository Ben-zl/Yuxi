"""统一访问 AgentScope 线程 workspace 的用户可见文件。"""

from __future__ import annotations

import json
import os
from pathlib import PurePosixPath

from yuxi.agentscope.client import AgentScopeServiceClient, AgentScopeServiceError, WorkspaceFileListing
from yuxi.repositories.agentscope_thread_sessions import get_thread_session

VIRTUAL_USER_DATA_ROOT = PurePosixPath("/home/gem/user-data")
WORKSPACE_ROOT = PurePosixPath("/workspace")
VISIBLE_NAMESPACES = ("uploads", "outputs")
ARTIFACT_MANIFEST = PurePosixPath("/workspace/data/yuxi-artifacts.json")


def virtual_to_workspace_path(path: str) -> str:
    """把用户虚拟路径安全映射到 AgentScope uploads/outputs。"""
    target = PurePosixPath("/" + path.lstrip("/"))
    if ".." in target.parts or not target.is_relative_to(VIRTUAL_USER_DATA_ROOT):
        raise ValueError("文件路径不在用户数据目录")
    relative = target.relative_to(VIRTUAL_USER_DATA_ROOT)
    if not relative.parts or relative.parts[0] not in VISIBLE_NAMESPACES:
        raise ValueError("只能访问 uploads 或 outputs")
    return str(WORKSPACE_ROOT / relative)


def workspace_to_virtual_path(path: str) -> str:
    """把 AgentScope 用户文件路径映射为前端虚拟路径。"""
    target = PurePosixPath(path)
    if not target.is_absolute() or ".." in target.parts or not target.is_relative_to(WORKSPACE_ROOT):
        raise ValueError("无效的 workspace 文件路径")
    relative = target.relative_to(WORKSPACE_ROOT)
    if not relative.parts or relative.parts[0] not in VISIBLE_NAMESPACES:
        raise ValueError("内部 workspace 文件不可见")
    return str(VIRTUAL_USER_DATA_ROOT / relative)


async def resolve_thread_workspace(db, *, uid: str, thread_id: str):
    """解析线程映射，未迁移线程返回 None。"""
    mapping = await get_thread_session(db, uid=uid, thread_id=thread_id)
    if mapping is None:
        return None
    client = AgentScopeServiceClient(os.getenv("AGENTSCOPE_BASE_URL", "http://agentscope:8100"))
    return client, mapping


async def list_visible_files(db, *, uid: str, thread_id: str, max_entries: int = 500) -> WorkspaceFileListing:
    """仅列出文件树可见的 uploads/outputs，并保留目录和完整性标记。"""
    context = await resolve_thread_workspace(db, uid=uid, thread_id=thread_id)
    if context is None:
        return WorkspaceFileListing(items=[])
    client, mapping = context
    files: list[dict] = []
    truncated = False
    for namespace in VISIBLE_NAMESPACES:
        remaining = max_entries - len(files)
        if remaining <= 0:
            truncated = True
            break
        try:
            listing = await client.list_workspace_files(
                uid,
                mapping.agentscope_agent_id,
                mapping.agentscope_session_id,
                root=f"/workspace/{namespace}",
                max_entries=remaining,
            )
        except AgentScopeServiceError as exc:
            if exc.status_code == 404:
                continue
            raise
        truncated = truncated or listing.truncated
        for item in listing.items:
            try:
                item = {**item, "path": workspace_to_virtual_path(str(item["path"]))}
            except (KeyError, ValueError):
                continue
            files.append(item)
    return WorkspaceFileListing(items=files, truncated=truncated)


async def read_file(db, *, uid: str, thread_id: str, virtual_path: str, max_bytes: int = 25 * 1024 * 1024) -> bytes:
    """读取当前线程的一个用户可见文件。"""
    context = await resolve_thread_workspace(db, uid=uid, thread_id=thread_id)
    if context is None:
        raise FileNotFoundError(virtual_path)
    client, mapping = context
    try:
        return await client.read_workspace_file(
            uid,
            mapping.agentscope_agent_id,
            mapping.agentscope_session_id,
            virtual_to_workspace_path(virtual_path),
            max_bytes=max_bytes,
        )
    except AgentScopeServiceError as exc:
        if exc.status_code == 404:
            raise FileNotFoundError(virtual_path) from exc
        raise


async def list_artifacts(db, *, uid: str, thread_id: str) -> list[str]:
    """读取 present_artifacts 持久化的交付物清单。"""
    context = await resolve_thread_workspace(db, uid=uid, thread_id=thread_id)
    if context is None:
        return []
    client, mapping = context
    try:
        raw = await client.read_workspace_file(
            uid,
            mapping.agentscope_agent_id,
            mapping.agentscope_session_id,
            str(ARTIFACT_MANIFEST),
            max_bytes=256 * 1024,
        )
    except AgentScopeServiceError as exc:
        if exc.status_code == 404:
            return []
        raise
    payload = json.loads(raw)
    return [workspace_to_virtual_path(path) for path in payload.get("filepaths", [])]
