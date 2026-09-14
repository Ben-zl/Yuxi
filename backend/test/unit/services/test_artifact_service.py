from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

import yuxi.services.artifact_service as svc
from server.routers.chat_router import chat
from server.utils.auth_middleware import get_db, get_required_user
from yuxi.agents.backends.paths import workspace_scope_from_runtime_path
from yuxi.workspace.errors import FileTransferLimitError
from yuxi.services.workdir_service import AuthorizedWorkdir
from yuxi.workspace.workdir import Workdir


class _Workspace:
    def __init__(self, skill_root: Path):
        self._write_lock = threading.Lock()
        self.files = {
            "/projects/11111111-1111-4111-8111-111111111111/report.md": b"one\ntwo\n",
            "/notes.txt": b"private",
        }
        self.directories = {"/", "/exports", "/exports/reports"}
        self.skill_root = skill_root
        skill_root.mkdir()
        (skill_root / "SKILL.md").write_bytes(b"skill")

    def download_authorized_file_to_path(self, path, target, max_bytes):
        content = self.files.get(path)
        if content is None:
            raise FileNotFoundError(path)
        assert len(content) <= max_bytes
        Path(target).write_bytes(content)
        return len(content)

    def stat_authorized_path(self, path, *, root):
        assert root == "/"
        if path in self.directories:
            return {"is_dir": True}
        if path in self.files:
            return {"is_dir": False}
        if any(path.startswith(f"{file_path}/") for file_path in self.files):
            raise NotADirectoryError(path)
        raise FileNotFoundError(path)

    def upload_authorized_file_from_path(self, path, source, *, overwrite=True, create_parents=True):
        parent = str(Path(path).parent)
        if parent not in self.directories:
            if not create_parents:
                raise FileNotFoundError(parent)
            self.directories.add(parent)
        content = Path(source).read_bytes()
        with self._write_lock:
            if not overwrite and path in self.files:
                raise FileExistsError(path)
            self.files[path] = content

    def expected_bytes(self, runtime_path: str) -> bytes:
        if runtime_path.startswith("/home/gem/skills/reporter/"):
            return (self.skill_root / runtime_path.rsplit("/", 1)[-1]).read_bytes()
        return self.files[workspace_scope_from_runtime_path(runtime_path)]

    def add_runtime_file(self, runtime_path: str, content: bytes) -> None:
        self.files[workspace_scope_from_runtime_path(runtime_path)] = content


@pytest.fixture
def live_files(monkeypatch, tmp_path):
    backend = _Workspace(tmp_path / "reporter")
    binding = AuthorizedWorkdir(
        conversation_id=1,
        thread_id="thread-1",
        uid="user-1",
        workdir=Workdir("projects/11111111-1111-4111-8111-111111111111", backend),
        project_id="11111111-1111-4111-8111-111111111111",
        directory_mode="managed",
    )

    async def resolve(**kwargs):
        assert kwargs["uid"] == "user-1"
        return binding

    monkeypatch.setattr(svc, "resolve_authorized_workdir", resolve)
    monkeypatch.setattr(svc, "lock_project_workdir_changes", lambda **_kwargs: _async_value(None))
    monkeypatch.setattr(svc, "resolve_thread_workspace", lambda *_args, **_kwargs: _async_value(None))
    monkeypatch.setattr(
        svc,
        "UserRepository",
        lambda _db: type(
            "Repo",
            (),
            {"get_by_uid": lambda self, uid: _async_value(type("User", (), {"uid": uid, "is_deleted": False})())},
        )(),
    )
    monkeypatch.setattr(
        svc,
        "list_accessible_skills",
        lambda _db, _user: _async_value([type("Skill", (), {"slug": "reporter", "source_dir": backend.skill_root})()]),
    )
    return backend


async def _async_value(value):
    return value


@pytest.fixture
def artifact_http_client(live_files):
    app = FastAPI()
    app.include_router(chat, prefix="/api")
    app.dependency_overrides[get_db] = lambda: object()
    app.dependency_overrides[get_required_user] = lambda: type("User", (), {"uid": "user-1"})()
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_save_artifact_http_uses_current_workdir_and_selected_destination(live_files, artifact_http_client):
    """正式 HTTP 保存入口使用当前 Workdir，并保留用户选择的目录。"""
    async with artifact_http_client as client:
        response = await client.post(
            "/api/chat/thread/thread-1/artifacts/save",
            json={
                "path": "/home/gem/user-data/projects/11111111-1111-4111-8111-111111111111/report.md",
                "destination_path": "/exports/reports",
            },
        )
        assert response.status_code == 200, response.text
        saved_path = response.json()["saved_path"]
        assert saved_path == "/home/gem/user-data/exports/reports/report.md"
        assert response.json()["saved_artifact_url"] == "/api/workspace/download?path=/exports/reports/report.md"
        assert live_files.expected_bytes(saved_path) == b"one\ntwo\n"


@pytest.mark.asyncio
async def test_save_artifact_http_rejects_other_project_of_same_user(live_files, artifact_http_client):
    """同一 UID 的其他 Project 文件不能经正式 HTTP 保存入口复制。"""
    other_path = "/home/gem/user-data/projects/22222222-2222-4222-8222-222222222222/secret.txt"
    live_files.add_runtime_file(other_path, b"other project secret")
    async with artifact_http_client as client:
        response = await client.post(
            "/api/chat/thread/thread-1/artifacts/save",
            json={"path": other_path},
        )
    assert response.status_code == 403
    assert "/saved_artifacts/secret.txt" not in live_files.files


@pytest.mark.asyncio
async def test_save_artifact_http_rejects_other_project_destination(live_files, artifact_http_client, monkeypatch):
    """当前 Conversation 不能把交付物写入同 UID 的其他 Project。"""
    other_dir = "/projects/22222222-2222-4222-8222-222222222222"
    live_files.directories.add(other_dir)
    binding = AuthorizedWorkdir(
        conversation_id=1,
        thread_id="thread-1",
        uid="user-1",
        workdir=Workdir("projects/11111111-1111-4111-8111-111111111111", live_files),
        project_id="11111111-1111-4111-8111-111111111111",
        directory_mode="managed",
        project_workdir_paths=(
            "projects/11111111-1111-4111-8111-111111111111",
            "projects/22222222-2222-4222-8222-222222222222",
        ),
    )
    monkeypatch.setattr(svc, "resolve_authorized_workdir", lambda **_kwargs: _async_value(binding))
    async with artifact_http_client as client:
        response = await client.post(
            "/api/chat/thread/thread-1/artifacts/save",
            json={
                "path": "/home/gem/user-data/projects/11111111-1111-4111-8111-111111111111/report.md",
                "destination_path": other_dir,
            },
        )
    assert response.status_code == 403
    assert f"{other_dir}/report.md" not in live_files.files


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["get", "post"])
async def test_artifact_http_rejects_session_root_owned_by_other_project(
    live_files, artifact_http_client, monkeypatch, method
):
    """Session 根路径与其他 linked Project 冲突时优先拒绝。"""
    from yuxi.services import thread_files_service

    original_resolve = svc.resolve_authorized_workdir

    async def resolve(**kwargs):
        access = await original_resolve(**kwargs)
        return AuthorizedWorkdir(
            conversation_id=access.conversation_id,
            thread_id=access.thread_id,
            uid=access.uid,
            workdir=access.workdir,
            project_id=access.project_id,
            directory_mode=access.directory_mode,
            project_workdir_paths=("projects/11111111-1111-4111-8111-111111111111", "uploads"),
        )

    async def session_artifact(**_kwargs):
        return thread_files_service.InMemoryArtifact(name="report.md", content=b"session bytes")

    monkeypatch.setattr(svc, "resolve_authorized_workdir", resolve)
    monkeypatch.setattr(svc, "resolve_thread_workspace", lambda *_args, **_kwargs: _async_value((object(), object())))
    monkeypatch.setattr(thread_files_service, "resolve_thread_artifact_by_owner", session_artifact)
    url = "/api/chat/thread/thread-1/artifacts/"
    path = "/home/gem/user-data/uploads/report.md"
    async with artifact_http_client as client:
        if method == "get":
            response = await client.get(f"{url}{path.lstrip('/')}")
        else:
            response = await client.post(f"{url}save", json={"path": path})
    assert response.status_code == 403
    assert "/saved_artifacts/report.md" not in live_files.files


@pytest.mark.asyncio
async def test_artifact_http_does_not_read_unbound_personal_directory(live_files, artifact_http_client):
    """未绑定的个人目录不属于当前线程的 Artifact 来源。"""
    path = "/home/gem/user-data/private/ledger.csv"
    live_files.add_runtime_file(path, b"private")
    async with artifact_http_client as client:
        response = await client.get(f"/api/chat/thread/thread-1/artifacts/{path.lstrip('/')}")
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_artifact_allows_project_user_data_and_authorized_skills(live_files):
    for path in (
        "/home/gem/user-data/projects/11111111-1111-4111-8111-111111111111/report.md",
        "/home/gem/user-data/notes.txt",
        "/home/gem/skills/reporter/SKILL.md",
    ):
        response = await svc.resolve_thread_artifact_view(
            thread_id="thread-1", current_uid="user-1", db=object(), path=path
        )
        assert Path(response.path).read_bytes() == live_files.expected_bytes(path)
        await response.background()


@pytest.mark.asyncio
async def test_artifact_preview_uses_shared_file_renderer(live_files, monkeypatch):
    path = "/home/gem/user-data/projects/11111111-1111-4111-8111-111111111111/report.docx"
    live_files.add_runtime_file(path, b"docx bytes")
    captured = {}
    sentinel = {"preview_type": "pdf", "supported": True}

    async def render_preview(file_path, raw_content, *, office_cache_key):
        captured.update(
            path=file_path,
            raw_content=raw_content,
            office_cache_key=office_cache_key,
        )
        return sentinel

    monkeypatch.setattr(svc, "render_file_preview", render_preview)

    response = await svc.resolve_thread_artifact_view(
        thread_id="thread-1",
        current_uid="user-1",
        db=object(),
        path=path,
        preview=True,
    )

    assert response is sentinel
    assert captured == {
        "path": path,
        "raw_content": b"docx bytes",
        "office_cache_key": f"artifact:user-1:{path}",
    }


@pytest.mark.asyncio
async def test_agentscope_artifact_preview_reads_the_mapped_session_file(live_files, monkeypatch):
    from yuxi.services import thread_files_service

    monkeypatch.setattr(svc, "resolve_thread_workspace", lambda *_args, **_kwargs: _async_value((object(), object())))

    async def resolve_from_agentscope(**_kwargs):
        return thread_files_service.InMemoryArtifact(name="report.html", content=b"<h1>ok</h1>")

    monkeypatch.setattr(thread_files_service, "resolve_thread_artifact_by_owner", resolve_from_agentscope)
    captured = {}

    async def render_preview(path, raw_content, *, office_cache_key):
        captured.update(path=path, raw_content=raw_content, office_cache_key=office_cache_key)
        return {"preview_type": "html", "supported": True}

    monkeypatch.setattr(svc, "render_file_preview", render_preview)

    response = await svc.resolve_thread_artifact_view(
        thread_id="thread-1",
        current_uid="user-1",
        db=object(),
        path="/home/gem/user-data/outputs/report.html",
        preview=True,
    )

    assert response == {"preview_type": "html", "supported": True}
    assert captured == {
        "path": "/home/gem/user-data/outputs/report.html",
        "raw_content": b"<h1>ok</h1>",
        "office_cache_key": "artifact:user-1:/home/gem/user-data/outputs/report.html",
    }


@pytest.mark.asyncio
async def test_save_agentscope_session_output_uses_current_session_owner(live_files, monkeypatch):
    """Session 产物可保存到用户目录，不经错误的本地 Workdir 来源。"""
    from yuxi.services import thread_files_service

    monkeypatch.setattr(svc, "resolve_thread_workspace", lambda *_args, **_kwargs: _async_value((object(), object())))

    async def resolve_from_agentscope(**_kwargs):
        return thread_files_service.InMemoryArtifact(name="report.md", content=b"session output")

    monkeypatch.setattr(thread_files_service, "resolve_thread_artifact_by_owner", resolve_from_agentscope)
    result = await svc.save_thread_artifact_to_workspace_view(
        thread_id="thread-1",
        current_uid="user-1",
        db=object(),
        path="/home/gem/user-data/outputs/report.md",
    )
    assert result["saved_path"] == "/home/gem/user-data/saved_artifacts/report.md"
    assert live_files.expected_bytes(result["saved_path"]) == b"session output"


@pytest.mark.asyncio
async def test_project_artifact_stays_owned_by_workdir_after_session_mapping_exists(live_files, monkeypatch):
    """Session 建立后，Project Workdir 附件仍必须从原 Owner 下载。"""
    from yuxi.services import thread_files_service

    path = "/home/gem/user-data/projects/11111111-1111-4111-8111-111111111111/report.md"
    monkeypatch.setattr(svc, "resolve_thread_workspace", lambda *_args, **_kwargs: _async_value((object(), object())))

    async def reject_session_owner(**_kwargs):
        raise AssertionError("Project Workdir artifact must not be routed to AgentScope Session")

    monkeypatch.setattr(thread_files_service, "resolve_thread_artifact_by_owner", reject_session_owner)

    response = await svc.resolve_thread_artifact_view(
        thread_id="thread-1",
        current_uid="user-1",
        db=object(),
        path=path,
    )

    assert Path(response.path).read_bytes() == live_files.expected_bytes(path)
    await response.background()


@pytest.mark.asyncio
@pytest.mark.parametrize("root", ["uploads", "outputs"])
async def test_root_named_linked_workdir_artifact_stays_owned_after_session_mapping(live_files, monkeypatch, root):
    """合法的根级 linked Workdir 优先于同名的 Session 目录。"""
    from yuxi.services import thread_files_service

    path = f"/home/gem/user-data/{root}/report.md"
    live_files.add_runtime_file(path, b"linked workdir")
    binding = AuthorizedWorkdir(
        conversation_id=1,
        thread_id="thread-1",
        uid="user-1",
        workdir=Workdir(root, live_files),
        project_id="linked-project",
        directory_mode="linked",
    )
    monkeypatch.setattr(svc, "resolve_authorized_workdir", lambda **_kwargs: _async_value(binding))
    monkeypatch.setattr(svc, "resolve_thread_workspace", lambda *_args, **_kwargs: _async_value((object(), object())))

    async def reject_session_owner(**_kwargs):
        raise AssertionError("linked Workdir artifact must not be routed to AgentScope Session")

    monkeypatch.setattr(thread_files_service, "resolve_thread_artifact_by_owner", reject_session_owner)

    response = await svc.resolve_thread_artifact_view(
        thread_id="thread-1", current_uid="user-1", db=object(), path=path
    )
    assert Path(response.path).read_bytes() == b"linked workdir"
    await response.background()


@pytest.mark.asyncio
async def test_artifact_preview_reports_oversized_file_without_rendering(live_files, monkeypatch):
    path = "/home/gem/user-data/projects/11111111-1111-4111-8111-111111111111/report.docx"

    def reject_large_file(_path, _target, max_bytes):
        assert max_bytes == svc.MAX_BINARY_PREVIEW_SIZE_BYTES
        raise FileTransferLimitError("file exceeds transfer limit")

    async def reject_render(*_args, **_kwargs):
        raise AssertionError("oversized preview must not reach the renderer")

    monkeypatch.setattr(live_files, "download_authorized_file_to_path", reject_large_file)
    monkeypatch.setattr(svc, "render_file_preview", reject_render)

    response = await svc.resolve_thread_artifact_view(
        thread_id="thread-1",
        current_uid="user-1",
        db=object(),
        path=path,
        preview=True,
    )

    assert response == svc.preview_too_large().payload()


@pytest.mark.asyncio
@pytest.mark.parametrize("file_name", ["报告.txt", 'quoted"name.txt', "line\nbreak.txt"])
async def test_artifact_download_encodes_untrusted_posix_filename(live_files, file_name):
    path = f"/home/gem/user-data/projects/11111111-1111-4111-8111-111111111111/{file_name}"
    live_files.add_runtime_file(path, b"safe")

    response = await svc.resolve_thread_artifact_view(
        thread_id="thread-1",
        current_uid="user-1",
        db=object(),
        path=path,
        download=True,
    )

    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    assert "\r" not in disposition and "\n" not in disposition
    assert file_name not in disposition
    await response.background()


@pytest.mark.asyncio
async def test_artifact_rejects_other_project(live_files):
    path = "/home/gem/user-data/projects/other/secret.txt"
    live_files.add_runtime_file(path, b"other project secret")
    with pytest.raises(HTTPException) as exc:
        await svc.resolve_thread_artifact_view(
            thread_id="thread-1",
            current_uid="user-1",
            db=object(),
            path=path,
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_save_artifact_rejects_existing_other_project(live_files):
    path = "/home/gem/user-data/projects/other/secret.txt"
    live_files.add_runtime_file(path, b"other project secret")
    with pytest.raises(HTTPException) as exc:
        await svc.save_thread_artifact_to_workspace_view(
            thread_id="thread-1",
            current_uid="user-1",
            db=object(),
            path=path,
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_saved_artifact_compatibility_rejects_other_linked_project(live_files, monkeypatch):
    """兼容目录不是跨 Project 的通用读取权限。"""
    path = "/home/gem/user-data/saved_artifacts/client/secret.txt"
    live_files.add_runtime_file(path, b"other linked project")
    original_resolve = svc.resolve_authorized_workdir

    async def resolve(**kwargs):
        access = await original_resolve(**kwargs)
        return AuthorizedWorkdir(
            conversation_id=access.conversation_id,
            thread_id=access.thread_id,
            uid=access.uid,
            workdir=access.workdir,
            project_id=access.project_id,
            directory_mode=access.directory_mode,
            project_workdir_paths=("saved_artifacts/client",),
        )

    monkeypatch.setattr(svc, "resolve_authorized_workdir", resolve)
    with pytest.raises(HTTPException) as exc:
        await svc.resolve_thread_artifact_view(thread_id="thread-1", current_uid="user-1", db=object(), path=path)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("root", ["uploads", "outputs"])
async def test_save_root_named_linked_workdir_ignores_session_owner(live_files, monkeypatch, root):
    """同名 linked Workdir 的保存来源仍是当前 Project。"""
    from yuxi.services import thread_files_service

    path = f"/home/gem/user-data/{root}/report.md"
    live_files.add_runtime_file(path, b"linked workdir")
    binding = AuthorizedWorkdir(
        conversation_id=1,
        thread_id="thread-1",
        uid="user-1",
        workdir=Workdir(root, live_files),
        project_id="linked-project",
        directory_mode="linked",
    )
    monkeypatch.setattr(svc, "resolve_authorized_workdir", lambda **_kwargs: _async_value(binding))
    monkeypatch.setattr(svc, "resolve_thread_workspace", lambda *_args, **_kwargs: _async_value((object(), object())))

    async def reject_session_owner(**_kwargs):
        raise AssertionError("linked Workdir save must not read AgentScope Session")

    monkeypatch.setattr(thread_files_service, "resolve_thread_artifact_by_owner", reject_session_owner)
    result = await svc.save_thread_artifact_to_workspace_view(
        thread_id="thread-1", current_uid="user-1", db=object(), path=path
    )
    assert live_files.expected_bytes(result["saved_path"]) == b"linked workdir"


@pytest.mark.asyncio
async def test_artifact_rejects_workdir_viewer_scope(live_files):
    with pytest.raises(HTTPException) as exc:
        await svc.resolve_thread_artifact_view(
            thread_id="thread-1",
            current_uid="user-1",
            db=object(),
            path="/outputs/report.md",
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_artifact_rechecks_current_skill_authorization(live_files, monkeypatch):
    monkeypatch.setattr(svc, "list_accessible_skills", lambda _db, _user: _async_value([]))

    with pytest.raises(HTTPException) as exc:
        await svc.resolve_thread_artifact_view(
            thread_id="thread-1",
            current_uid="user-1",
            db=object(),
            path="/home/gem/skills/reporter/SKILL.md",
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_artifact_transfer_limit_is_not_reported_as_missing(live_files, monkeypatch):
    def reject_large_file(*_args, **_kwargs):
        raise FileTransferLimitError("file exceeds transfer limit")

    monkeypatch.setattr(live_files, "download_authorized_file_to_path", reject_large_file)
    with pytest.raises(HTTPException) as exc:
        await svc.resolve_thread_artifact_view(
            thread_id="thread-1",
            current_uid="user-1",
            db=object(),
            path="/home/gem/user-data/projects/11111111-1111-4111-8111-111111111111/report.md",
        )
    assert exc.value.status_code == 413


@pytest.mark.asyncio
async def test_save_artifact_copies_live_bytes_to_user_data(live_files):
    result = await svc.save_thread_artifact_to_workspace_view(
        thread_id="thread-1",
        current_uid="user-1",
        db=object(),
        path="/home/gem/user-data/projects/11111111-1111-4111-8111-111111111111/report.md",
    )
    assert result["saved_path"] == "/home/gem/user-data/saved_artifacts/report.md"
    assert live_files.expected_bytes(result["saved_path"]) == b"one\ntwo\n"


@pytest.mark.asyncio
async def test_save_artifact_uses_selected_workspace_destination(live_files):
    result = await svc.save_thread_artifact_to_workspace_view(
        thread_id="thread-1",
        current_uid="user-1",
        db=object(),
        path="/home/gem/user-data/projects/11111111-1111-4111-8111-111111111111/report.md",
        destination_path="/exports/reports",
    )
    assert result["saved_path"] == "/home/gem/user-data/exports/reports/report.md"
    assert live_files.expected_bytes(result["saved_path"]) == b"one\ntwo\n"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "destination",
    ["../exports", "/exports/../reports", "/home/gem/user-data/exports", "/home/gem/skills/reporter"],
)
async def test_save_artifact_rejects_unsafe_destination(live_files, destination):
    with pytest.raises(HTTPException) as exc:
        await svc.save_thread_artifact_to_workspace_view(
            thread_id="thread-1",
            current_uid="user-1",
            db=object(),
            path="/home/gem/user-data/projects/11111111-1111-4111-8111-111111111111/report.md",
            destination_path=destination,
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_save_artifact_accepts_explicit_default_destination(live_files):
    result = await svc.save_thread_artifact_to_workspace_view(
        thread_id="thread-1",
        current_uid="user-1",
        db=object(),
        path="/home/gem/user-data/projects/11111111-1111-4111-8111-111111111111/report.md",
        destination_path="/saved_artifacts",
    )
    assert result["saved_path"] == "/home/gem/user-data/saved_artifacts/report.md"
    assert live_files.expected_bytes(result["saved_path"]) == b"one\ntwo\n"


@pytest.mark.asyncio
async def test_save_artifact_rejects_missing_destination(live_files):
    with pytest.raises(HTTPException) as exc:
        await svc.save_thread_artifact_to_workspace_view(
            thread_id="thread-1",
            current_uid="user-1",
            db=object(),
            path="/home/gem/user-data/projects/11111111-1111-4111-8111-111111111111/report.md",
            destination_path="/missing",
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_save_artifact_does_not_recreate_selected_directory_after_validation(live_files, monkeypatch):
    original_stat = live_files.stat_authorized_path

    def remove_after_stat(path, *, root):
        result = original_stat(path, root=root)
        live_files.directories.remove(path)
        return result

    monkeypatch.setattr(live_files, "stat_authorized_path", remove_after_stat)
    with pytest.raises(HTTPException) as exc:
        await svc.save_thread_artifact_to_workspace_view(
            thread_id="thread-1",
            current_uid="user-1",
            db=object(),
            path="/home/gem/user-data/projects/11111111-1111-4111-8111-111111111111/report.md",
            destination_path="/exports/reports",
        )
    assert exc.value.status_code == 404
    assert "/exports/reports" not in live_files.directories


@pytest.mark.asyncio
async def test_save_artifact_rejects_file_in_destination_path(live_files):
    with pytest.raises(HTTPException) as exc:
        await svc.save_thread_artifact_to_workspace_view(
            thread_id="thread-1",
            current_uid="user-1",
            db=object(),
            path="/home/gem/user-data/projects/11111111-1111-4111-8111-111111111111/report.md",
            destination_path="/notes.txt/child",
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_concurrent_artifact_saves_use_distinct_atomic_names(live_files):
    second_source = "/home/gem/user-data/projects/11111111-1111-4111-8111-111111111111/outputs/report.md"
    live_files.add_runtime_file(second_source, b"second")

    first, second = await asyncio.gather(
        svc.save_thread_artifact_to_workspace_view(
            thread_id="thread-1",
            current_uid="user-1",
            db=object(),
            path="/home/gem/user-data/projects/11111111-1111-4111-8111-111111111111/report.md",
        ),
        svc.save_thread_artifact_to_workspace_view(
            thread_id="thread-1",
            current_uid="user-1",
            db=object(),
            path=second_source,
        ),
    )

    assert {first["saved_path"], second["saved_path"]} == {
        "/home/gem/user-data/saved_artifacts/report.md",
        "/home/gem/user-data/saved_artifacts/report (1).md",
    }
    assert set(live_files.expected_bytes(path) for path in (first["saved_path"], second["saved_path"])) == {
        b"one\ntwo\n",
        b"second",
    }
