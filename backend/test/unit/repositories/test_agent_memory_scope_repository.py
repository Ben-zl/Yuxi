"""Agent memory scope catalog 删除屏障测试。"""

from unittest.mock import AsyncMock

import pytest

from yuxi.repositories.agent_memory_scope_repository import AgentMemoryScopeRepository


@pytest.mark.asyncio
async def test_ensure_rejects_deleted_or_pending_agent():
    """没有活动 Agent 行时旧 Run 不能重建同 slug memory scope。"""
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=None)
    repo = AgentMemoryScopeRepository(db)

    with pytest.raises(RuntimeError, match="正在删除"):
        await repo.ensure("user-1", "retired-agent", "workspace-1")

    db.execute.assert_not_awaited()
