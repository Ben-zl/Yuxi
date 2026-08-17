"""按线程选择集隔离的本地只读 Skills 文件后端。"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from yuxi.agents.skills.service import get_skills_root_dir, is_valid_skill_slug


@dataclass
class LsResult:
    error: str | None = None
    entries: list[dict] | None = None


@dataclass
class ReadResult:
    error: str | None = None
    file_data: dict | None = None


@dataclass
class GrepResult:
    error: str | None = None
    matches: list[dict] | None = None


@dataclass
class GlobResult:
    error: str | None = None
    matches: list[dict] | None = None


@dataclass
class FileDownloadResponse:
    path: str
    content: bytes | None = None
    error: str | None = None


@dataclass
class WriteResult:
    error: str | None = None


@dataclass
class EditResult:
    error: str | None = None


@dataclass
class FileUploadResponse:
    path: str
    error: str | None = None


class SelectedSkillsReadonlyBackend:
    """只暴露配置中选中的 Skill 目录。"""

    def __init__(self, *, selected_slugs: list[str] | None, root_dir: Path | None = None):
        self.root_dir = (root_dir or get_skills_root_dir()).resolve()
        self._selected_slugs = {
            slug.strip()
            for slug in (selected_slugs or [])
            if isinstance(slug, str) and is_valid_skill_slug(slug.strip())
        }

    def _resolve(self, path: str, *, require_skill: bool) -> tuple[Path, str | None]:
        pure = PurePosixPath(path if str(path).startswith("/") else f"/{path}")
        if ".." in pure.parts:
            raise ValueError("path traversal is not allowed")
        parts = [part for part in pure.parts if part not in {"/", ""}]
        slug = parts[0] if parts else None
        if require_skill and slug is None:
            raise PermissionError("Access denied: file is outside selected skills.")
        if slug is not None and slug not in self._selected_slugs:
            noun = "file" if require_skill else "path"
            raise PermissionError(f"Access denied: {noun} is outside selected skills.")

        target = self.root_dir.joinpath(*parts).resolve()
        if not target.is_relative_to(self.root_dir):
            raise PermissionError("Access denied: path is outside selected skills.")
        return target, slug

    def _entry(self, path: Path) -> dict:
        relative = path.relative_to(self.root_dir).as_posix()
        stat = path.stat()
        is_dir = path.is_dir()
        virtual_path = f"/{relative}"
        if is_dir:
            virtual_path += "/"
        return {
            "path": virtual_path,
            "is_dir": is_dir,
            "size": 0 if path.is_dir() else stat.st_size,
            "modified_at": str(stat.st_mtime),
        }

    def ls(self, path: str) -> LsResult:
        if not self._selected_slugs:
            return LsResult(entries=[])
        try:
            target, slug = self._resolve(path or "/", require_skill=False)
            if slug is None:
                entries = [
                    self._entry(self.root_dir / item)
                    for item in sorted(self._selected_slugs)
                    if (self.root_dir / item).is_dir()
                ]
            elif not target.exists():
                entries = []
            elif not target.is_dir():
                return LsResult(error="当前路径不是目录")
            else:
                entries = [self._entry(child) for child in sorted(target.iterdir(), key=lambda item: item.name)]
            return LsResult(entries=entries)
        except (PermissionError, ValueError) as exc:
            return LsResult(error=str(exc))

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        try:
            target, _ = self._resolve(file_path, require_skill=True)
            if not target.exists():
                return ReadResult(error="file_not_found")
            if target.is_dir():
                return ReadResult(error="is_directory")
            lines = target.read_text(encoding="utf-8").splitlines(keepends=True)
            return ReadResult(file_data={"content": "".join(lines[offset : offset + limit]), "encoding": "utf-8"})
        except (PermissionError, ValueError, UnicodeDecodeError) as exc:
            return ReadResult(error=str(exc))

    def grep(self, pattern: str, path: str | None = None, glob: str | None = None) -> GrepResult:
        try:
            target, _ = self._resolve(path or "/", require_skill=False)
            matches = []
            candidates = [target] if target.is_file() else target.rglob(glob or "*")
            for candidate in candidates:
                if not candidate.is_file() or (glob and not fnmatch.fnmatch(candidate.name, glob)):
                    continue
                try:
                    for line_number, text in enumerate(candidate.read_text(encoding="utf-8").splitlines(), 1):
                        if pattern in text:
                            matches.append({"path": self._entry(candidate)["path"], "line": line_number, "text": text})
                except UnicodeDecodeError:
                    continue
            return GrepResult(matches=matches)
        except (PermissionError, ValueError) as exc:
            return GrepResult(error=str(exc))

    def glob(self, pattern: str, path: str = "/") -> GlobResult:
        if ".." in PurePosixPath(pattern).parts:
            return GlobResult(error="path traversal is not allowed")
        try:
            target, _ = self._resolve(path, require_skill=False)
            matches = [self._entry(item) for item in target.glob(pattern) if item.exists()]
            return GlobResult(matches=matches)
        except (PermissionError, ValueError) as exc:
            return GlobResult(error=str(exc))

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        responses = []
        for path in paths:
            try:
                target, _ = self._resolve(path, require_skill=True)
                if not target.exists():
                    responses.append(FileDownloadResponse(path=path, error="file_not_found"))
                elif target.is_dir():
                    responses.append(FileDownloadResponse(path=path, error="is_directory"))
                else:
                    responses.append(FileDownloadResponse(path=path, content=target.read_bytes()))
            except (PermissionError, ValueError):
                responses.append(FileDownloadResponse(path=path, error="invalid_path"))
        return responses

    def write(self, file_path: str, content: str) -> WriteResult:
        return WriteResult(error="Skills path is read-only.")

    def edit(self, file_path: str, old_string: str, new_string: str, replace_all: bool = False) -> EditResult:
        return EditResult(error="Skills path is read-only.")

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return [FileUploadResponse(path=path, error="permission_denied") for path, _ in files]
