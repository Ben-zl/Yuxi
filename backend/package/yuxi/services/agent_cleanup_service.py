"""Agent 删除与 AgentScope 长期记忆补偿清理用例。"""

from __future__ import annotations

import os
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.agentscope.client import AgentScopeServiceClient, AgentScopeServiceError
from yuxi.repositories.agent_memory_scope_repository import AgentMemoryScopeRepository
from yuxi.repositories.agent_repository import AgentRepository
from yuxi.repositories.agent_task_repository import AgentTaskRepository
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import (
    AGENT_RUN_TERMINAL_STATUSES,
    Agent,
    AgentRun,
    AgentRunRequest,
)
from yuxi.utils.logging_config import logger

MEMORY_RECONCILIATION_CALLER_UID = "system"


class AgentDeletionBlocked(Exception):
    """表示 Agent 仍被未归档任务引用。"""

    def __init__(self, referencing_count: int) -> None:
        self.referencing_count = referencing_count
        super().__init__(f"Agent is referenced by {referencing_count} active task(s)")


@dataclass(frozen=True)
class AgentDeletionResult:
    """描述本地 Agent 删除后的外部清理状态。"""

    cleanup_pending: bool


def _memory_client() -> AgentScopeServiceClient:
    """创建短超时的内部 AgentScope Memory 客户端。"""
    return AgentScopeServiceClient(os.getenv("AGENTSCOPE_BASE_URL", "http://agentscope:8100"), timeout=5.0)


async def _has_active_writers(db: AsyncSession, agent_slug: str) -> bool:
    """检查仍可能向该 AgentScope memory 写入的请求或 Run。"""
    active_runs = await db.scalar(
        select(func.count(AgentRun.id)).where(
            AgentRun.agent_slug == agent_slug,
            AgentRun.status.notin_(AGENT_RUN_TERMINAL_STATUSES),
        )
    )
    active_requests = await db.scalar(
        select(func.count(AgentRunRequest.id)).where(
            AgentRunRequest.agent_slug == agent_slug,
            AgentRunRequest.status == "queued",
        )
    )
    return bool(active_runs or active_requests)


async def _try_finalize_pending_agent(agent_slug: str, client: AgentScopeServiceClient) -> bool:
    """无活动 writer 时清理 memory，并在数据库回读为空后物理删除 tombstone。"""
    async with pg_manager.get_async_session_context() as db:
        agent = await db.scalar(
            select(Agent).where(
                Agent.slug == agent_slug,
                Agent.deletion_pending_at.is_not(None),
            )
        )
        if agent is None or await _has_active_writers(db, agent_slug):
            return False

    await client.clear_memory_agent(MEMORY_RECONCILIATION_CALLER_UID, agent_slug)

    async with pg_manager.get_async_session_context() as db:
        agent = await db.scalar(
            select(Agent).where(Agent.slug == agent_slug, Agent.deletion_pending_at.is_not(None)).with_for_update()
        )
        if agent is None or await _has_active_writers(db, agent_slug):
            return False
        if await AgentMemoryScopeRepository(db).list_for_agent(agent_slug):
            return False
        await db.delete(agent)
    return True


async def delete_agent_with_memory_cleanup(
    *,
    db: AsyncSession,
    agent: Agent,
    caller_uid: str,
    client: AgentScopeServiceClient | None = None,
) -> AgentDeletionResult:
    """隐藏本地 Agent 并建立持久删除屏障，再尽力完成外部清理。"""
    del caller_uid
    task_repo = AgentTaskRepository(db)
    pending_agent = await AgentRepository(db).mark_deletion_pending(agent.id)
    if pending_agent is None:
        return AgentDeletionResult(cleanup_pending=False)
    referencing_count = await task_repo.count_active_references(agent.id)
    if referencing_count:
        await db.rollback()
        raise AgentDeletionBlocked(referencing_count)

    await task_repo.detach_agent_references(agent.id)
    await db.commit()

    try:
        completed = await _try_finalize_pending_agent(pending_agent.slug, client or _memory_client())
    except AgentScopeServiceError as exc:
        logger.warning(
            "AgentScope memory cleanup deferred for deleted agent %s: status=%s",
            pending_agent.slug,
            exc.status_code,
        )
        completed = False
    return AgentDeletionResult(cleanup_pending=not completed)


async def reconcile_deleted_agent_memories(
    client: AgentScopeServiceClient | None = None,
    *,
    limit: int = 50,
) -> list[str]:
    """重试持久 Agent tombstone，并返回本轮最终删除的 slug。"""
    async with pg_manager.get_async_session_context() as db:
        agents = await AgentRepository(db).claim_pending_deletions(limit=limit)
    memory_client = client or _memory_client()
    cleared: list[str] = []
    for agent in agents:
        try:
            if await _try_finalize_pending_agent(agent.slug, memory_client):
                cleared.append(agent.slug)
        except AgentScopeServiceError as exc:
            logger.warning(
                "AgentScope pending agent reconciliation deferred for %s: status=%s",
                agent.slug,
                exc.status_code,
            )
    return cleared
