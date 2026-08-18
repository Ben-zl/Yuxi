"""线程 ↔ agentscope session 映射的数据访问层。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.models_business import AgentScopeThreadSession


async def get_thread_session(db: AsyncSession, *, uid: str, thread_id: str) -> AgentScopeThreadSession | None:
    """按用户与线程取映射记录。"""
    result = await db.execute(
        select(AgentScopeThreadSession).where(
            AgentScopeThreadSession.uid == uid,
            AgentScopeThreadSession.thread_id == thread_id,
        )
    )
    return result.scalar_one_or_none()


async def create_thread_session(
    db: AsyncSession,
    *,
    uid: str,
    thread_id: str,
    agent_slug: str,
    model_spec: str,
    agentscope_agent_id: str,
    agentscope_credential_id: str,
    agentscope_session_id: str,
) -> AgentScopeThreadSession:
    """写入映射事实；uid+thread 冲突时抛 IntegrityError（调用方保证先查后写）。"""
    record = AgentScopeThreadSession(
        uid=uid,
        thread_id=thread_id,
        agent_slug=agent_slug,
        model_spec=model_spec,
        agentscope_agent_id=agentscope_agent_id,
        agentscope_credential_id=agentscope_credential_id,
        agentscope_session_id=agentscope_session_id,
    )
    db.add(record)
    await db.flush()
    return record


async def update_thread_session_model(
    db: AsyncSession,
    record: AgentScopeThreadSession,
    *,
    model_spec: str,
    agentscope_credential_id: str,
) -> AgentScopeThreadSession:
    """在 AgentScope 会话更新成功后同步模型映射事实。"""
    record.model_spec = model_spec
    record.agentscope_credential_id = agentscope_credential_id
    await db.flush()
    return record


async def delete_thread_session(db: AsyncSession, record: AgentScopeThreadSession) -> None:
    """删除已完成远端清理的线程映射。"""
    await db.delete(record)
    await db.flush()


async def get_thread_session_by_agentscope_agent(
    db: AsyncSession, *, agentscope_agent_id: str
) -> AgentScopeThreadSession | None:
    """按 agentscope agent_id 反查映射（extra_agent_tools 工厂使用）。"""
    result = await db.execute(
        select(AgentScopeThreadSession).where(AgentScopeThreadSession.agentscope_agent_id == agentscope_agent_id)
    )
    return result.scalar_one_or_none()


async def get_thread_session_by_agentscope_context(
    db: AsyncSession,
    *,
    uid: str,
    agentscope_agent_id: str,
    agentscope_session_id: str,
) -> AgentScopeThreadSession | None:
    """按用户、Agent 与 Session 精确反查线程映射。"""
    result = await db.execute(
        select(AgentScopeThreadSession).where(
            AgentScopeThreadSession.uid == uid,
            AgentScopeThreadSession.agentscope_agent_id == agentscope_agent_id,
            AgentScopeThreadSession.agentscope_session_id == agentscope_session_id,
        )
    )
    return result.scalar_one_or_none()
