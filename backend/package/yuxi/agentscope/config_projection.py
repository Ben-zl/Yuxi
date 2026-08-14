"""运行时配置投影的统一入口（迁移工单 03）。

从 yuxi 库读取一个线程运行所需的全部配置（Agent 定义、模型供应商与凭据、
Skill、MCP、子智能体模板、知识库），投影为 agentscope 运行时对象载荷。
后续工单（工具/Skills/MCP/Team）一律消费本入口，不得各自直连 yuxi 表。

LITE 模式下裁剪知识库相关投影，纯聊天能力不受影响。
"""

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.agentscope.projection import (
    agent_context,
    is_lite_mode,
    project_agent_request,
    project_chat_model,
    project_subagent_template,
    split_model_spec,
)
from yuxi.storage.postgres.models_business import Agent, ModelProvider, Skill


@dataclass
class RuntimeProjection:
    """一次线程运行所需的全部 agentscope 运行时对象投影。"""

    agent_slug: str
    model_spec: str
    agent_request: dict
    credential_data: dict
    chat_model_config: dict
    skill_slugs: list[str] = field(default_factory=list)
    mcp_server_names: list[str] = field(default_factory=list)
    knowledge_slugs: list[str] = field(default_factory=list)
    subagent_templates: list[dict] = field(default_factory=list)


async def _load_agent(db: AsyncSession, agent_slug: str) -> Agent:
    """按 slug 读取智能体定义，缺失即显式失败。"""
    result = await db.execute(select(Agent).where(Agent.slug == agent_slug))
    agent = result.scalar_one_or_none()
    if agent is None:
        raise ValueError(f"智能体 {agent_slug} 不存在")
    return agent


async def _load_provider(db: AsyncSession, provider_id: str) -> ModelProvider:
    """按 provider_id 读取模型供应商，缺失或未启用即显式失败。"""
    result = await db.execute(
        select(ModelProvider).where(ModelProvider.provider_id == provider_id)
    )
    provider = result.scalar_one_or_none()
    if provider is None:
        raise ValueError(f"模型供应商 {provider_id} 不存在")
    if not provider.is_enabled:
        raise ValueError(f"模型供应商 {provider_id} 未启用")
    return provider


async def _visible_skill_slugs(db: AsyncSession) -> list[str]:
    """全部可见 Skill（激活门控由 Skills 工单的渐进披露机制处理）。"""
    result = await db.execute(select(Skill.slug))
    return [row[0] for row in result.all()]


async def project_runtime(
    db: AsyncSession,
    *,
    uid: str,
    agent_slug: str,
    model_spec: str | None = None,
) -> RuntimeProjection:
    """统一投影入口：读取 yuxi 配置并产出该线程运行的全部运行时对象。"""
    agent = await _load_agent(db, agent_slug)
    context = agent_context(agent)

    spec = model_spec or context.get("model")
    if not spec:
        raise ValueError(f"智能体 {agent_slug} 未配置模型（context.model 为空）")
    provider_id, model_id = split_model_spec(spec)
    provider = await _load_provider(db, provider_id)

    credential_data, chat_model_config = project_chat_model(provider, model_id)
    projection = RuntimeProjection(
        agent_slug=agent_slug,
        model_spec=spec,
        agent_request=project_agent_request(agent),
        credential_data=credential_data,
        chat_model_config=chat_model_config,
    )

    # 资源列表：None 表示全部可用（BaseContext 语义）
    skills = context.get("skills")
    projection.skill_slugs = (
        await _visible_skill_slugs(db) if skills is None else list(skills)
    )
    mcps = context.get("mcps")
    projection.mcp_server_names = [] if mcps is None else list(mcps)
    projection.knowledge_slugs = [] if is_lite_mode() else list(context.get("knowledges") or [])

    subagent_result = await db.execute(select(Agent).where(Agent.is_subagent.is_(True)))
    projection.subagent_templates = [
        project_subagent_template(row) for row in subagent_result.scalars().all()
    ]
    return projection
