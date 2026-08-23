"""把 AgentScope 会话上下文解析为唯一的 Yuxi 运行时投影。"""

import asyncio

from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.agentscope.config_projection import RuntimeProjection, project_runtime
from yuxi.repositories.agentscope_thread_sessions import (
    get_thread_session_by_agentscope_context,
)
from yuxi.repositories.agentscope_team_workers import AgentScopeTeamWorkerRepository


async def sync_runtime_skills(workspace, projection: RuntimeProjection, *, agent_id: str) -> None:
    """将当前 Agent 的 workspace Skill 对齐到运行时投影。"""
    current_skills = {item.name for item in await workspace.list_skills(agent_id=agent_id)}
    desired_skills = {item["slug"]: item["source_dir"] for item in projection.skills}

    for slug in sorted(current_skills - desired_skills.keys()):
        await workspace.remove_skill(slug, agent_id=agent_id)
    for slug in sorted(desired_skills.keys() - current_skills):
        await workspace.add_skill(desired_skills[slug], agent_id=agent_id)


async def resolve_runtime_projection(
    db: AsyncSession,
    storage,
    *,
    user_id: str,
    agent_id: str,
    session_id: str,
) -> RuntimeProjection:
    """解析会话投影；Team worker 使用子智能体资源和父 Run 上下文。"""
    mapping = await get_thread_session_by_agentscope_context(
        db,
        uid=user_id,
        agentscope_agent_id=agent_id,
        agentscope_session_id=session_id,
    )
    runtime_agent_slug: str | None = None
    if mapping is None:
        session = await storage.get_session(user_id, agent_id, session_id)
        if session is None or session.team_id is None:
            raise ValueError("AgentScope 会话不存在有效的 Yuxi 线程映射")

        binding_repo = AgentScopeTeamWorkerRepository(db)
        binding = None
        # AgentCreate 启动 worker 与 leader 中间件落 binding 存在短暂竞态。
        for _ in range(20):
            binding = await binding_repo.get_by_worker_session(
                uid=user_id,
                worker_session_id=session_id,
            )
            if binding is not None:
                break
            await asyncio.sleep(0.1)
        if binding is None:
            raise ValueError("AgentScope Team worker 不存在有效的子智能体绑定")
        runtime_agent_slug = binding.subagent_slug

        team = await storage.get_team(user_id, session.team_id)
        if team is None:
            raise ValueError("AgentScope Team 已不存在，无法解析运行时配置")
        leader_session = await storage.get_session(user_id, "", team.session_id)
        if leader_session is None:
            raise ValueError("AgentScope Team leader 会话不存在，无法解析运行时配置")
        mapping = await get_thread_session_by_agentscope_context(
            db,
            uid=user_id,
            agentscope_agent_id=leader_session.agent_id,
            agentscope_session_id=leader_session.id,
        )
        if mapping is None:
            raise ValueError("AgentScope Team leader 不存在有效的 Yuxi 线程映射")

    return await project_runtime(
        db,
        uid=user_id,
        agent_slug=runtime_agent_slug or mapping.agent_slug,
        model_spec=mapping.model_spec,
        thread_id=mapping.thread_id,
    )
