"""AgentScope workspace 附件服务单元测试。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from yuxi.agentscope.workspace_files import (
    MAX_WORKSPACE_UPLOAD_BYTES,
    delete_workspace_output,
    store_workspace_upload,
)

pytestmark = pytest.mark.unit


async def test_store_workspace_upload_writes_only_uploads_directory():
    """合法附件写入解析出的隔离 workspace。"""
    backend = SimpleNamespace(write_file=AsyncMock())
    service = SimpleNamespace(resolve=AsyncMock(return_value=SimpleNamespace(get_backend=Mock(return_value=backend))))

    result = await store_workspace_upload(
        service,
        user_id="u",
        agent_id="a",
        session_id="s",
        destination="/workspace/uploads/a.txt",
        data=b"ok",
    )

    assert result == {"path": "/workspace/uploads/a.txt", "size": 2}
    service.resolve.assert_awaited_once_with("u", "a", "s")
    backend.write_file.assert_awaited_once_with("/workspace/uploads/a.txt", b"ok")


@pytest.mark.parametrize(
    "destination",
    ["relative.txt", "/workspace/uploads/../escape.txt", "/workspace/outputs/a.txt"],
)
async def test_store_workspace_upload_rejects_out_of_scope_paths(destination):
    """越界目标在解析 workspace 前即拒绝。"""
    service = SimpleNamespace(resolve=AsyncMock())

    with pytest.raises(ValueError, match="uploads"):
        await store_workspace_upload(
            service,
            user_id="u",
            agent_id="a",
            session_id="s",
            destination=destination,
            data=b"blocked",
        )
    service.resolve.assert_not_awaited()


async def test_store_workspace_upload_rejects_oversized_payload():
    """超限附件在 workspace 解析前拒绝。"""
    service = SimpleNamespace(resolve=AsyncMock())

    with pytest.raises(OverflowError, match="25 MB"):
        await store_workspace_upload(
            service,
            user_id="u",
            agent_id="a",
            session_id="s",
            destination="/workspace/uploads/a.bin",
            data=b"x" * (MAX_WORKSPACE_UPLOAD_BYTES + 1),
        )
    service.resolve.assert_not_awaited()


async def test_delete_workspace_output_deletes_existing_descendant():
    """合法产出路径通过解析出的隔离 workspace 删除。"""
    backend = SimpleNamespace(
        stat=AsyncMock(return_value=SimpleNamespace()),
        delete_path=AsyncMock(),
    )
    service = SimpleNamespace(resolve=AsyncMock(return_value=SimpleNamespace(get_backend=Mock(return_value=backend))))

    result = await delete_workspace_output(
        service,
        user_id="u",
        agent_id="a",
        session_id="s",
        path="/workspace/outputs/report.md",
    )

    assert result == {"success": True, "path": "/workspace/outputs/report.md"}
    backend.delete_path.assert_awaited_once_with("/workspace/outputs/report.md")


@pytest.mark.parametrize(
    "path",
    [
        "relative.md",
        "/workspace/outputs",
        "/workspace/outputs/../uploads/private.txt",
        "/workspace/uploads/private.txt",
    ],
)
async def test_delete_workspace_output_rejects_root_and_out_of_scope_paths(path):
    """根目录、相对路径和任何越界路径在解析 workspace 前即拒绝。"""
    service = SimpleNamespace(resolve=AsyncMock())

    with pytest.raises(ValueError, match="outputs"):
        await delete_workspace_output(
            service,
            user_id="u",
            agent_id="a",
            session_id="s",
            path=path,
        )

    service.resolve.assert_not_awaited()
