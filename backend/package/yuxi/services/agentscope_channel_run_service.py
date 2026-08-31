"""AgentScope Channel Run 的 Yuxi 侧运行控制。"""

import os

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.agentscope.client import AgentScopeServiceClient
from yuxi.repositories.agent_run_repository import (
    AgentRunRepository,
    TERMINAL_RUN_STATUSES,
)
from yuxi.repositories.agentscope_thread_sessions import get_thread_session
from yuxi.storage.postgres.models_business import AgentRun


async def cancel_agentscope_channel_run(
    *,
    run: AgentRun,
    current_uid: str,
    db: AsyncSession,
) -> dict:
    """标记 Channel Run 取消并中断对应 AgentScope Session。"""
    if run.status in TERMINAL_RUN_STATUSES:
        return {"run": run.to_dict()}
    mapping = await get_thread_session(
        db,
        uid=current_uid,
        thread_id=run.conversation_thread_id,
    )
    if mapping is None:
        raise HTTPException(status_code=409, detail="Channel Run 缺少 AgentScope Session 映射")

    cancelled = await AgentRunRepository(db).request_cancel(run.id)
    await db.commit()
    client = AgentScopeServiceClient(
        os.getenv("AGENTSCOPE_BASE_URL", "http://agentscope:8100"),
    )
    await client.interrupt_session(
        current_uid,
        mapping.agentscope_agent_id,
        mapping.agentscope_session_id,
    )
    return {"run": cancelled.to_dict() if cancelled else None}
