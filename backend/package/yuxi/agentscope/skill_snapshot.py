"""提交时封存并在 AgentScope 进程内物化不可变 Skill tree。"""

from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import json
import os
import re
import stat
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

from yuxi.agents.skills.metadata import SKILL_SLUG_PATTERN
from yuxi.utils.upload_utils import MAX_UPLOAD_SIZE_BYTES
from yuxi.utils.paths import open_regular_file_fd

MAX_SKILL_SNAPSHOT_ENTRIES = 4096
MAX_SKILL_SNAPSHOT_BYTES = MAX_UPLOAD_SIZE_BYTES
_SNAPSHOT_REF_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _validated_slug(raw_slug: object) -> str:
    """验证快照 slug，确保物化目录是临时根下的单级目录。"""
    if not isinstance(raw_slug, str):
        raise ValueError("Skill 快照 slug 非法")
    slug = raw_slug.strip()
    if raw_slug != slug or len(slug) > 128 or SKILL_SLUG_PATTERN.fullmatch(slug) is None:
        raise ValueError("Skill 快照 slug 非法")
    return slug


def _check_snapshot_limits(*, entries: int, total_bytes: int) -> None:
    """限制快照条目数量和未编码内容大小。"""
    if entries > MAX_SKILL_SNAPSHOT_ENTRIES:
        raise ValueError(f"Skill 快照条目不能超过 {MAX_SKILL_SNAPSHOT_ENTRIES}")
    if total_bytes > MAX_SKILL_SNAPSHOT_BYTES:
        raise ValueError(f"Skill 快照大小不能超过 {MAX_SKILL_SNAPSHOT_BYTES} 字节")


def _snapshot_tree_usage(skill: dict) -> tuple[str, int, int] | None:
    """返回快照树身份和资源占用；历史 source_dir 投影不参与嵌入配额。"""
    snapshot_files = skill.get("snapshot_files")
    if snapshot_files is None:
        return None
    if not isinstance(snapshot_files, list):
        raise ValueError("Skill 快照文件列表非法")
    directories = skill.get("snapshot_directories") or []
    if not isinstance(directories, list):
        raise ValueError("Skill 快照目录列表非法")

    slug = _validated_slug(skill.get("slug"))
    directory_paths = [_validated_relative_path(path).as_posix() for path in directories]
    file_fingerprints: list[tuple[str, bool, str]] = []
    file_paths: set[str] = set()
    total_bytes = 0
    entries = len(directory_paths) + len(snapshot_files)
    _check_snapshot_limits(entries=entries, total_bytes=0)
    for item in snapshot_files:
        if not isinstance(item, dict):
            raise ValueError("Skill 快照文件项非法")
        relative_path = _validated_relative_path(item.get("path")).as_posix()
        if relative_path in file_paths:
            raise ValueError("Skill 快照包含重复文件")
        file_paths.add(relative_path)
        encoded_content = item.get("content_base64")
        if not isinstance(encoded_content, str):
            raise ValueError("Skill 快照文件内容非法")
        if len(encoded_content) > 4 * ((MAX_SKILL_SNAPSHOT_BYTES + 2) // 3):
            raise ValueError(f"Skill 快照大小不能超过 {MAX_SKILL_SNAPSHOT_BYTES} 字节")
        try:
            content = base64.b64decode(encoded_content, validate=True)
        except (ValueError, TypeError) as exc:
            raise ValueError("Skill 快照文件内容非法") from exc
        total_bytes += len(content)
        _check_snapshot_limits(entries=entries, total_bytes=total_bytes)
        file_fingerprints.append(
            (
                relative_path,
                bool(item.get("executable")),
                hashlib.sha256(content).hexdigest(),
            )
        )

    identity_payload = json.dumps(
        {
            "slug": slug,
            "directories": sorted(directory_paths),
            "files": sorted(file_fingerprints),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode()
    return hashlib.sha256(identity_payload).hexdigest(), entries, total_bytes


def validate_run_skill_snapshots(projections: dict) -> None:
    """限制一个 Run 的主 Agent 和全部 SubAgent 唯一 Skill tree 总量。"""
    if not isinstance(projections, dict):
        raise ValueError("Run Skill 快照投影非法")
    seen: set[str] = set()
    total_entries = 0
    total_bytes = 0
    for projection in projections.values():
        if not isinstance(projection, dict):
            raise ValueError("Run Skill 快照投影非法")
        skills = projection.get("skills") or []
        if not isinstance(skills, list):
            raise ValueError("Run Skill 快照列表非法")
        for skill in skills:
            if not isinstance(skill, dict):
                raise ValueError("Skill 快照项非法")
            usage = _snapshot_tree_usage(skill)
            if usage is None:
                continue
            identity, entries, tree_bytes = usage
            if identity in seen:
                continue
            seen.add(identity)
            total_entries += entries
            total_bytes += tree_bytes
            _check_snapshot_limits(entries=total_entries, total_bytes=total_bytes)


def add_skill_tree_reference(
    skill: dict,
    skill_trees: dict[str, dict],
    *,
    total_entries: int,
    total_bytes: int,
) -> tuple[dict, int, int]:
    """把完整 Skill tree 提升到顶层内容寻址表，并返回投影引用。"""
    usage = _snapshot_tree_usage(skill)
    if usage is None:
        raise ValueError("Skill 快照缺少完整文件树")
    identity, entries, tree_bytes = usage
    tree = {
        "slug": _validated_slug(skill.get("slug")),
        "snapshot_directories": sorted(
            _validated_relative_path(path).as_posix() for path in (skill.get("snapshot_directories") or [])
        ),
        "snapshot_files": sorted(
            (copy.deepcopy(item) for item in skill.get("snapshot_files") or []),
            key=lambda item: str(item.get("path") or ""),
        ),
    }
    existing = skill_trees.get(identity)
    if existing is None:
        total_entries += entries
        total_bytes += tree_bytes
        _check_snapshot_limits(entries=total_entries, total_bytes=total_bytes)
        skill_trees[identity] = tree
    elif existing != tree:
        raise ValueError("Skill 快照内容寻址身份冲突")

    projection_skill = {
        key: copy.deepcopy(value)
        for key, value in skill.items()
        if key not in {"snapshot_directories", "snapshot_files", "source_dir"}
    }
    projection_skill["snapshot_ref"] = identity
    return projection_skill, total_entries, total_bytes


def validate_referenced_run_skill_snapshots(projections: dict, skill_trees: dict) -> None:
    """校验 v2 顶层 Skill tree、引用完整性和一个 Run 的聚合配额。"""
    if not isinstance(projections, dict) or not isinstance(skill_trees, dict):
        raise ValueError("Run Skill 快照投影非法")

    total_entries = 0
    total_bytes = 0
    for reference, tree in skill_trees.items():
        if not isinstance(reference, str) or _SNAPSHOT_REF_PATTERN.fullmatch(reference) is None:
            raise ValueError("Skill 快照引用非法")
        if not isinstance(tree, dict):
            raise ValueError("Skill 快照树非法")
        usage = _snapshot_tree_usage(tree)
        if usage is None:
            raise ValueError("Skill 快照树缺少完整文件树")
        identity, entries, tree_bytes = usage
        if not hmac.compare_digest(reference, identity):
            raise ValueError("Skill 快照引用与内容身份不一致")
        total_entries += entries
        total_bytes += tree_bytes
        _check_snapshot_limits(entries=total_entries, total_bytes=total_bytes)

    for projection in projections.values():
        if not isinstance(projection, dict):
            raise ValueError("Run Skill 快照投影非法")
        skills = projection.get("skills") or []
        if not isinstance(skills, list):
            raise ValueError("Run Skill 快照列表非法")
        for skill in skills:
            if not isinstance(skill, dict):
                raise ValueError("Skill 快照项非法")
            reference = skill.get("snapshot_ref")
            if not isinstance(reference, str) or _SNAPSHOT_REF_PATTERN.fullmatch(reference) is None:
                raise ValueError("Skill 快照引用非法")
            tree = skill_trees.get(reference)
            if tree is None:
                raise ValueError("Skill 快照引用不存在")
            if "snapshot_files" in skill or "snapshot_directories" in skill:
                raise ValueError("Skill 快照引用不能同时包含内联文件树")
            if _validated_slug(skill.get("slug")) != _validated_slug(tree.get("slug")):
                raise ValueError("Skill 快照投影与引用身份不一致")


def expand_referenced_run_skill_snapshots(projections: dict, skill_trees: dict) -> dict:
    """把已校验的 v2 引用展开为 RuntimeProjection 消费的内联结构。"""
    expanded = copy.deepcopy(projections)
    for projection in expanded.values():
        skills = projection.get("skills") or []
        for index, skill in enumerate(skills):
            reference = skill.pop("snapshot_ref")
            tree = skill_trees[reference]
            skills[index] = {
                **skill,
                "snapshot_directories": copy.deepcopy(tree["snapshot_directories"]),
                "snapshot_files": copy.deepcopy(tree["snapshot_files"]),
            }
    return expanded


def capture_skill_tree(skill: dict) -> dict:
    """把一个已授权 Skill 的普通文件树编码到加密 Run 快照。"""
    slug = _validated_slug(skill.get("slug"))
    source_dir = Path(str(skill.get("source_dir") or ""))
    if not source_dir.is_absolute() or ".." in source_dir.parts or source_dir.is_symlink():
        raise ValueError(f"Skill {slug} 来源目录非法")
    if not source_dir.is_dir():
        raise FileNotFoundError(source_dir)

    files: list[dict] = []
    directories: list[str] = []
    entry_count = 0
    total_bytes = 0
    for entry in _iter_bounded_skill_entries(source_dir):
        entry_count += 1
        _check_snapshot_limits(entries=entry_count, total_bytes=total_bytes)
        relative = entry.relative_to(source_dir)
        relative_path = relative.as_posix()
        if entry.is_symlink():
            raise ValueError(f"Skill {slug} 来源只允许普通文件和目录")
        if entry.is_dir():
            directories.append(relative_path)
            continue
        if not entry.is_file():
            raise ValueError(f"Skill {slug} 来源只允许普通文件和目录")
        with open_regular_file_fd(source_dir, tuple(relative.parts)) as (file_fd, file_stat):
            chunks = []
            while chunk := os.read(file_fd, 1024 * 1024):
                total_bytes += len(chunk)
                _check_snapshot_limits(entries=entry_count, total_bytes=total_bytes)
                chunks.append(chunk)
        files.append(
            {
                "path": relative_path,
                "content_base64": base64.b64encode(b"".join(chunks)).decode("ascii"),
                "executable": bool(stat.S_IMODE(file_stat.st_mode) & 0o111),
            }
        )

    if not any(item["path"] == "SKILL.md" for item in files):
        raise ValueError(f"Skill {slug} 缺少根级 SKILL.md")
    return {key: value for key, value in skill.items() if key not in {"slug", "source_dir"}} | {
        "slug": slug,
        "snapshot_directories": directories,
        "snapshot_files": files,
    }


def _iter_bounded_skill_entries(source_dir: Path) -> Iterator[Path]:
    """增量枚举并限制已发现的条目，再按相对路径顺序遍历。"""
    pending = [source_dir]
    discovered = 0
    while pending:
        entry = pending.pop()
        if entry != source_dir:
            yield entry
        if entry.is_dir() and not entry.is_symlink():
            children = []
            with os.scandir(entry) as listing:
                for child in listing:
                    discovered += 1
                    _check_snapshot_limits(entries=discovered, total_bytes=0)
                    children.append(Path(child.path))
            pending.extend(sorted(children, key=lambda path: path.name, reverse=True))


def _validated_relative_path(raw_path: str) -> PurePosixPath:
    """验证快照内相对路径，拒绝绝对路径和目录穿越。"""
    if not isinstance(raw_path, str):
        raise ValueError("Skill 快照路径非法")
    raw = raw_path
    path = PurePosixPath(raw)
    if not raw or path.is_absolute() or "\\" in raw or "://" in raw:
        raise ValueError("Skill 快照路径非法")
    if any(part in {"", ".", ".."} for part in raw.split("/")):
        raise ValueError("Skill 快照路径非法")
    return path


def _write_all(file_fd: int, content: bytes) -> None:
    """完整写入文件描述符。"""
    offset = 0
    while offset < len(content):
        offset += os.write(file_fd, content[offset:])


@contextmanager
def materialized_skill_source(skill: dict) -> Iterator[Path]:
    """把新快照物化为临时目录；历史快照继续使用其固化来源路径。"""
    snapshot_files = skill.get("snapshot_files")
    if snapshot_files is None:
        source_dir = Path(str(skill.get("source_dir") or ""))
        if not source_dir.is_absolute() or ".." in source_dir.parts:
            raise ValueError(f"Skill {skill.get('slug')} 来源目录非法")
        yield source_dir
        return
    if not isinstance(snapshot_files, list):
        raise ValueError("Skill 快照文件列表非法")

    slug = _validated_slug(skill.get("slug"))
    directories = skill.get("snapshot_directories") or []
    if not isinstance(directories, list):
        raise ValueError("Skill 快照目录列表非法")
    _check_snapshot_limits(entries=len(directories) + len(snapshot_files), total_bytes=0)

    validated_directories = [_validated_relative_path(raw_path) for raw_path in directories]
    prepared_files: list[tuple[dict, PurePosixPath]] = []
    file_paths: set[str] = set()
    for item in snapshot_files:
        if not isinstance(item, dict):
            raise ValueError("Skill 快照文件项非法")
        relative = _validated_relative_path(item.get("path"))
        relative_path = relative.as_posix()
        if relative_path in file_paths:
            raise ValueError("Skill 快照包含重复文件")
        file_paths.add(relative_path)
        prepared_files.append((item, relative))

    directory_paths = {path.as_posix() for path in validated_directories}
    if directory_paths & file_paths:
        raise ValueError("Skill 快照文件与目录路径冲突")
    for path in [*validated_directories, *(relative for _, relative in prepared_files)]:
        if any(parent.as_posix() in file_paths for parent in path.parents if parent != PurePosixPath(".")):
            raise ValueError("Skill 快照文件与目录路径冲突")

    with tempfile.TemporaryDirectory(prefix="yuxi-run-skill-") as temp_root:
        target_root = Path(temp_root) / slug
        target_root.mkdir(mode=0o700)
        for relative in validated_directories:
            target_root.joinpath(*relative.parts).mkdir(parents=True, mode=0o700, exist_ok=True)

        total_bytes = 0
        for item, relative in prepared_files:
            encoded_content = item.get("content_base64")
            if not isinstance(encoded_content, str):
                raise ValueError("Skill 快照文件内容非法")
            if len(encoded_content) > 4 * ((MAX_SKILL_SNAPSHOT_BYTES + 2) // 3):
                raise ValueError(f"Skill 快照大小不能超过 {MAX_SKILL_SNAPSHOT_BYTES} 字节")
            try:
                content = base64.b64decode(encoded_content, validate=True)
            except (ValueError, TypeError) as exc:
                raise ValueError("Skill 快照文件内容非法") from exc
            total_bytes += len(content)
            _check_snapshot_limits(entries=len(directories) + len(snapshot_files), total_bytes=total_bytes)
            target = target_root.joinpath(*relative.parts)
            target.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            file_fd = os.open(
                target,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o700 if bool(item.get("executable")) else 0o600,
            )
            try:
                _write_all(file_fd, content)
            finally:
                os.close(file_fd)

        if "SKILL.md" not in file_paths:
            raise ValueError(f"Skill {slug} 快照缺少根级 SKILL.md")
        yield target_root
