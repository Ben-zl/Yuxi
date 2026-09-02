"""Yuxi 对 ReMe Daily 近重复卡片的保守归并。"""

from __future__ import annotations

import inspect
import hashlib
import re
import shutil
import unicodedata
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

import frontmatter


@dataclass(frozen=True)
class CompactionResult:
    """一次 Daily 归并的可观测结果。"""

    changed: bool
    canonical_count: int
    archived_count: int


@dataclass(frozen=True)
class _Topic:
    key: str
    filename: str
    title: str
    description: str
    body: str


_PERFORMANCE_RETRIEVAL = _Topic(
    key="starsand-island-pc-performance-retrieval",
    filename="yuxi-canonical-starsand-island-pc-performance-retrieval.md",
    title="星砂岛 PC 性能监控数据获取",
    description="星砂岛 PC 性能监控数据获取的跨会话稳定主题，临时派发和等待状态不作为长期事实。",
    body=(
        "# 星砂岛 PC 性能监控数据获取\n\n"
        "- 项目：星砂岛（`AUTOMATION_PROJECT_ID=starsandisland`）\n"
        "- 自动化任务：PC-分支-性能监控\n"
        "- 数据周期：2026.8.28\n"
        "- 用户标识：`AUTOMATION_USER_ID=2325`\n\n"
        "该主题用于归并多个会话中对同一批性能监控数据的重复请求；任务是否仍在等待应以最新运行状态为准。\n"
    ),
)

_DIY_ANALYSIS = _Topic(
    key="starsand-island-large-diy-performance-analysis",
    filename="yuxi-canonical-starsand-island-large-diy-performance-analysis.md",
    title="星砂岛大规模 DIY 性能分析",
    description="星砂岛大规模 DIY 场景的稳定性能分析主题，保留用例和周期等可复用事实。",
    body=(
        "# 星砂岛大规模 DIY 性能分析\n\n"
        "- 场景：大规模 DIY\n"
        "- 用例：`ci=13740`\n"
        "- 周期：`#246`\n"
        "- 问题：卡顿、低 FPS\n\n"
        "后续分析应聚焦瓶颈定位、低配机和单点异常，以及可验证的优化建议；执行中的等待状态不作为长期事实。\n"
    ),
)

_TRANSIENT_MEMORY_MARKERS = (
    "已创建团队",
    "创建团队",
    "委派给",
    "委派子智能体",
    "已委派",
    "已派发",
    "派发给",
    "正在等待",
    "等待结果",
    "等待回报",
    "等待成员",
    "等待子智能体",
    "尚未返回",
    "结果尚未",
    "执行中",
    "处理中",
    "pending",
    "running",
    "in progress",
    "之后将交付",
    "稍后交付",
    "承诺交付",
)
_TRANSIENT_SECTION_TITLES = ("执行状态", "当前状态", "任务状态")


def strip_transient_memory_state(text: str) -> str:
    """移除单次运行状态，保留同句稳定前缀和原有 Markdown 缩进。"""
    cleaned_lines = []
    for line in text.splitlines() or [text]:
        indentation = line[: len(line) - len(line.lstrip())]
        content = line[len(indentation) :]
        cleaned_segments = []

        for segment in re.findall(r".*?[。！？!?；;]|.+$", content):
            lowered = segment.lower()
            positions = [lowered.find(marker) for marker in _TRANSIENT_MEMORY_MARKERS if marker in lowered]
            if not positions:
                cleaned_segments.append(segment)
                continue

            marker_at = min(positions)
            prefix = segment[:marker_at]
            colon_at = max(prefix.rfind(":"), prefix.rfind("："))
            if colon_at >= 0 and not prefix[colon_at + 1 :].strip(" \t，,、-→"):
                continue
            prefix = prefix.rstrip(" \t，,、:：-→")
            if not prefix.strip():
                continue
            if segment[-1:] in "。！？!?；;" and prefix[-1:] not in "。！？!?；;":
                prefix += segment[-1]
            cleaned_segments.append(prefix)

        cleaned = "".join(cleaned_segments).rstrip()
        if cleaned:
            cleaned_lines.append(f"{indentation}{cleaned}")
        elif not content.strip():
            cleaned_lines.append("")
    return "\n".join(cleaned_lines).strip("\n")


def sanitize_daily_runtime_state(root: str | Path, *, date: str) -> bool:
    """清理当日未归档 Daily 中不应持久化的运行态内容。"""
    root_path = Path(root).resolve()
    day_dir = root_path / "daily" / date
    if day_dir.is_symlink() or not day_dir.is_dir():
        return False
    day_dir.resolve().relative_to(root_path)

    changed = False
    for path in sorted(day_dir.glob("*.md")):
        if path.is_symlink() or not path.is_file():
            continue
        path.resolve().relative_to(root_path)
        try:
            post = frontmatter.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            continue
        if post.metadata.get("yuxi_memory_status") in {"canonical", "archived_duplicate"}:
            continue

        metadata = dict(post.metadata)
        description = strip_transient_memory_state(str(metadata.get("description") or ""))
        if description:
            metadata["description"] = description
        else:
            metadata.pop("description", None)

        content_lines = []
        for line in str(post.content or "").splitlines():
            stripped = line.strip()
            if stripped.startswith("#") and any(title in stripped for title in _TRANSIENT_SECTION_TITLES):
                continue
            cleaned = strip_transient_memory_state(line)
            if cleaned:
                content_lines.append(cleaned)
            elif not stripped:
                content_lines.append("")
        content = "\n".join(content_lines).strip()
        rendered = _render_post(metadata, content)
        if rendered != path.read_text(encoding="utf-8"):
            path.write_text(rendered, encoding="utf-8")
            changed = True
    return changed


def _normalized(text: str) -> str:
    """统一大小写、Unicode 和常见项目别名，供高置信规则匹配。"""
    value = unicodedata.normalize("NFKC", text).lower()
    for alias in ("starry sands island", "starsand island", "starsandisland", "xingsha island"):
        value = value.replace(alias, "星砂岛")
    return re.sub(r"\s+", " ", value)


def _topic_filename(key: str) -> str:
    """为动态主题生成不泄露原始标识且稳定的 canonical 文件名。"""
    return f"yuxi-canonical-{hashlib.sha256(key.encode('utf-8')).hexdigest()[:20]}.md"


def _topic_definitions(text: str) -> dict[str, _Topic]:
    """从稳定项目键、任务、数据周期和用例键构造高置信主题。"""
    raw = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text).lower())
    value = _normalized(text)
    topics: dict[str, _Topic] = {}
    project_match = re.search(r"automation_project_id\s*=\s*([a-z0-9_-]+)", raw)
    project_id = project_match.group(1) if project_match else None
    is_starsand = "星砂岛" in value or project_id == "starsandisland"
    if is_starsand:
        project_id = "starsandisland"
    project_name = "星砂岛" if is_starsand else project_id

    period_match = re.search(r"(20\d{2})[./-](0?\d{1,2})[./-](0?\d{1,2})", raw)
    has_retrieval_identity = "pc-分支-性能监控" in value or bool(project_id and "性能监控" in value)
    if project_id and period_match and has_retrieval_identity:
        year, month, day = (int(part) for part in period_match.groups())
        period = f"{year:04d}-{month:02d}-{day:02d}"
        if project_id == "starsandisland" and period == "2026-08-28":
            topics[_PERFORMANCE_RETRIEVAL.key] = _PERFORMANCE_RETRIEVAL
        else:
            key = f"pc-performance-retrieval:{project_id}:{period}"
            title = f"{project_name} PC 性能监控数据获取"
            user_match = re.search(r"automation_user_id\s*=\s*([a-z0-9_-]+)", raw)
            user_line = f"- 用户标识：`AUTOMATION_USER_ID={user_match.group(1)}`\n" if user_match else ""
            topics[key] = _Topic(
                key=key,
                filename=_topic_filename(key),
                title=title,
                description=f"{title}的跨会话稳定主题，临时派发和等待状态不作为长期事实。",
                body=(
                    f"# {title}\n\n"
                    f"- 项目：`AUTOMATION_PROJECT_ID={project_id}`\n"
                    "- 自动化任务：PC-分支-性能监控\n"
                    f"- 数据周期：{year}.{month}.{day}\n"
                    f"{user_line}\n"
                    "该主题用于归并多个会话中对同一批性能监控数据的重复请求；任务状态以最新运行事实为准。\n"
                ),
            )

    ci_match = re.search(r"ci\s*=\s*(\d+)", raw)
    cycle_match = re.search(r"#\s*(\d+)", raw)
    if project_id and "diy" in value and ci_match and cycle_match:
        ci, cycle = ci_match.group(1), cycle_match.group(1)
        if project_id == "starsandisland" and ci == "13740" and cycle == "246":
            topics[_DIY_ANALYSIS.key] = _DIY_ANALYSIS
        else:
            key = f"performance-analysis:{project_id}:diy:{ci}:{cycle}"
            title = f"{project_name}大规模 DIY 性能分析"
            topics[key] = _Topic(
                key=key,
                filename=_topic_filename(key),
                title=title,
                description=f"{title}的稳定主题，保留用例和周期等可复用事实。",
                body=(
                    f"# {title}\n\n"
                    "- 场景：大规模 DIY\n"
                    f"- 用例：`ci={ci}`\n"
                    f"- 周期：`#{cycle}`\n\n"
                    "后续分析应聚焦可验证的瓶颈、异常和优化结论；执行中的等待状态不作为长期事实。\n"
                ),
            )
    return topics


def detect_memory_topic_keys(text: str) -> set[str]:
    """识别具备稳定项目键、周期或用例键的高置信长期记忆主题。"""
    return set(_topic_definitions(text))


def _topics_for(text: str) -> dict[str, _Topic]:
    """把公开主题键映射为归并写入所需的 canonical 定义。"""
    return _topic_definitions(text)


def _load_sources(day_dir: Path, root: Path) -> list[tuple[Path, frontmatter.Post, dict[str, _Topic]]]:
    """读取当天非 canonical Daily，并返回每张卡片命中的高置信主题。"""
    result = []
    for path in sorted(day_dir.glob("*.md")):
        if path.is_symlink() or not path.is_file() or path.name.startswith("yuxi-canonical-"):
            continue
        try:
            path.resolve().relative_to(root.resolve())
            post = frontmatter.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            continue
        if post.metadata.get("yuxi_memory_status") == "canonical":
            continue
        text = "\n".join(
            (
                str(post.metadata.get("name") or ""),
                str(post.metadata.get("description") or ""),
                str(post.content or ""),
            ),
        )
        result.append((path, post, _topics_for(text)))
    return result


def _load_existing_canonicals(root: Path) -> dict[str, tuple[Path, frontmatter.Post]]:
    """读取 scope 内已有 canonical，允许新日期来源接入历史主题。"""
    result: dict[str, tuple[Path, frontmatter.Post]] = {}
    daily_root = root / "daily"
    if not daily_root.is_dir() or daily_root.is_symlink():
        return result
    for path in daily_root.glob("*/*.md"):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            path.resolve().relative_to(root.resolve())
            post = frontmatter.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            continue
        if post.metadata.get("yuxi_memory_status") != "canonical":
            continue
        key = str(post.metadata.get("yuxi_topic_key") or "").strip()
        if key and key not in result:
            result[key] = (path, post)
    return result


def _render_post(metadata: dict, content: str) -> str:
    """使用统一 frontmatter 序列化，保证重复扫描结果稳定。"""
    return frontmatter.dumps(frontmatter.Post(content.rstrip() + "\n", **metadata)) + "\n"


async def compact_daily_memories(
    root: str | Path,
    *,
    date: str,
    maintain_index: Callable[[str], Awaitable[None] | None],
) -> CompactionResult:
    """归并一天内的高置信近重复 Daily，失败时恢复全部文件。"""
    root_path = Path(root).resolve()
    day_dir = root_path / "daily" / date
    if day_dir.is_symlink() or not day_dir.is_dir():
        return CompactionResult(False, 0, 0)
    try:
        day_dir.resolve().relative_to(root_path)
    except ValueError as exc:
        raise ValueError("Daily 目录不在 Memory Workspace 内") from exc

    sources = _load_sources(day_dir, root_path)
    existing_canonicals = _load_existing_canonicals(root_path)
    topic_sources: dict[str, list[Path]] = {}
    topic_definitions: dict[str, _Topic] = {}
    for path, _post, topics in sources:
        for key, topic in topics.items():
            topic_sources.setdefault(key, []).append(path)
            current = topic_definitions.get(key)
            if current is None or len(topic.body) > len(current.body):
                topic_definitions[key] = topic
    composite_sources = {path for path, _post, topics in sources if len(topics) > 1}
    selected = {
        key: paths
        for key, paths in topic_sources.items()
        if key in existing_canonicals or len(paths) > 1 or any(path in composite_sources for path in paths)
    }
    if not selected:
        return CompactionResult(False, 0, 0)

    desired: dict[Path, str] = {}
    canonical_paths_by_source: dict[Path, list[str]] = {}
    for key, paths in selected.items():
        existing = existing_canonicals.get(key)
        topic = topic_definitions.get(key)
        if existing is not None:
            canonical, existing_post = existing
            existing_sources = existing_post.metadata.get("yuxi_source_paths")
            relative_sources = {
                str(value) for value in existing_sources if isinstance(existing_sources, list) and str(value).strip()
            }
            relative_sources.update(path.relative_to(root_path).as_posix() for path in paths)
            metadata = dict(existing_post.metadata)
            metadata.update(
                yuxi_memory_status="canonical",
                yuxi_topic_key=key,
                yuxi_source_paths=sorted(relative_sources),
            )
            desired[canonical] = _render_post(metadata, str(existing_post.content or ""))
        else:
            topic = topic_definitions[key]
            relative_sources = sorted(path.relative_to(root_path).as_posix() for path in paths)
            canonical = day_dir / topic.filename
            desired[canonical] = _render_post(
                {
                    "name": topic.title,
                    "description": topic.description,
                    "yuxi_memory_status": "canonical",
                    "yuxi_topic_key": topic.key,
                    "yuxi_source_paths": relative_sources,
                },
                topic.body,
            )
        relative_canonical = canonical.relative_to(root_path).as_posix()
        for source in paths:
            canonical_paths_by_source.setdefault(source, []).append(relative_canonical)

    source_by_path = {path: post for path, post, _topics in sources}
    for source, canonical_paths in canonical_paths_by_source.items():
        post = source_by_path[source]
        metadata = dict(post.metadata)
        metadata["yuxi_memory_status"] = "archived_duplicate"
        metadata["yuxi_canonical_paths"] = sorted(canonical_paths)
        desired[source] = _render_post(metadata, str(post.content or ""))

    stale_canonical = set(day_dir.glob("yuxi-canonical-*.md")) - set(desired)
    changed_paths = {
        path for path, content in desired.items() if not path.exists() or path.read_text(encoding="utf-8") != content
    } | stale_canonical
    if not changed_paths:
        return CompactionResult(False, len(selected), len(canonical_paths_by_source))

    staging = root_path / f".yuxi-compact-{uuid.uuid4().hex}"
    staging.mkdir()
    existing = {path for path in changed_paths if path.exists()}
    try:
        for path in existing:
            backup = staging / path.relative_to(root_path)
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, backup)
        for path in stale_canonical:
            path.unlink(missing_ok=True)
        for path, content in desired.items():
            if path not in changed_paths:
                continue
            temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
            temporary.write_text(content, encoding="utf-8")
            temporary.replace(path)

        result = maintain_index(date)
        if inspect.isawaitable(result):
            await result
    except Exception:
        for path in changed_paths:
            path.unlink(missing_ok=True)
        for path in existing:
            backup = staging / path.relative_to(root_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup, path)
        try:
            result = maintain_index(date)
            if inspect.isawaitable(result):
                await result
        except Exception:
            pass
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    return CompactionResult(True, len(selected), len(canonical_paths_by_source))


__all__ = [
    "CompactionResult",
    "compact_daily_memories",
    "detect_memory_topic_keys",
    "sanitize_daily_runtime_state",
    "strip_transient_memory_state",
]
