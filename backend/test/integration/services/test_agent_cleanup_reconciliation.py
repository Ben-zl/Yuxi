"""Agent 删除后长期记忆补偿的真实 PostgreSQL 验证。"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from yuxi.agentscope.client import AgentScopeServiceClient, AgentScopeServiceError
from yuxi.agentscope.memory import memory_scope_identity
from yuxi.repositories.agent_repository import AgentRepository
from yuxi.services.agent_cleanup_service import (
    delete_agent_with_memory_cleanup,
    reconcile_deleted_agent_memories,
)
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import Agent, AgentMemoryScope, User

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest_asyncio.fixture(autouse=True)
async def reset_postgres_manager_between_event_loops():
    """避免全局 asyncpg pool 跨 pytest event loop 复用。"""
    await pg_manager.reset()
    pg_manager.initialize()
    yield
    await pg_manager.reset()


class FailingMemoryClient:
    """稳定注入一次 AgentScope 不可用。"""

    async def clear_memory_agent(self, _uid: str, _agent_slug: str) -> None:
        raise AgentScopeServiceError("unavailable", status_code=503)


async def test_deleted_agent_memory_is_durable_retried_and_slug_isolated():
    """失败后 catalog 保留、slug 隔离，真实 AgentScope 重试后再释放 slug。"""
    suffix = uuid.uuid4().hex[:10]
    uid = f"pytest_cleanup_{suffix}"
    agent_slug = f"pytest-cleanup-{suffix}"
    created_slugs: list[str] = []

    try:
        async with pg_manager.get_async_session_context() as db:
            db.add(User(username=uid, uid=uid, password_hash="test-only", role="user"))
            await db.flush()
            agent = await AgentRepository(db).create(
                name="cleanup source",
                slug=agent_slug,
                backend_id="ChatbotAgent",
                created_by=uid,
            )
            created_slugs.append(agent.slug)

        async with pg_manager.get_async_session_context() as db:
            db.add(
                AgentMemoryScope(
                    uid=uid,
                    agent_slug=agent_slug,
                    workspace_id=memory_scope_identity(uid, agent_slug),
                    last_memory_at=datetime.now(UTC),
                )
            )

        async with pg_manager.get_async_session_context() as db:
            agent = await db.scalar(select(Agent).where(Agent.slug == agent_slug))
            result = await delete_agent_with_memory_cleanup(
                db=db,
                agent=agent,
                caller_uid=uid,
                client=FailingMemoryClient(),
            )

        assert result.cleanup_pending is True
        async with pg_manager.get_async_session_context() as db:
            pending_agent = await db.scalar(select(Agent).where(Agent.slug == agent_slug))
            assert pending_agent is not None and pending_agent.deletion_pending_at is not None
            assert await db.scalar(
                select(AgentMemoryScope.workspace_id).where(AgentMemoryScope.agent_slug == agent_slug)
            ) == memory_scope_identity(uid, agent_slug)
            replacement = await AgentRepository(db).create(
                name="cleanup replacement",
                slug=agent_slug,
                backend_id="ChatbotAgent",
                created_by=uid,
            )
            created_slugs.append(replacement.slug)

        assert replacement.slug == f"{agent_slug}-2"
        cleared = await reconcile_deleted_agent_memories(
            client=AgentScopeServiceClient(os.getenv("AGENTSCOPE_BASE_URL", "http://agentscope:8100"))
        )

        assert agent_slug in cleared
        async with pg_manager.get_async_session_context() as db:
            assert (
                await db.scalar(select(AgentMemoryScope.workspace_id).where(AgentMemoryScope.agent_slug == agent_slug))
                is None
            )
            reused = await AgentRepository(db).create(
                name="cleanup reused",
                slug=agent_slug,
                backend_id="ChatbotAgent",
                created_by=uid,
            )
            created_slugs.append(reused.slug)

        assert reused.slug == agent_slug
    finally:
        async with pg_manager.get_async_session_context() as db:
            await db.execute(delete(AgentMemoryScope).where(AgentMemoryScope.agent_slug == agent_slug))
            if created_slugs:
                await db.execute(delete(Agent).where(Agent.slug.in_(created_slugs)))
            await db.execute(delete(User).where(User.uid == uid))


async def test_pending_deletion_claim_rotates_failed_head_batch():
    """首批 50 个长期失败时，第 51 个 tombstone 下一轮仍能被领取。"""
    prefix = f"pytest-claim-{uuid.uuid4().hex[:8]}"
    slugs = [f"{prefix}-{index:02d}" for index in range(51)]
    now = datetime.now(UTC).replace(tzinfo=None)
    try:
        async with pg_manager.get_async_session_context() as db:
            db.add_all(
                [
                    Agent(
                        slug=slug,
                        backend_id="ChatbotAgent",
                        name=slug,
                        config_json={},
                        share_config={},
                        pics=[],
                        is_default=False,
                        is_subagent=False,
                        deletion_pending_at=now,
                        updated_at=now,
                    )
                    for slug in slugs
                ]
            )

        async with pg_manager.get_async_session_context() as db:
            first = await AgentRepository(db).claim_pending_deletions(limit=50)
        async with pg_manager.get_async_session_context() as db:
            second = await AgentRepository(db).claim_pending_deletions(limit=50)

        assert len(first) == 50
        assert second[0].slug == slugs[-1]
    finally:
        async with pg_manager.get_async_session_context() as db:
            await db.execute(delete(Agent).where(Agent.slug.in_(slugs)))
