"""AgentScope Channel binding 数据访问层。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.models_business import AgentScopeChannelBinding


class AgentScopeChannelBindingRepository:
    """封装 WPS Channel binding 的持久化查询。"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_all(self) -> list[AgentScopeChannelBinding]:
        """按创建时间返回全部 binding。"""
        result = await self.db.execute(
            select(AgentScopeChannelBinding).order_by(
                AgentScopeChannelBinding.created_at.desc(),
            ),
        )
        return list(result.scalars().all())

    async def get(self, binding_id: str) -> AgentScopeChannelBinding | None:
        """按 Yuxi binding ID 查询。"""
        return await self.db.get(AgentScopeChannelBinding, binding_id)

    async def create(self, **values) -> AgentScopeChannelBinding:
        """创建待同步 binding intent。"""
        binding = AgentScopeChannelBinding(**values)
        self.db.add(binding)
        await self.db.flush()
        return binding

    async def delete(self, binding: AgentScopeChannelBinding) -> None:
        """删除已经完成远端清理的 binding。"""
        await self.db.delete(binding)
        await self.db.flush()
