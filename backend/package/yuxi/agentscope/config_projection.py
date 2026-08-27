"""运行时配置投影的统一入口（迁移工单 03）。

从 yuxi 库读取一个线程运行所需的全部配置（Agent 定义、模型供应商与凭据、
Skill、MCP、子智能体模板、知识库），投影为 agentscope 运行时对象载荷。
后续工单（工具/Skills/MCP/Team）一律消费本入口，不得各自直连 yuxi 表。

LITE 模式下裁剪知识库相关投影，纯聊天能力不受影响。
"""

from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.agents.skills.service import list_accessible_skills
from yuxi.agents.toolkits.service import get_tool_metadata
from yuxi.utils.paths import VIRTUAL_PATH_PREFIX
from yuxi.agentscope.projection import (
    agent_context,
    is_lite_mode,
    project_agent_request,
    project_chat_model,
    project_subagent_template,
    split_model_spec,
)
from yuxi.repositories.agent_repository import AgentRepository
from yuxi.repositories.user_repository import UserRepository
from yuxi.models.providers.repository import get_model_provider
from yuxi.storage.postgres.models_business import Agent, ModelProvider


@dataclass
class RuntimeProjection:
    """一次线程运行所需的全部 agentscope 运行时对象投影。"""

    agent_slug: str
    model_spec: str
    agent_request: dict
    credential_data: dict
    chat_model_config: dict
    skill_slugs: list[str] = field(default_factory=list)
    skills: list[dict] = field(default_factory=list)
    tool_slugs: list[str] | None = None
    skill_tool_dependencies: dict[str, list[str]] = field(default_factory=dict)
    skill_mcp_dependencies: dict[str, list[str]] = field(default_factory=dict)
    skill_mcp_servers: dict[str, list[dict]] = field(default_factory=dict)
    mcp_servers: list[dict] = field(default_factory=list)
    knowledge_slugs: list[str] | None = None
    subagent_templates: list[dict] = field(default_factory=list)


def adapt_prompt_paths_for_agentscope(prompt: str) -> str:
    """把 Yuxi 虚拟文件路径映射到 AgentScope 的持久 workspace。"""
    return prompt.replace(VIRTUAL_PATH_PREFIX, "/workspace")


def build_agentscope_system_prompt(system_prompt: str) -> str:
    """构造主、子智能体共享的平台提示词，并映射 AgentScope 文件路径。"""
    from types import SimpleNamespace

    from yuxi.agents.buildin.chatbot.prompt import build_prompt_with_context

    return adapt_prompt_paths_for_agentscope(build_prompt_with_context(SimpleNamespace(system_prompt=system_prompt)))


async def _load_user(db: AsyncSession, uid: str):
    """读取运行用户，缺失即显式失败。"""
    user = await UserRepository().get_by_uid_with_db(db, uid)
    if user is None:
        raise ValueError(f"用户 {uid} 不存在")
    return user


async def _load_agent(db: AsyncSession, agent_slug: str, user) -> Agent:
    """按 slug 读取用户可见的主智能体，缺失即显式失败。"""
    agent = await AgentRepository(db).get_visible_by_slug(slug=agent_slug, user=user, kind="any")
    if agent is None:
        raise ValueError(f"智能体 {agent_slug} 不存在")
    return agent


async def _load_provider(db: AsyncSession, provider_id: str) -> ModelProvider:
    """按 provider_id 读取模型供应商，缺失或未启用即显式失败。"""
    provider = await get_model_provider(db, provider_id)
    if provider is None:
        raise ValueError(f"模型供应商 {provider_id} 不存在")
    if not provider.is_enabled:
        raise ValueError(f"模型供应商 {provider_id} 未启用")
    return provider


async def project_runtime(
    db: AsyncSession,
    *,
    uid: str,
    agent_slug: str,
    model_spec: str | None = None,
    thread_id: str | None = None,
) -> RuntimeProjection:
    """统一投影入口：读取 yuxi 配置并产出该线程运行的全部运行时对象。"""
    user = await _load_user(db, uid)
    agent = await _load_agent(db, agent_slug, user)
    context = agent_context(agent)

    spec = model_spec or context.get("model")
    if not spec:
        # 与旧栈一致：请求与智能体均未指定模型时，回落系统默认对话模型
        # （前端新线程首条消息不携带 model_spec，无模型智能体依赖此兜底）
        from yuxi.config import config as sys_config

        spec = sys_config.default_model or ""
    if not spec:
        raise ValueError(f"智能体 {agent_slug} 未配置模型（context.model 为空）")
    provider_id, model_id = split_model_spec(spec)
    provider = await _load_provider(db, provider_id)

    credential_data, chat_model_config = project_chat_model(provider, model_id)
    agent_request = project_agent_request(agent)
    if thread_id:
        from yuxi.agents.context import build_agent_input_context

        platform_prompt = build_agentscope_system_prompt(context.get("system_prompt") or "")
        input_context = await build_agent_input_context(
            {"system_prompt": platform_prompt},
            thread_id=thread_id,
            uid=uid,
        )
        agent_request["system_prompt"] = input_context["system_prompt"]

    projection = RuntimeProjection(
        agent_slug=agent_slug,
        model_spec=spec,
        agent_request=agent_request,
        credential_data=credential_data,
        chat_model_config=chat_model_config,
    )

    # 资源列表：None 表示全部可用（BaseContext 语义）
    accessible_skills = await list_accessible_skills(db, user)
    accessible_by_slug = {item.slug: item for item in accessible_skills}
    configured_skills = context.get("skills")
    selected_slugs = list(accessible_by_slug) if configured_skills is None else list(configured_skills)
    inaccessible_skills = [slug for slug in selected_slugs if slug not in accessible_by_slug]
    if inaccessible_skills:
        raise ValueError(f"智能体引用了当前用户不可访问的 Skill: {', '.join(inaccessible_skills)}")
    resolved_slugs: list[str] = []
    visiting: set[str] = set()

    def visit_skill(slug: str) -> None:
        """按依赖优先顺序解析 Skill 闭包，并拒绝循环与越权依赖。"""
        if slug in resolved_slugs:
            return
        if slug in visiting:
            raise ValueError(f"Skill 依赖存在循环: {slug}")
        item = accessible_by_slug.get(slug)
        if item is None:
            raise ValueError(f"Skill 依赖不存在、未启用或不可访问: {slug}")
        visiting.add(slug)
        for dependency in item.skill_dependencies:
            visit_skill(dependency)
        visiting.remove(slug)
        resolved_slugs.append(slug)

    for slug in selected_slugs:
        visit_skill(slug)

    projection.skill_slugs = resolved_slugs
    projection.skills = [
        {
            "slug": item.slug,
            "name": item.name,
            "source_dir": str(item.source_dir),
        }
        for item in (accessible_by_slug[slug] for slug in resolved_slugs)
    ]
    projection.skill_tool_dependencies = {
        slug: list(accessible_by_slug[slug].tool_dependencies) for slug in resolved_slugs
    }
    projection.skill_mcp_dependencies = {
        slug: list(accessible_by_slug[slug].mcp_dependencies) for slug in resolved_slugs
    }

    configured_tools = context.get("tools")
    available_tools = {item["slug"] for item in get_tool_metadata()}
    available_dependency_tools = available_tools | {
        "list_kbs",
        "query_kb",
        "find_kb_document",
        "open_kb_document",
        "get_mindmap",
        "search_file",
        "download_kb_file",
    }
    if configured_tools is None:
        projection.tool_slugs = None
    else:
        if not isinstance(configured_tools, list):
            raise ValueError("智能体工具配置必须是 slug 列表")
        projection.tool_slugs = list(dict.fromkeys(configured_tools))
        unavailable_tools = [slug for slug in projection.tool_slugs if slug not in available_tools]
        if unavailable_tools:
            raise ValueError("智能体引用了不存在或不可用的工具: " + ", ".join(unavailable_tools))
    unavailable_skill_tools = sorted(
        {
            slug
            for dependencies in projection.skill_tool_dependencies.values()
            for slug in dependencies
            if slug not in available_dependency_tools
        }
    )
    if unavailable_skill_tools:
        raise ValueError("Skill 引用了不存在或不可用的工具: " + ", ".join(unavailable_skill_tools))
    from yuxi.agents.mcp.service import load_enabled_mcp_server_configs

    configured_mcps = context.get("mcps")
    if configured_mcps is None:
        selected_mcps = None
    else:
        if not isinstance(configured_mcps, list) or any(
            not isinstance(slug, str) or not slug.strip() for slug in configured_mcps
        ):
            raise ValueError("智能体 MCP 配置只能包含非空 slug")
        selected_mcps = list(dict.fromkeys(slug.strip() for slug in configured_mcps))
    dependency_mcps = list(
        dict.fromkeys(slug for dependencies in projection.skill_mcp_dependencies.values() for slug in dependencies)
    )
    requested_mcps = None if selected_mcps is None else list(dict.fromkeys([*selected_mcps, *dependency_mcps]))
    loaded_mcp_configs = await load_enabled_mcp_server_configs(names=requested_mcps, db=db)
    mcp_configs = {
        slug: config
        for slug, config in loaded_mcp_configs.items()
        if config.get("transport") in {"stdio", "sse", "streamable_http"}
    }
    if selected_mcps is not None:
        unavailable_mcps = [slug for slug in selected_mcps if slug not in mcp_configs]
        if unavailable_mcps:
            raise ValueError("智能体引用了不存在、未启用或不允许的 MCP: " + ", ".join(unavailable_mcps))
        mcp_slugs = selected_mcps
    else:
        mcp_slugs = list(mcp_configs)
    projection.mcp_servers = [{"slug": slug, **mcp_configs[slug]} for slug in mcp_slugs]
    unavailable_skill_mcps = [slug for slug in dependency_mcps if slug not in mcp_configs]
    if unavailable_skill_mcps:
        raise ValueError("Skill 引用了不存在、未启用或不允许的 MCP: " + ", ".join(unavailable_skill_mcps))
    projection.skill_mcp_servers = {
        skill_slug: [{"slug": mcp_slug, **mcp_configs[mcp_slug]} for mcp_slug in dependencies]
        for skill_slug, dependencies in projection.skill_mcp_dependencies.items()
    }

    configured_knowledge = context.get("knowledges")
    projection.knowledge_slugs = (
        [] if is_lite_mode() else None if configured_knowledge is None else list(configured_knowledge)
    )

    visible_subagents = await AgentRepository(db).list_visible_subagents(user=user)
    configured_subagents = context.get("subagents")
    if configured_subagents is not None:
        visible_by_slug = {item.slug: item for item in visible_subagents}
        inaccessible_subagents = [slug for slug in configured_subagents if slug not in visible_by_slug]
        if inaccessible_subagents:
            raise ValueError("智能体引用了当前用户不可访问的子智能体: " + ", ".join(inaccessible_subagents))
        visible_subagents = [visible_by_slug[slug] for slug in configured_subagents]
    projection.subagent_templates = [project_subagent_template(row) for row in visible_subagents]
    for template in projection.subagent_templates:
        template["system_prompt_template"] = build_agentscope_system_prompt(template["system_prompt_template"])
    return projection
