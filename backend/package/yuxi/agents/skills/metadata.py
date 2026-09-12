"""Skill 元数据格式与解析规则。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

SKILL_SLUG_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def normalize_string_list(values: list[str] | None) -> list[str]:
    """规范字符串列表并保持首次出现顺序。"""
    if not values:
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        item = value.strip()
        if not item or item in seen:
            continue
        seen.add(item)
        normalized.append(item)
    return normalized


def split_skill_frontmatter(content: str) -> tuple[str, str]:
    """拆分严格位于文件开头的 YAML frontmatter。"""
    if not content.startswith("---"):
        raise ValueError("SKILL.md 缺少有效 frontmatter（--- ... ---）")

    lines = content.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise ValueError("SKILL.md 缺少有效 frontmatter（--- ... ---）")

    frontmatter_lines: list[str] = []
    body_start = 0
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            body_start = index + 1
            break
        frontmatter_lines.append(line)
    else:
        raise ValueError("SKILL.md 缺少有效 frontmatter（--- ... ---）")

    return "".join(frontmatter_lines), "".join(lines[body_start:])


def _validate_slug(slug: str, *, field_name: str) -> str:
    slug = slug.strip()
    if not slug:
        raise ValueError(f"SKILL.md frontmatter 缺少 {field_name}")
    if len(slug) > 128:
        raise ValueError(f"SKILL.md frontmatter.{field_name} 长度不能超过 128")
    if not SKILL_SLUG_PATTERN.match(slug):
        raise ValueError(f"SKILL.md frontmatter.{field_name} 必须是小写字母/数字/短横线，且不能连续短横线")
    return slug


def parse_skill_markdown(content: str) -> tuple[str, str, str, dict[str, Any]]:
    """按统一规则解析 SKILL.md frontmatter。"""
    frontmatter_raw, _body = split_skill_frontmatter(content)
    try:
        data = yaml.safe_load(frontmatter_raw)
    except yaml.YAMLError as exc:
        raise ValueError(f"SKILL.md frontmatter YAML 解析失败: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("SKILL.md frontmatter 必须是对象")

    name = str(data.get("name", "")).strip()
    if not name:
        raise ValueError("SKILL.md frontmatter 缺少 name")
    if len(name) > 128:
        raise ValueError("SKILL.md frontmatter.name 长度不能超过 128")
    raw_slug = str(data.get("slug", "")).strip()
    slug = _validate_slug(raw_slug, field_name="slug") if raw_slug else _validate_slug(name, field_name="name")
    description = str(data.get("description", "")).strip()
    if not description:
        raise ValueError("SKILL.md frontmatter 缺少 description")
    return slug, name, description, data


def rewrite_skill_frontmatter_slug(content: str, new_slug: str) -> str:
    """重写 Skill slug，并保留正文。"""
    frontmatter_raw, body = split_skill_frontmatter(content)
    data = yaml.safe_load(frontmatter_raw)
    if not isinstance(data, dict):
        raise ValueError("SKILL.md frontmatter 必须是对象")
    if data.get("slug"):
        data["slug"] = new_slug
    else:
        data["name"] = new_slug
    dumped = yaml.safe_dump(data, sort_keys=False, allow_unicode=True).strip()
    return f"---\n{dumped}\n---\n{body}"


def parse_skill_dir_metadata(source_skill_dir: Path) -> dict[str, Any]:
    """读取并规范 Skill 目录的根级元数据。"""
    skill_md_path = source_skill_dir / "SKILL.md"
    if not skill_md_path.exists() or not skill_md_path.is_file():
        raise ValueError("技能目录缺少根级 SKILL.md")
    slug, name, description, meta = parse_skill_markdown(skill_md_path.read_text(encoding="utf-8"))
    return {
        "slug": slug,
        "name": name,
        "description": description,
        "tool_dependencies": normalize_string_list(meta.get("tool_dependencies")),
        "mcp_dependencies": normalize_string_list(meta.get("mcp_dependencies")),
        "skill_dependencies": normalize_string_list(meta.get("skill_dependencies")),
    }
