"""线程 artifact 下载与保存用例。"""

from __future__ import annotations

import asyncio
import contextlib
import os
import tempfile
from pathlib import PurePosixPath
from urllib.parse import quote

from fastapi import HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from starlette.background import BackgroundTask
from yuxi.agents.backends.paths import (
    VIRTUAL_PATH_PREFIX,
    VIRTUAL_SKILLS_PATH,
    is_runtime_path,
    runtime_user_data_path,
    workspace_scope_from_runtime_path,
)
from yuxi.agents.skills.service import ResolvedSkill, list_accessible_skills
from yuxi.repositories.user_repository import UserRepository
from yuxi.services.file_preview import render_file_preview
from yuxi.services.project_service import lock_project_workdir_changes
from yuxi.services.thread_workspace_service import resolve_thread_workspace
from yuxi.services.workdir_service import resolve_authorized_workdir
from yuxi.utils.filepreview import (
    MAX_BINARY_PREVIEW_SIZE_BYTES,
    OfficePreviewConversionError,
    detect_media_type,
    preview_too_large,
)
from yuxi.utils.paths import open_regular_file_fd
from yuxi.workspace.errors import FileTransferLimitError

MAX_ARTIFACT_DOWNLOAD_BYTES = 1024 * 1024 * 1024
MAX_SAVED_ARTIFACT_NAME_ATTEMPTS = 1000
DEFAULT_ARTIFACT_DESTINATION = "/saved_artifacts"


def _is_agentscope_session_artifact_path(path: str) -> bool:
    """仅识别 AgentScope Session 实际拥有的根级 uploads/outputs 路径。"""
    raw = str(path or "").strip()
    target = PurePosixPath(raw if raw.startswith("/") else f"/{raw}")
    if ".." in PurePosixPath(raw).parts:
        return False
    try:
        relative = target.relative_to(PurePosixPath(VIRTUAL_PATH_PREFIX.rstrip("/")))
    except ValueError:
        return False
    return len(relative.parts) > 1 and relative.parts[0] in {"uploads", "outputs"}


def _normalize_artifact_path(workdir_path: str, path: str, *, project_workdir_paths: tuple[str, ...] = ()) -> str:
    raw = str(path or "").strip()
    normalized = str(PurePosixPath(raw if raw.startswith("/") else f"/{raw}"))
    if ".." in PurePosixPath(raw).parts:
        raise HTTPException(status_code=403, detail="access denied")
    user_root = VIRTUAL_PATH_PREFIX.rstrip("/")
    _reject_other_project_path(workdir_path, normalized, project_workdir_paths)
    relative = PurePosixPath(normalized).relative_to(user_root) if normalized.startswith(f"{user_root}/") else None
    allowed = normalized.startswith(f"{workdir_path}/") or normalized.startswith(f"{VIRTUAL_SKILLS_PATH}/")
    if relative is not None:
        allowed = allowed or len(relative.parts) == 1 or normalized.startswith(f"{user_root}/saved_artifacts/")
    if not allowed:
        raise HTTPException(status_code=403, detail="artifact is outside the current user's visible roots")
    return normalized


def _reject_other_project_path(workdir_path: str, normalized: str, project_workdir_paths: tuple[str, ...]) -> None:
    """其他 active Project 优先于 Session 和用户根兼容路径。"""
    user_root = VIRTUAL_PATH_PREFIX.rstrip("/")
    for other in project_workdir_paths:
        other_root = f"{user_root}/{other}"
        if other_root != workdir_path and (normalized == other_root or normalized.startswith(f"{other_root}/")):
            raise HTTPException(status_code=403, detail="artifact access denied")


async def _require_skill_artifact_access(
    *, normalized_path: str, current_uid: str, db
) -> tuple[ResolvedSkill, str] | None:
    skills_prefix = f"{VIRTUAL_SKILLS_PATH}/"
    if not normalized_path.startswith(skills_prefix):
        return None
    slug = normalized_path[len(skills_prefix) :].split("/", 1)[0]
    user = await UserRepository(db).get_by_uid(str(current_uid))
    if user is None or bool(user.is_deleted):
        raise HTTPException(status_code=403, detail="artifact access denied")
    accessible = {skill.slug: skill for skill in await list_accessible_skills(db, user)}
    skill = accessible.get(slug)
    if skill is None:
        raise HTTPException(status_code=403, detail="artifact access denied")
    relative_path = normalized_path[len(skills_prefix) + len(slug) :].lstrip("/")
    if not relative_path:
        raise HTTPException(status_code=400, detail="artifact path is not a regular file")
    return skill, relative_path


def _copy_skill_file_to_path(skill: ResolvedSkill, relative_path: str, target_path: str, max_bytes: int) -> int:
    """从已授权 Skill 真实来源有界复制普通文件。"""
    parts = tuple(PurePosixPath(relative_path).parts)
    if not parts or ".." in parts:
        raise ValueError("invalid skill artifact path")
    target_fd = None
    with open_regular_file_fd(skill.source_dir, parts) as (source_fd, source_stat):
        if source_stat.st_size > max_bytes:
            raise FileTransferLimitError("file exceeds transfer limit")
        try:
            target_fd = os.open(target_path, os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW)
            total = 0
            while chunk := os.read(source_fd, 1024 * 1024):
                total += len(chunk)
                if total > max_bytes:
                    raise FileTransferLimitError("file exceeds transfer limit")
                offset = 0
                while offset < len(chunk):
                    offset += os.write(target_fd, chunk[offset:])
            return total
        finally:
            if target_fd is not None:
                os.close(target_fd)


async def _copy_artifact_to_path(
    access,
    normalized_path: str,
    skill_source,
    target_path: str,
    max_bytes: int = MAX_ARTIFACT_DOWNLOAD_BYTES,
) -> None:
    """把已授权的 Workspace 或 Skill artifact 有界复制到临时文件。"""
    try:
        if skill_source is None:
            await asyncio.to_thread(
                access.workdir.workspace.download_authorized_file_to_path,
                workspace_scope_from_runtime_path(normalized_path),
                target_path,
                max_bytes,
            )
            return
        await asyncio.to_thread(
            _copy_skill_file_to_path,
            skill_source[0],
            skill_source[1],
            target_path,
            max_bytes,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="artifact access denied") from exc
    except IsADirectoryError as exc:
        raise HTTPException(status_code=400, detail="artifact path is not a regular file") from exc
    except FileTransferLimitError as exc:
        raise HTTPException(status_code=413, detail="artifact exceeds transfer limit") from exc
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="artifact not found") from exc


async def _resolve_session_artifact(*, access, thread_id: str, current_uid: str, db, path: str):
    """只在路径不属于当前 Workdir 时读取同线程 Session 的根级产物。"""
    workdir_root = runtime_user_data_path(access.workdir.root_path)
    raw_path = str(path or "").strip()
    normalized = str(PurePosixPath(raw_path if raw_path.startswith("/") else f"/{raw_path}"))
    _reject_other_project_path(workdir_root, normalized, access.project_workdir_paths)
    if (
        not _is_agentscope_session_artifact_path(path)
        or normalized.startswith(f"{workdir_root}/")
        or await resolve_thread_workspace(db, uid=str(current_uid), thread_id=thread_id) is None
    ):
        return None
    from yuxi.services.thread_files_service import resolve_thread_artifact_by_owner

    source = await resolve_thread_artifact_by_owner(
        thread_id=thread_id,
        owner_uid=str(current_uid),
        db=db,
        path=path,
    )
    if not hasattr(source, "content"):
        raise HTTPException(status_code=500, detail="AgentScope artifact source is invalid")
    return source


def _write_session_artifact_to_path(content: bytes, target_path: str) -> None:
    """把已授权 Session 字节写入本次临时文件。"""
    with open(target_path, "wb") as target:
        target.write(content)


async def resolve_thread_artifact_view(
    *,
    thread_id: str,
    current_uid: str,
    db,
    path: str,
    download: bool = False,
    preview: bool = False,
) -> FileResponse | StreamingResponse | dict:
    """把实时授权文件导出为自动清理的 HTTP 文件响应。"""
    await lock_project_workdir_changes(db=db, uid=current_uid)
    access = await resolve_authorized_workdir(thread_id=thread_id, uid=current_uid, db=db)
    workdir_root = runtime_user_data_path(access.workdir.root_path)
    source = await _resolve_session_artifact(
        access=access, thread_id=thread_id, current_uid=current_uid, db=db, path=path
    )
    if source is not None:
        file_name = source.name or PurePosixPath(path).name or "artifact"
        is_preview = preview and not download
        if is_preview:
            return await render_file_preview(
                path,
                source.content,
                office_cache_key=f"artifact:{current_uid}:{path}",
            )
        return StreamingResponse(
            iter([source.content]),
            media_type=detect_media_type(file_name, source.content[: 16 * 1024]),
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(file_name)}"},
        )

    normalized = _normalize_artifact_path(workdir_root, path, project_workdir_paths=access.project_workdir_paths)
    skill_source = await _require_skill_artifact_access(normalized_path=normalized, current_uid=current_uid, db=db)
    is_preview = preview and not download
    descriptor, temp_path = tempfile.mkstemp(prefix="yuxi-artifact-", suffix=PurePosixPath(normalized).suffix)
    os.close(descriptor)
    try:
        await _copy_artifact_to_path(
            access,
            normalized,
            skill_source,
            temp_path,
            MAX_BINARY_PREVIEW_SIZE_BYTES if is_preview else MAX_ARTIFACT_DOWNLOAD_BYTES,
        )
    except HTTPException as exc:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temp_path)
        if is_preview and exc.status_code == 413:
            return preview_too_large().payload()
        raise
    except Exception:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temp_path)
        raise
    file_name = PurePosixPath(normalized).name or "artifact"
    if is_preview:
        try:
            with open(temp_path, "rb") as artifact_file:
                raw_content = artifact_file.read()
            return await render_file_preview(
                normalized,
                raw_content,
                office_cache_key=f"artifact:{current_uid}:{normalized}",
            )
        except OfficePreviewConversionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(temp_path)

    with open(temp_path, "rb") as artifact_file:
        media_type = detect_media_type(file_name, artifact_file.read(16 * 1024))
    return FileResponse(
        temp_path,
        media_type=media_type,
        filename=file_name if download else None,
        content_disposition_type="attachment",
        background=BackgroundTask(os.unlink, temp_path),
    )


async def save_thread_artifact_to_workspace_view(
    *, thread_id: str, current_uid: str, db, path: str, destination_path: str | None = None
) -> dict[str, str]:
    """把可见 artifact 复制到用户选择的工作区目录。"""
    await lock_project_workdir_changes(db=db, uid=current_uid)
    access = await resolve_authorized_workdir(thread_id=thread_id, uid=current_uid, db=db)
    source = await _resolve_session_artifact(
        access=access, thread_id=thread_id, current_uid=current_uid, db=db, path=path
    )
    if source is None:
        normalized = _normalize_artifact_path(
            runtime_user_data_path(access.workdir.root_path), path, project_workdir_paths=access.project_workdir_paths
        )
    else:
        raw_path = str(path or "").strip()
        normalized = str(PurePosixPath(raw_path if raw_path.startswith("/") else f"/{raw_path}"))
    raw_destination = str(destination_path or DEFAULT_ARTIFACT_DESTINATION).strip()
    destination = PurePosixPath(raw_destination)
    if (
        not destination.is_absolute()
        or ".." in PurePosixPath(raw_destination).parts
        or "\\" in raw_destination
        or "://" in raw_destination
        or is_runtime_path(raw_destination)
    ):
        raise HTTPException(status_code=403, detail="invalid artifact destination")
    destination_scope = destination.as_posix()
    file_name = PurePosixPath(normalized).name or "artifact"
    workdir_root = runtime_user_data_path(access.workdir.root_path)
    target_scope = f"{destination_scope.rstrip('/')}/{file_name}"
    target = runtime_user_data_path(target_scope)
    _reject_other_project_path(workdir_root, target, access.project_workdir_paths)
    if any(
        destination_scope == f"/{root}" or destination_scope.startswith(f"/{root}/")
        for root in ("projects", "uploads", "outputs")
    ) and not (target == workdir_root or target.startswith(f"{workdir_root}/")):
        raise HTTPException(status_code=403, detail="artifact destination access denied")
    destination_must_exist = destination_path is not None and destination_scope != DEFAULT_ARTIFACT_DESTINATION
    if destination_must_exist:
        try:
            destination_item = await asyncio.to_thread(
                access.workdir.workspace.stat_authorized_path,
                destination_scope,
                root="/",
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="artifact destination does not exist") from exc
        except NotADirectoryError as exc:
            raise HTTPException(status_code=400, detail="artifact destination is not a directory") from exc
        except (PermissionError, ValueError) as exc:
            raise HTTPException(status_code=403, detail="artifact destination access denied") from exc
        if not destination_item["is_dir"]:
            raise HTTPException(status_code=400, detail="artifact destination is not a directory")
    skill_source = await _require_skill_artifact_access(normalized_path=normalized, current_uid=current_uid, db=db)
    descriptor, temp_path = tempfile.mkstemp(prefix="yuxi-save-artifact-")
    os.close(descriptor)
    try:
        if source is None:
            await _copy_artifact_to_path(access, normalized, skill_source, temp_path)
        else:
            if len(source.content) > MAX_ARTIFACT_DOWNLOAD_BYTES:
                raise HTTPException(status_code=413, detail="artifact exceeds transfer limit")
            await asyncio.to_thread(_write_session_artifact_to_path, source.content, temp_path)
        stem = PurePosixPath(file_name).stem
        suffix = PurePosixPath(file_name).suffix
        for index in range(MAX_SAVED_ARTIFACT_NAME_ATTEMPTS + 1):
            candidate_name = file_name if index == 0 else f"{stem} ({index}){suffix}"
            target_scope = f"{destination_scope.rstrip('/')}/{candidate_name}"
            target = runtime_user_data_path(target_scope)
            try:
                await asyncio.to_thread(
                    access.workdir.workspace.upload_authorized_file_from_path,
                    target_scope,
                    temp_path,
                    overwrite=False,
                    create_parents=not destination_must_exist,
                )
                break
            except FileExistsError:
                continue
            except FileNotFoundError as exc:
                raise HTTPException(status_code=404, detail="artifact destination does not exist") from exc
            except NotADirectoryError as exc:
                raise HTTPException(status_code=400, detail="artifact destination is not a directory") from exc
            except (PermissionError, ValueError) as exc:
                raise HTTPException(status_code=403, detail="artifact destination access denied") from exc
        else:
            raise HTTPException(status_code=409, detail="saved artifact name space is exhausted")
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temp_path)
    return {
        "name": PurePosixPath(target).name,
        "source_path": normalized,
        "saved_path": target,
        "saved_artifact_url": (
            f"/api/chat/thread/{thread_id}/artifacts/{target.lstrip('/')}"
            if target.startswith(f"{workdir_root}/")
            or target.startswith(f"{VIRTUAL_PATH_PREFIX.rstrip('/')}/saved_artifacts/")
            or PurePosixPath(target_scope).parent == PurePosixPath("/")
            else f"/api/workspace/download?path={quote(target_scope, safe='/')}"
        ),
    }
