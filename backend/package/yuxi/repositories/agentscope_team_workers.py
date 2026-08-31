"""AgentScope Team worker 长期绑定的数据访问层。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.models_business import AgentScopeTeamWorkerBinding


class AgentScopeTeamWorkerRepository:
    """集中维护 worker Session、子线程和 child Run 的唯一映射。"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_worker_session(self, *, uid: str, worker_session_id: str) -> AgentScopeTeamWorkerBinding | None:
        """按用户和 worker Session 查询绑定，跨用户引用不可见。"""
        result = await self.db.execute(
            select(AgentScopeTeamWorkerBinding).where(
                AgentScopeTeamWorkerBinding.uid == str(uid),
                AgentScopeTeamWorkerBinding.worker_session_id == worker_session_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_by_child_run(self, *, uid: str, run_id: str) -> AgentScopeTeamWorkerBinding | None:
        """按当前活动 child Run 查询 worker 绑定。"""
        result = await self.db.execute(
            select(AgentScopeTeamWorkerBinding).where(
                AgentScopeTeamWorkerBinding.uid == str(uid),
                AgentScopeTeamWorkerBinding.active_run_id == run_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_for_parent_run(self, *, uid: str, parent_run_id: str) -> list[AgentScopeTeamWorkerBinding]:
        """列出由父 Run 创建且仍可中断的 worker。"""
        result = await self.db.execute(
            select(AgentScopeTeamWorkerBinding).where(
                AgentScopeTeamWorkerBinding.uid == str(uid),
                AgentScopeTeamWorkerBinding.created_by_run_id == parent_run_id,
                AgentScopeTeamWorkerBinding.runtime_active.is_(True),
            )
        )
        return list(result.scalars().all())

    async def list_for_parent_thread(self, *, uid: str, parent_thread_id: str) -> list[AgentScopeTeamWorkerBinding]:
        """列出父线程持有的全部 worker，用于删除生命周期收口。"""
        result = await self.db.execute(
            select(AgentScopeTeamWorkerBinding).where(
                AgentScopeTeamWorkerBinding.uid == str(uid),
                AgentScopeTeamWorkerBinding.parent_thread_id == parent_thread_id,
            )
        )
        return list(result.scalars().all())

    async def list_active_for_parent_thread(
        self,
        *,
        uid: str,
        parent_thread_id: str,
    ) -> list[AgentScopeTeamWorkerBinding]:
        """列出父线程仍保留运行时的 worker，用于同步会话模型配置。"""
        result = await self.db.execute(
            select(AgentScopeTeamWorkerBinding).where(
                AgentScopeTeamWorkerBinding.uid == str(uid),
                AgentScopeTeamWorkerBinding.parent_thread_id == parent_thread_id,
                AgentScopeTeamWorkerBinding.runtime_active.is_(True),
            )
        )
        return list(result.scalars().all())

    async def list_for_active_runs(self, *, uid: str, run_ids: list[str]) -> list[AgentScopeTeamWorkerBinding]:
        """批量解析待取消 child Run 对应的活动 worker。"""
        if not run_ids:
            return []
        result = await self.db.execute(
            select(AgentScopeTeamWorkerBinding).where(
                AgentScopeTeamWorkerBinding.uid == str(uid),
                AgentScopeTeamWorkerBinding.active_run_id.in_(run_ids),
                AgentScopeTeamWorkerBinding.runtime_active.is_(True),
            )
        )
        return list(result.scalars().all())

    async def deactivate_parent_runtime(self, *, uid: str, parent_thread_id: str) -> list[AgentScopeTeamWorkerBinding]:
        """标记父线程持有的 Team runtime 已清理，保留绑定供历史审计。"""
        bindings = await self.list_for_parent_thread(uid=uid, parent_thread_id=parent_thread_id)
        for binding in bindings:
            binding.runtime_active = False
        await self.db.flush()
        return bindings

    async def set_workspace_id(
        self,
        binding: AgentScopeTeamWorkerBinding,
        *,
        agentscope_workspace_id: str,
    ) -> AgentScopeTeamWorkerBinding:
        """保存 worker Session 分配的权威 Workspace ID。"""
        binding.agentscope_workspace_id = agentscope_workspace_id
        await self.db.flush()
        return binding

    async def create(self, **values) -> AgentScopeTeamWorkerBinding:
        """创建唯一绑定；调用方负责在同一事务内创建子线程和 Run。"""
        binding = AgentScopeTeamWorkerBinding(**values)
        self.db.add(binding)
        await self.db.flush()
        return binding
