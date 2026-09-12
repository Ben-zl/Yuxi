"""Agent 授权使用的 Skill 资源目录查询。"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.agents.skills.metadata import parse_skill_dir_metadata
from yuxi.agents.skills.repository import SkillRepository
from yuxi.permissions import ResourcePermission, normalize_permission_config, resolve_skill_permission
from yuxi.storage.postgres.models_business import User
from yuxi.workspace.paths import user_workspace_dir


@dataclass(frozen=True, slots=True)
class AuthorizableSkill:
    """保存期授权所需的最小 Skill 元数据。"""

    slug: str
    created_by: str | None
    share_config: dict | None
    skill_dependencies: list[str]
    mcp_dependencies: list[str]


def _personal_skill_slugs(uid: str) -> list[str]:
    """读取有效个人 Skill slug，不加载业务 Service。"""
    root = user_workspace_dir(uid) / "agents" / "skills"
    if not root.is_dir() or root.is_symlink():
        return []
    slugs: list[str] = []
    for entry in sorted(root.iterdir(), key=lambda path: path.name):
        if entry.is_symlink() or not entry.is_dir():
            continue
        if any(child.is_symlink() for child in entry.rglob("*")):
            continue
        try:
            metadata = parse_skill_dir_metadata(entry)
        except (OSError, UnicodeError, ValueError):
            continue
        if metadata["slug"] != entry.name:
            continue
        slugs.append(entry.name)
    return slugs


async def list_authorizable_skills(db: AsyncSession, *, user: User) -> list[AuthorizableSkill]:
    """合并当前用户可见的共享与个人 Skill，个人同名项优先。"""
    if user is None:
        raise PermissionError("Skill 查询需要当前用户授权上下文")
    effective: dict[str, AuthorizableSkill] = {}
    for item in await SkillRepository(db).list_enabled():
        if resolve_skill_permission(user, item) == ResourcePermission.NONE:
            continue
        effective[item.slug] = AuthorizableSkill(
            slug=item.slug,
            created_by=item.created_by,
            share_config=normalize_permission_config(item.share_config),
            skill_dependencies=list(item.skill_dependencies or []),
            mcp_dependencies=list(item.mcp_dependencies or []),
        )
    for slug in _personal_skill_slugs(str(user.uid)):
        effective[slug] = AuthorizableSkill(
            slug=slug,
            created_by=str(user.uid),
            share_config=None,
            skill_dependencies=[],
            mcp_dependencies=[],
        )
    return list(effective.values())
