"""把 AgentScope 会话上下文解析为唯一的 Yuxi 运行时投影。"""

from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.agentscope.config_projection import RuntimeProjection, project_runtime
from yuxi.repositories.agentscope_thread_sessions import (
    get_thread_session_by_agentscope_context,
)


async def resolve_runtime_projection(
    db: AsyncSession,
    storage,
    *,
    user_id: str,
    agent_id: str,
    session_id: str,
) -> RuntimeProjection:
    """解析当前会话对应的线程投影；Team worker 继承 leader 的资源投影。"""
    mapping = await get_thread_session_by_agentscope_context(
        db,
        uid=user_id,
        agentscope_agent_id=agent_id,
        agentscope_session_id=session_id,
    )
    if mapping is None:
        session = await storage.get_session(user_id, agent_id, session_id)
        if session is None or session.team_id is None:
            raise ValueError("AgentScope 会话不存在有效的 Yuxi 线程映射")

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
        agent_slug=mapping.agent_slug,
        model_spec=mapping.model_spec,
    )
