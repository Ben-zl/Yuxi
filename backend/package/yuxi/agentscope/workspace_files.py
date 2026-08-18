"""Yuxi 授权附件写入 AgentScope workspace 的服务用例。"""

from pathlib import PurePosixPath

MAX_WORKSPACE_UPLOAD_BYTES = 25 * 1024 * 1024
WORKSPACE_OUTPUT_ROOT = PurePosixPath("/workspace/outputs")


async def store_workspace_upload(
    workspace_service,
    *,
    user_id: str,
    agent_id: str,
    session_id: str,
    destination: str,
    data: bytes,
) -> dict:
    """校验附件目标与大小后写入对应隔离 workspace。"""
    path = PurePosixPath(destination)
    if not path.is_absolute() or path.parent != PurePosixPath("/workspace/uploads") or path.name in {"", ".", ".."}:
        raise ValueError("附件目标必须位于 /workspace/uploads")
    if len(data) > MAX_WORKSPACE_UPLOAD_BYTES:
        raise OverflowError("附件超过 25 MB")

    workspace = await workspace_service.resolve(user_id, agent_id, session_id)
    await workspace.get_backend().write_file(str(path), data)
    return {"path": str(path), "size": len(data)}


async def delete_workspace_output(
    workspace_service,
    *,
    user_id: str,
    agent_id: str,
    session_id: str,
    path: str,
) -> dict:
    """删除指定会话的产出文件，禁止删除产出根目录及越界路径。"""
    target = PurePosixPath(path)
    if (
        not target.is_absolute()
        or ".." in target.parts
        or target == WORKSPACE_OUTPUT_ROOT
        or not target.is_relative_to(WORKSPACE_OUTPUT_ROOT)
    ):
        raise ValueError("只能删除 outputs 目录中的文件或子目录")

    workspace = await workspace_service.resolve(user_id, agent_id, session_id)
    backend = workspace.get_backend()
    if await backend.stat(str(target)) is None:
        raise FileNotFoundError(str(target))
    await backend.delete_path(str(target))
    return {"success": True, "path": str(target)}
