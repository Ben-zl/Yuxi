from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from yuxi.agents.backends.sandbox import paths as sandbox_paths
from yuxi.agentscope.client import AgentScopeServiceClient
from yuxi.repositories import agentscope_thread_sessions
from yuxi.services import viewer_filesystem_service as svc
from yuxi.services import workspace_service


def _patch_mapped_agentscope_thread(monkeypatch) -> None:
    """把 Viewer 线程固定映射到一个 AgentScope Session。"""

    async def fake_resolve_viewer_state(**kwargs):
        return None, []

    async def fake_get_thread_session(db, *, uid: str, thread_id: str):
        assert uid == "user-1"
        assert thread_id == "thread-1"
        return SimpleNamespace(
            agentscope_agent_id="agent-1",
            agentscope_session_id="session-1",
        )

    monkeypatch.setattr(svc, "_resolve_viewer_state", fake_resolve_viewer_state)
    monkeypatch.setattr(agentscope_thread_sessions, "get_thread_session", fake_get_thread_session)


def test_resolve_local_user_data_path_blocks_upload_symlink_escape(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sandbox_paths.conf, "save_dir", str(tmp_path))
    thread_id = f"symlink-{uuid4().hex}"
    uid = "user-1"
    sandbox_paths.ensure_thread_dirs(thread_id, uid)

    outside_file = tmp_path / "outside.txt"
    outside_file.write_text("outside", encoding="utf-8")
    escape_link = sandbox_paths.sandbox_uploads_dir(thread_id) / "escape.txt"
    escape_link.symlink_to(outside_file)

    with pytest.raises(HTTPException) as exc_info:
        svc._resolve_local_user_data_path(thread_id, uid, "/home/gem/user-data/uploads/escape.txt")

    assert exc_info.value.status_code == 403


def test_list_local_entries_skips_symlink_escape(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sandbox_paths.conf, "save_dir", str(tmp_path))
    thread_id = f"symlink-{uuid4().hex}"
    uid = "user-1"
    sandbox_paths.ensure_thread_dirs(thread_id, uid)

    uploads_dir = sandbox_paths.sandbox_uploads_dir(thread_id)
    (uploads_dir / "safe.txt").write_text("safe", encoding="utf-8")
    outside_file = tmp_path / "outside.txt"
    outside_file.write_text("outside", encoding="utf-8")
    (uploads_dir / "escape.txt").symlink_to(outside_file)

    entries = svc._list_local_entries(thread_id, uid, uploads_dir)

    assert {entry["path"] for entry in entries} == {"/home/gem/user-data/uploads/safe.txt"}


def test_agentscope_output_path_rejects_traversal() -> None:
    """Viewer 虚拟路径不能借助上级目录越出 AgentScope outputs。"""
    with pytest.raises(ValueError, match="上级目录"):
        svc._agentscope_output_path("/home/gem/user-data/outputs/../skills/private.txt")


@pytest.mark.asyncio
async def test_viewer_outputs_use_mapped_agentscope_session(tmp_path: Path, monkeypatch) -> None:
    """AgentScope 线程的产出目录必须读取对应 Session workspace。"""
    monkeypatch.setattr(sandbox_paths.conf, "save_dir", str(tmp_path))
    sandbox_paths.ensure_thread_dirs("thread-1", "user-1")
    _patch_mapped_agentscope_thread(monkeypatch)

    async def fake_list_workspace_directory(self, uid, agent_id, session_id, path):
        assert (uid, agent_id, session_id, path) == (
            "user-1",
            "agent-1",
            "session-1",
            "/workspace/outputs",
        )
        return {
            "path": path,
            "entries": [
                {
                    "name": "report.md",
                    "is_dir": False,
                    "size_bytes": 42,
                    "updated_at": 1_787_000_000.0,
                }
            ],
        }

    monkeypatch.setattr(
        AgentScopeServiceClient,
        "list_workspace_directory",
        fake_list_workspace_directory,
        raising=False,
    )

    result = await svc.list_viewer_filesystem_tree(
        thread_id="thread-1",
        path="/home/gem/user-data/outputs",
        current_user=SimpleNamespace(uid="user-1"),
        db=None,
    )

    assert result["entries"] == [
        {
            "path": "/home/gem/user-data/outputs/report.md",
            "name": "report.md",
            "is_dir": False,
            "size": 42,
            "modified_at": "2026-08-17T20:53:20+00:00",
        }
    ]


@pytest.mark.asyncio
async def test_viewer_output_preview_uses_mapped_agentscope_session(monkeypatch) -> None:
    """AgentScope 产出文件预览必须读取映射 Session。"""
    _patch_mapped_agentscope_thread(monkeypatch)

    async def fake_read_workspace_file(self, uid, agent_id, session_id, path, **kwargs):
        assert (uid, agent_id, session_id, path) == (
            "user-1",
            "agent-1",
            "session-1",
            "/workspace/outputs/report.md",
        )
        return b"# AgentScope report"

    monkeypatch.setattr(AgentScopeServiceClient, "read_workspace_file", fake_read_workspace_file)

    result = await svc.read_viewer_file_content(
        thread_id="thread-1",
        path="/home/gem/user-data/outputs/report.md",
        current_user=SimpleNamespace(uid="user-1"),
        db=None,
    )

    assert result["preview_type"] == "markdown"
    assert result["content"] == "# AgentScope report"


@pytest.mark.asyncio
async def test_viewer_output_download_uses_mapped_agentscope_session(monkeypatch) -> None:
    """AgentScope 产出文件下载必须返回映射 Session 中的原始字节。"""
    _patch_mapped_agentscope_thread(monkeypatch)

    async def fake_read_workspace_file(self, uid, agent_id, session_id, path, **kwargs):
        assert kwargs == {"max_bytes": 100 * 1024 * 1024}
        return b"download content"

    monkeypatch.setattr(AgentScopeServiceClient, "read_workspace_file", fake_read_workspace_file)

    response = await svc.download_viewer_file(
        thread_id="thread-1",
        path="/home/gem/user-data/outputs/report.md",
        current_user=SimpleNamespace(uid="user-1"),
        db=None,
    )
    body = b""
    async for chunk in response.body_iterator:
        body += chunk

    assert response.media_type == "text/markdown"
    assert "report.md" in response.headers["content-disposition"]
    assert body == b"download content"


@pytest.mark.asyncio
async def test_viewer_output_delete_uses_mapped_agentscope_session(monkeypatch) -> None:
    """AgentScope 产出文件删除必须派发到映射 Session。"""
    _patch_mapped_agentscope_thread(monkeypatch)
    calls = []

    async def fake_delete_workspace_output(self, uid, agent_id, session_id, path):
        calls.append((uid, agent_id, session_id, path))

    monkeypatch.setattr(AgentScopeServiceClient, "delete_workspace_output", fake_delete_workspace_output)

    result = await svc.delete_viewer_file(
        thread_id="thread-1",
        path="/home/gem/user-data/outputs/report.md",
        current_user=SimpleNamespace(uid="user-1"),
        db=None,
    )

    assert result == {"success": True, "path": "/home/gem/user-data/outputs/report.md"}
    assert calls == [
        (
            "user-1",
            "agent-1",
            "session-1",
            "/workspace/outputs/report.md",
        )
    ]


@pytest.mark.asyncio
async def test_read_viewer_workspace_office_file_returns_pdf_preview(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(sandbox_paths.conf, "save_dir", str(tmp_path))
    thread_id = "thread-1"
    uid = "user-1"
    user = SimpleNamespace(uid=uid)
    sandbox_paths.ensure_thread_dirs(thread_id, uid)
    target = sandbox_paths.sandbox_workspace_dir(thread_id, uid) / "slides.pptx"
    target.write_bytes(b"presentation")

    async def fake_resolve_viewer_state(**kwargs):
        return None, []

    async def fake_convert(filename: str, content: bytes) -> bytes:
        assert filename == "slides.pptx"
        assert content == b"presentation"
        return b"%PDF-1.4\npreview"

    monkeypatch.setattr(svc, "_resolve_viewer_state", fake_resolve_viewer_state)
    monkeypatch.setattr(workspace_service, "convert_office_to_pdf", fake_convert)

    response = await svc.read_viewer_file_content(
        thread_id=thread_id,
        path="/home/gem/user-data/workspace/slides.pptx",
        current_user=user,
        db=None,
    )
    body = b""
    async for chunk in response.body_iterator:
        body += chunk

    assert response.media_type == "application/pdf"
    assert response.headers["x-yuxi-preview-type"] == "pdf"
    assert body == b"%PDF-1.4\npreview"
