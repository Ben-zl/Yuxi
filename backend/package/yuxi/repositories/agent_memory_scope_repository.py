"""ReMe 长期记忆 scope catalog 的 PostgreSQL 访问边界。"""

from datetime import UTC, date, datetime

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.models_business import AgentMemoryScope
from yuxi.utils.datetime_utils import utc_now


class AgentMemoryScopeRepository:
    """维护用户和 Agent 组合 scope 的路径标识与 Dream 状态。"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get(self, uid: str, agent_slug: str) -> AgentMemoryScope | None:
        """读取一个精确 scope。"""
        result = await self.db.execute(
            select(AgentMemoryScope).where(
                AgentMemoryScope.uid == uid,
                AgentMemoryScope.agent_slug == agent_slug,
            ),
        )
        return result.scalar_one_or_none()

    async def ensure(self, uid: str, agent_slug: str, workspace_id: str) -> AgentMemoryScope:
        """幂等创建 scope catalog，不把尚未写入的 reply 记作已有记忆。"""
        now = utc_now()
        await self.db.execute(
            insert(AgentMemoryScope)
            .values(
                uid=uid,
                agent_slug=agent_slug,
                workspace_id=workspace_id,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_nothing(index_elements=["uid", "agent_slug"]),
        )
        record = await self.get(uid, agent_slug)
        if record is None:
            raise RuntimeError("Memory scope catalog 创建后无法读取")
        if record.workspace_id != workspace_id:
            raise ValueError("Memory scope workspace_id 与确定性标识不一致")
        record.updated_at = now
        await self.db.flush()
        return record

    async def mark_memory_at(self, record: AgentMemoryScope, memory_at: datetime) -> None:
        """在实际卡片存在时推进最近记忆时间。"""
        memory_at = self._as_utc(memory_at)
        if record.last_memory_at is None or memory_at > self._as_utc(record.last_memory_at):
            record.last_memory_at = memory_at
        record.updated_at = utc_now()
        await self.db.flush()

    async def list_all(self) -> list[AgentMemoryScope]:
        """按 scope 稳定顺序列出 catalog。"""
        result = await self.db.execute(
            select(AgentMemoryScope).order_by(AgentMemoryScope.uid, AgentMemoryScope.agent_slug),
        )
        return list(result.scalars().all())

    async def list_for_agent(self, agent_slug: str) -> list[AgentMemoryScope]:
        """列出某 Agent 在全部用户下的 scope。"""
        result = await self.db.execute(
            select(AgentMemoryScope).where(AgentMemoryScope.agent_slug == agent_slug),
        )
        return list(result.scalars().all())

    async def list_for_user(self, uid: str) -> list[AgentMemoryScope]:
        """列出某用户的全部 scope。"""
        result = await self.db.execute(select(AgentMemoryScope).where(AgentMemoryScope.uid == uid))
        return list(result.scalars().all())

    async def set_dream_result(
        self,
        record: AgentMemoryScope,
        *,
        attempted_at: datetime,
        status: str,
        dream_date: date | None = None,
        error: str | None = None,
    ) -> None:
        """更新一次 Dream 尝试结果；失败不推进完成日期。"""
        record.dream_attempted_at = attempted_at
        record.dream_status = status
        record.dream_error = error
        if status == "completed" and dream_date is not None:
            record.last_dream_date = dream_date
        record.updated_at = utc_now()
        await self.db.flush()

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        """把旧 naive 时间按 UTC 解释，并统一为 aware UTC。"""
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    async def delete(self, uid: str, agent_slug: str) -> None:
        """删除一个 scope catalog，目录清理由调用方先完成。"""
        await self.db.execute(
            delete(AgentMemoryScope).where(
                AgentMemoryScope.uid == uid,
                AgentMemoryScope.agent_slug == agent_slug,
            ),
        )
