"""Agent 配置引用的模型、Skill 与 MCP 范围授权策略。"""

from __future__ import annotations

from copy import deepcopy

from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.agents.skills.catalog import list_authorizable_skills
from yuxi.models.providers.cache import model_cache
from yuxi.models.providers.repository import (
    get_legacy_model_provider_for_user,
    get_model_provider_for_user,
)
from yuxi.permissions import resource_read_scope_covers_share_config
from yuxi.repositories.mcp_repository import get_authorizable_mcp_server, list_authorizable_mcp_servers
from yuxi.storage.postgres.models_business import User

_AGENT_MODEL_FIELDS = {
    "model": "chat",
    "model_spec": "chat",
    "chat_model": "chat",
    "chat_model_spec": "chat",
    "llm_model": "chat",
    "llm_model_spec": "chat",
    "embedding_model": "embedding",
    "embedding_model_spec": "embedding",
    "embed_model": "embedding",
    "embed_model_spec": "embedding",
    "rerank_model": "rerank",
    "rerank_model_spec": "rerank",
    "reranker_model": "rerank",
    "reranker_model_spec": "rerank",
}


async def authorize_agent_config_resources(
    config_json: dict | None,
    *,
    db: AsyncSession,
    user: User,
    agent_share_config: dict,
    owner_uid: str,
) -> dict:
    """保存前授权 Agent 的模型与 MCP 引用，并写回规范资源 ID。"""
    normalized = deepcopy(config_json) if isinstance(config_json, dict) else {}
    context = normalized.get("context")
    if context is None:
        context = {}
        normalized["context"] = context
    elif not isinstance(context, dict):
        raise ValueError("Agent context 必须是对象")

    for field_name, expected_type in _AGENT_MODEL_FIELDS.items():
        raw_spec = context.get(field_name)
        if raw_spec in (None, ""):
            continue
        if not isinstance(raw_spec, str):
            raise ValueError(f"Agent {field_name} 必须是模型 resource_id spec")
        canonical_spec = model_cache.canonicalize_spec(raw_spec.strip())
        info = model_cache.get_model_info(canonical_spec) if canonical_spec else None
        if info is None or info.model_type != expected_type:
            raise ValueError(f"Agent {field_name} 引用的 {expected_type} 模型不存在、类型错误或旧标识存在歧义")
        if info.resource_id and info.resource_id != info.provider_id:
            provider = await get_model_provider_for_user(db, info.resource_id, user)
        else:
            provider = await get_legacy_model_provider_for_user(db, info.provider_id, user=user)
        if provider is None or not provider.is_enabled:
            raise ValueError(f"Agent {field_name} 引用的模型资源不可访问")
        if not await resource_read_scope_covers_share_config(
            db,
            resource_share_config=provider.share_config,
            target_share_config=agent_share_config,
            owner_uid=owner_uid,
        ):
            raise ValueError(f"Agent {field_name} 引用的模型资源未覆盖智能体完整读取范围")
        context[field_name] = f"{provider.resource_id}:{info.model_id}"

    effective_skills = []
    raw_skills = context.get("skills")
    accessible_skills = {skill.slug: skill for skill in await list_authorizable_skills(db, user=user) if skill.slug}
    selected_skills = (
        list(accessible_skills) if raw_skills is None else _normalize_reference_list(raw_skills, field_name="skills")
    )
    missing_selected = [slug for slug in selected_skills if slug not in accessible_skills]
    if missing_selected:
        raise ValueError("Agent skills 包含不存在、禁用或不可访问的 Skill: " + ", ".join(missing_selected))

    effective_slugs: list[str] = []
    resolved: set[str] = set()
    resolving: set[str] = set()

    def resolve_skill(slug: str) -> None:
        if slug in resolved:
            return
        if slug in resolving:
            raise ValueError(f"Agent Skill 依赖存在循环: {slug}")
        item = accessible_skills.get(slug)
        if item is None:
            raise ValueError(f"Agent Skill 依赖不存在、禁用或不可访问: {slug}")
        resolving.add(slug)
        effective_slugs.append(slug)
        for dependency in _normalize_reference_list(item.skill_dependencies or [], field_name="skill_dependencies"):
            resolve_skill(dependency)
        resolving.remove(slug)
        resolved.add(slug)

    for slug in selected_skills:
        resolve_skill(slug)
    effective_skills = [accessible_skills[slug] for slug in effective_slugs]
    for skill in effective_skills:
        skill_share_config = skill.share_config
        if skill_share_config is None:
            skill_share_config = {
                "version": 2,
                "read_scope": {
                    "access_level": "user",
                    "department_ids": [],
                    "user_uids": [str(skill.created_by or user.uid)],
                },
                "manage_scope": None,
            }
        if not await resource_read_scope_covers_share_config(
            db,
            resource_share_config=skill_share_config,
            target_share_config=agent_share_config,
            owner_uid=owner_uid,
        ):
            raise ValueError(f"Agent Skill 未覆盖智能体完整读取范围: {skill.slug}")
    context["skills"] = selected_skills

    raw_preloads = context.get("preload_skills")
    if raw_preloads is not None:
        preloads = _normalize_reference_list(raw_preloads, field_name="preload_skills")
        invalid_preloads = [slug for slug in preloads if slug not in selected_skills]
        if invalid_preloads:
            raise ValueError("Agent preload_skills 必须来自已选择的 skills: " + ", ".join(invalid_preloads))
        context["preload_skills"] = preloads

    raw_mcps = context.get("mcps")
    if raw_mcps is None:
        mcp_references = [server.resource_id for server in await list_authorizable_mcp_servers(db, user=user)]
    else:
        mcp_references = _normalize_reference_list(raw_mcps, field_name="mcps")
    canonical_mcps: list[str] = []
    for reference in mcp_references:
        server = await get_authorizable_mcp_server(db, reference, user=user)
        if server is None:
            raise ValueError(f"Agent MCP 资源不存在、不可访问或旧 slug 存在歧义: {reference}")
        resource_id = str(server.resource_id or "")
        if not await resource_read_scope_covers_share_config(
            db,
            resource_share_config=server.share_config,
            target_share_config=agent_share_config,
            owner_uid=owner_uid,
        ):
            raise ValueError(f"Agent MCP 资源未覆盖智能体完整读取范围: {reference}")
        if resource_id not in canonical_mcps:
            canonical_mcps.append(resource_id)
    context["mcps"] = canonical_mcps

    for skill in effective_skills:
        for reference in _normalize_reference_list(skill.mcp_dependencies or [], field_name="mcp_dependencies"):
            server = await get_authorizable_mcp_server(db, reference, user=user)
            if server is None:
                raise ValueError(f"Agent Skill MCP 资源不存在、禁用、不可访问或旧 slug 存在歧义: {reference}")
            if not await resource_read_scope_covers_share_config(
                db,
                resource_share_config=server.share_config,
                target_share_config=agent_share_config,
                owner_uid=owner_uid,
            ):
                raise ValueError(f"Agent Skill MCP 资源未覆盖智能体完整读取范围: {reference}")

    return normalized


def _normalize_reference_list(value, *, field_name: str) -> list[str]:
    """规范非空字符串引用列表并保持声明顺序。"""
    if not isinstance(value, list):
        raise ValueError(f"Agent {field_name} 必须是字符串列表")
    normalized: list[str] = []
    for reference in value:
        if not isinstance(reference, str) or not reference.strip():
            raise ValueError(f"Agent {field_name} 包含无效引用")
        reference = reference.strip()
        if reference not in normalized:
            normalized.append(reference)
    return normalized
