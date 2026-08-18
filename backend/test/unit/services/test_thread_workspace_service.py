"""AgentScope 线程用户文件协议测试。"""

from types import SimpleNamespace

import pytest

from yuxi.services import thread_workspace_service as service
from yuxi.agentscope.client import WorkspaceFileListing


def test_workspace_virtual_paths_only_allow_uploads_and_outputs():
    assert service.virtual_to_workspace_path("/home/gem/user-data/outputs/report.md") == "/workspace/outputs/report.md"
    assert service.workspace_to_virtual_path("/workspace/uploads/input.txt") == "/home/gem/user-data/uploads/input.txt"

    for path in (
        "/home/gem/user-data/outputs/../skills/private.md",
        "/home/gem/user-data/workspace/private.md",
        "/workspace/skills/private.md",
    ):
        with pytest.raises(ValueError):
            if path.startswith("/workspace"):
                service.workspace_to_virtual_path(path)
            else:
                service.virtual_to_workspace_path(path)


@pytest.mark.asyncio
async def test_list_visible_files_never_scans_internal_directories(monkeypatch):
    roots = []

    class Client:
        async def list_workspace_files(self, uid, agent_id, session_id, *, root, max_entries=500):
            roots.append(root)
            return WorkspaceFileListing(
                items=[
                    {"path": f"{root}/nested", "is_dir": True},
                    {"path": f"{root}/nested/file.txt", "is_dir": False, "size_bytes": 4},
                ],
                truncated=root.endswith("outputs"),
            )

    async def resolve(*_args, **_kwargs):
        return Client(), SimpleNamespace(agentscope_agent_id="a1", agentscope_session_id="s1")

    monkeypatch.setattr(service, "resolve_thread_workspace", resolve)
    result = await service.list_visible_files(None, uid="u1", thread_id="t1")

    assert roots == ["/workspace/uploads", "/workspace/outputs"]
    assert [item["path"] for item in result.items] == [
        "/home/gem/user-data/uploads/nested",
        "/home/gem/user-data/uploads/nested/file.txt",
        "/home/gem/user-data/outputs/nested",
        "/home/gem/user-data/outputs/nested/file.txt",
    ]
    assert result.truncated is True


@pytest.mark.asyncio
async def test_artifact_manifest_maps_only_outputs(monkeypatch):
    class Client:
        async def read_workspace_file(self, *_args, **_kwargs):
            return b'{"filepaths":["/workspace/outputs/report.md"]}'

    async def resolve(*_args, **_kwargs):
        return Client(), SimpleNamespace(agentscope_agent_id="a1", agentscope_session_id="s1")

    monkeypatch.setattr(service, "resolve_thread_workspace", resolve)
    assert await service.list_artifacts(None, uid="u1", thread_id="t1") == ["/home/gem/user-data/outputs/report.md"]
