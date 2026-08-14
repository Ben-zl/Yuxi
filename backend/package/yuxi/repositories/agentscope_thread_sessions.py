"""线程 ↔ agentscope session 映射的数据访问层。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.models_business import AgentScopeThreadSession


async def get_thread_session(
    db: AsyncSession, *, uid: str, thread_id: str
) -> AgentScopeThreadSession | None:
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
