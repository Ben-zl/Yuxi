"""ReMe Markdown 记忆卡片的安全枚举、删除和索引维护。"""

from __future__ import annotations

import hashlib
import inspect
import shutil
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime
from pathlib import Path, PurePosixPath
from typing import Any

import frontmatter


def _memory_id(relative_path: str) -> str:
    """把 scope 相对路径转换为不泄露目录结构的稳定 ID。"""
    return hashlib.sha256(relative_path.encode("utf-8")).hexdigest()


def _safe_file(root: Path, path: Path) -> bool:
    """仅接受 root 下真实普通文件，拒绝符号链接和路径逃逸。"""
    try:
        if path.is_symlink() or not path.is_file():
            return False
        path.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return False
    return True


def _daily_paths(root: Path) -> list[tuple[Path, str, str, str]]:
    """枚举 daily/<YYYY-MM-DD>/*.md，不包含日期索引。"""
    daily_root = root / "daily"
    if not daily_root.is_dir() or daily_root.is_symlink():
        return []
    result = []
    for day_dir in sorted(daily_root.iterdir()):
        if day_dir.is_symlink() or not day_dir.is_dir():
            continue
        try:
            date.fromisoformat(day_dir.name)
        except ValueError:
            continue
        for path in sorted(day_dir.glob("*.md")):
            if _safe_file(root, path):
                result.append((path, "daily", "personal", day_dir.name))
    return result


def _digest_paths(root: Path) -> list[tuple[Path, str, str, str | None]]:
    """枚举允许展示的三类 Digest Markdown。"""
    result = []
    for category in ("personal", "procedure", "wiki"):
        category_root = root / "digest" / category
        if not category_root.is_dir() or category_root.is_symlink():
            continue
        for path in sorted(category_root.rglob("*.md")):
            if _safe_file(root, path):
                result.append((path, "digest", category, None))
    return result


def _read_card(root: Path, item: tuple[Path, str, str, str | None]) -> dict[str, Any] | None:
    """读取单张 Markdown 卡片并裁剪为公开字段。"""
    path, kind, category, memory_date = item
    try:
        relative = path.relative_to(root).as_posix()
        post = frontmatter.loads(path.read_text(encoding="utf-8"))
        metadata = dict(post.metadata or {})
        body = " ".join(str(post.content or "").split())
        stat = path.stat()
    except (OSError, UnicodeError, ValueError):
        return None
    return {
        "memory_id": _memory_id(relative),
        "kind": kind,
        "category": category,
        "title": str(metadata.get("name") or path.stem),
        "summary": str(metadata.get("description") or body[:300]),
        "memory_date": memory_date or metadata.get("date"),
        "updated_at": datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
    }


def list_memory_cards(
    root: str | Path,
    *,
    kind: str = "all",
    category: str = "all",
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    """分页列出一个 scope 的 Daily/Digest 卡片。"""
    root_path = Path(root)
    candidates = [*_daily_paths(root_path), *_digest_paths(root_path)]
    cards = [card for item in candidates if (card := _read_card(root_path, item)) is not None]
    if kind != "all":
        cards = [item for item in cards if item["kind"] == kind]
    if category != "all":
        cards = [item for item in cards if item["category"] == category]
    cards.sort(key=lambda item: (item["memory_date"] or "", item["updated_at"]), reverse=True)
    total = len(cards)
    start = (page - 1) * page_size
    return {
        "items": cards[start : start + page_size],
        "pagination": {"page": page, "page_size": page_size, "total": total},
    }


def latest_memory_at(root: str | Path) -> datetime | None:
    """返回可管理记忆卡片的最新文件时间；没有卡片时返回 None。"""
    root_path = Path(root)
    mtimes = [item[0].stat().st_mtime for item in [*_daily_paths(root_path), *_digest_paths(root_path)]]
    return datetime.fromtimestamp(max(mtimes), tz=UTC) if mtimes else None


def _find_card(root: Path, memory_id: str) -> tuple[Path, str, str, str | None] | None:
    """通过哈希 ID 在允许的卡片集合中定位文件。"""
    for item in [*_daily_paths(root), *_digest_paths(root)]:
        relative = item[0].relative_to(root).as_posix()
        if _memory_id(relative) == memory_id:
            return item
    return None


def _session_id(path: Path) -> str | None:
    """读取 Daily card 的来源 session_id。"""
    try:
        value = frontmatter.loads(path.read_text(encoding="utf-8")).metadata.get("session_id")
    except (OSError, UnicodeError, ValueError):
        return None
    return str(value).strip() if value else None


async def delete_memory_card(
    root: str | Path,
    memory_id: str,
    *,
    maintain_index: Callable[[str | None], Awaitable[None] | None],
) -> None:
    """暂存删除一张卡片，完整重建索引后才最终提交文件删除。"""
    root_path = Path(root).resolve()
    item = _find_card(root_path, memory_id)
    if item is None:
        raise FileNotFoundError("memory_not_found")
    target, kind, _category, memory_date = item
    session_id = _session_id(target) if kind == "daily" else None
    staging = root_path / f".yuxi-delete-{uuid.uuid4().hex}"
    moved: list[tuple[Path, Path]] = []

    def stage(path: Path) -> None:
        if not _safe_file(root_path, path):
            return
        relative = path.relative_to(root_path)
        destination = staging / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        path.replace(destination)
        moved.append((destination, path))

    stage(target)
    if kind == "daily" and memory_date:
        stage(root_path / "daily" / f"{memory_date}.md")
    if session_id:
        still_referenced = any(_session_id(candidate[0]) == session_id for candidate in _daily_paths(root_path))
        if not still_referenced:
            safe_session = PurePosixPath(session_id)
            if len(safe_session.parts) == 1 and safe_session.name == session_id:
                stage(root_path / "session" / "dialog" / f"{session_id}.jsonl")

    try:
        result = maintain_index(memory_date if kind == "daily" else None)
        if inspect.isawaitable(result):
            await result
    except Exception:
        for source, destination in reversed(moved):
            destination.parent.mkdir(parents=True, exist_ok=True)
            source.replace(destination)
        shutil.rmtree(staging, ignore_errors=True)
        result = maintain_index(memory_date if kind == "daily" else None)
        if inspect.isawaitable(result):
            await result
        raise
    shutil.rmtree(staging, ignore_errors=True)


def clear_memory_workspace(root: str | Path, *, base_dir: str | Path) -> bool:
    """安全删除一个精确 scope Workspace；不存在时返回 False。"""
    path = Path(root)
    if path.is_symlink():
        raise ValueError("Memory Workspace 必须是真实目录")
    if not path.exists():
        return False
    if not path.is_dir():
        raise ValueError("Memory Workspace 必须是真实目录")
    resolved = path.resolve()
    base = Path(base_dir).resolve()
    try:
        relative = resolved.relative_to(base)
    except ValueError as exc:
        raise ValueError("Memory Workspace 不在配置根目录下") from exc
    if len(relative.parts) != 2:
        raise ValueError("拒绝删除不安全的 Memory Workspace 路径")
    shutil.rmtree(resolved)
    return True
