"""Yuxi 授权附件写入 AgentScope workspace 的服务用例。"""

from pathlib import PurePosixPath

MAX_WORKSPACE_UPLOAD_BYTES = 25 * 1024 * 1024


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
