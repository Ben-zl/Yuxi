"""部门成员关系数据访问层 - Repository"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import Department, DepartmentMembership


class DepartmentMembershipRepository:
    """部门成员关系查询与写入；事务由调用方 service 拥有。"""

    def __init__(self, db_session: AsyncSession | None = None):
        self.db_session = db_session

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[AsyncSession]:
        """复用请求会话，未注入时创建独立事务会话。"""
        if self.db_session is not None:
            yield self.db_session
            return
        async with pg_manager.get_async_session_context() as session:
            yield session

    async def get(
        self,
        user_id: int,
        department_id: int,
        *,
        for_update: bool = False,
    ) -> DepartmentMembership | None:
        """查询单条成员关系，可选行锁。"""
        async with self._session() as session:
            stmt = select(DepartmentMembership).where(
                DepartmentMembership.user_id == user_id,
                DepartmentMembership.department_id == department_id,
            )
            if for_update:
                stmt = stmt.with_for_update()
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def list_for_user(self, user_id: int) -> list[DepartmentMembership]:
        """列出账号的全部成员关系，按部门 ID 排序。"""
        async with self._session() as session:
            result = await session.execute(
                select(DepartmentMembership)
                .where(DepartmentMembership.user_id == user_id)
                .order_by(DepartmentMembership.department_id)
            )
            return list(result.scalars().all())

    async def list_for_department(
        self,
        department_id: int,
        *,
        offset: int = 0,
        limit: int = 20,
    ) -> dict:
        """分页列出部门成员，返回 {items, total}。"""
        async with self._session() as session:
            total = await session.scalar(
                select(func.count())
                .select_from(DepartmentMembership)
                .where(DepartmentMembership.department_id == department_id)
            )
            result = await session.execute(
                select(DepartmentMembership)
                .where(DepartmentMembership.department_id == department_id)
                .order_by(DepartmentMembership.user_id)
                .offset(offset)
                .limit(limit)
            )
            return {"items": list(result.scalars().all()), "total": int(total or 0)}

    async def add(self, user_id: int, department_id: int, *, role: str = "user") -> DepartmentMembership:
        """插入成员关系；先锁部门行并验证存在（与部门删除一致锁序），重复或非法角色由数据库约束拒绝。"""
        membership = DepartmentMembership(user_id=user_id, department_id=department_id, role=role)
        async with self._session() as session:
            locked = await session.execute(
                select(Department.id).where(Department.id == department_id).with_for_update()
            )
            if locked.scalar_one_or_none() is None:
                raise ValueError("部门不存在")
            session.add(membership)
            await session.flush()
            return membership

    async def remove(self, user_id: int, department_id: int) -> None:
        """删除单条成员关系。"""
        async with self._session() as session:
            await session.execute(
                delete(DepartmentMembership).where(
                    DepartmentMembership.user_id == user_id,
                    DepartmentMembership.department_id == department_id,
                )
            )
            await session.flush()
