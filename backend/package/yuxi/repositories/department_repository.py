"""部门数据访问层 - Repository"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from sqlalchemy import exists, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import (
    AgentMemoryScope,
    AgentRun,
    AgentRunRequest,
    AgentScopeChannelBinding,
    AgentTask,
    APIKey,
    AuthSession,
    CLIAuthSession,
    Department,
    DepartmentMembership,
    TaskExecution,
    User,
)
from yuxi.utils.datetime_utils import utc_now_naive


class DepartmentDeletionConflict(ValueError):
    """部门仍存在部门权限上下文引用，不能按旧路径删除。"""


@dataclass(frozen=True)
class DepartmentDeletionResult:
    """部门删除结果。"""

    name: str
    migrated_user_count: int


class DepartmentRepository:
    """部门数据访问层"""

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

    async def get_by_id(self, id: int) -> Department | None:
        """根据 ID 获取部门"""
        async with self._session() as session:
            result = await session.execute(select(Department).where(Department.id == id))
            return result.scalar_one_or_none()

    async def get_name_by_id(self, id: int) -> str | None:
        """根据 ID 获取部门名称。"""
        async with self._session() as session:
            result = await session.execute(select(Department.name).where(Department.id == id))
            return result.scalar_one_or_none()

    async def get_with_user_count(self, id: int) -> dict[str, Any] | None:
        """获取部门及其未删除用户数量。"""
        async with self._session() as session:
            result = await session.execute(select(Department).where(Department.id == id))
            department = result.scalar_one_or_none()
            if department is None:
                return None
            count_result = await session.execute(
                select(func.count(User.id)).where(User.department_id == id, User.is_deleted == 0)
            )
            return {**department.to_dict(), "user_count": count_result.scalar() or 0}

    async def get_by_name(self, name: str) -> Department | None:
        """根据名称获取部门"""
        async with self._session() as session:
            result = await session.execute(select(Department).where(Department.name == name))
            return result.scalar_one_or_none()

    async def list_departments(self) -> list[Department]:
        """获取所有部门列表"""
        async with self._session() as session:
            result = await session.execute(select(Department).order_by(Department.created_at.desc()))
            return list(result.scalars().all())

    async def list_with_user_count(self) -> list[dict[str, Any]]:
        """获取所有部门列表，包含用户数量"""
        async with self._session() as session:
            result = await session.execute(select(Department).order_by(Department.created_at.desc()))
            departments = result.scalars().all()

            department_list = []
            for dep in departments:
                user_count_result = await session.execute(
                    select(func.count(User.id)).where(User.department_id == dep.id, User.is_deleted == 0)
                )
                user_count = user_count_result.scalar()
                dep_dict = dep.to_dict()
                dep_dict["user_count"] = user_count
                department_list.append(dep_dict)

            return department_list

    async def create(self, data: dict[str, Any]) -> Department:
        """创建部门"""
        async with self._session() as session:
            department = Department(**data)
            session.add(department)
            await session.flush()
            await session.refresh(department)
        return department

    async def update(self, id: int, data: dict[str, Any]) -> Department | None:
        """更新部门"""
        async with self._session() as session:
            result = await session.execute(select(Department).where(Department.id == id))
            department = result.scalar_one_or_none()
            if department is None:
                return None
            for key, value in data.items():
                if key != "id":
                    setattr(department, key, value)
            await session.flush()
            await session.refresh(department)
        return department

    async def delete(self, id: int) -> bool:
        """删除部门"""
        async with self._session() as session:
            result = await session.execute(select(Department).where(Department.id == id))
            department = result.scalar_one_or_none()
            if department is None:
                return False
            await session.delete(department)
            await session.flush()
        return True

    async def delete_and_migrate_users(
        self, id: int, *, default_department_id: int = 1
    ) -> DepartmentDeletionResult | None:
        """迁移部门用户、删除关联 API Key，并原子删除部门。

        部门成员等新引用存在时抛出 DepartmentDeletionConflict，不产生任何写入；
        无新引用时保留旧删除语义，最终删除策略由部门边界决策接管。
        """
        async with self._session() as session:
            # 先锁部门行，与新增引用写入使用一致的锁序，防止检查后并发插入
            result = await session.execute(select(Department).where(Department.id == id).with_for_update())
            department = result.scalar_one_or_none()
            if department is None:
                return None

            reference = await self._find_context_reference(session, id)
            if reference is not None:
                raise DepartmentDeletionConflict(f"部门仍存在{reference}引用，不能删除")

            user_result = await session.execute(select(User).where(User.department_id == id))
            users = list(user_result.scalars().all())
            for user in users:
                user.department_id = default_department_id

            await session.execute(
                update(APIKey)
                .where(APIKey.department_id == id)
                .values(is_enabled=False, revoked_at=utc_now_naive(), department_id=None)
            )
            await session.delete(department)
            await session.flush()
            return DepartmentDeletionResult(name=department.name, migrated_user_count=len(users))

    @staticmethod
    async def _find_context_reference(session: AsyncSession, department_id: int) -> str | None:
        """返回任意一类部门权限上下文引用的说明；无引用时为 None。"""
        checks = (
            ("部门成员关系", DepartmentMembership.department_id),
            ("登录会话活动部门", AuthSession.active_department_id),
            ("CLI 批准部门", CLIAuthSession.approved_department_id),
            ("Memory 维护绑定", AgentMemoryScope.maintenance_department_id),
            ("运行请求", AgentRunRequest.department_id),
            ("运行", AgentRun.department_id),
            ("定时任务", AgentTask.department_id),
            ("任务执行", TaskExecution.department_id),
            ("Channel 绑定", AgentScopeChannelBinding.department_id),
        )
        for label, column in checks:
            has_reference = await session.scalar(select(exists().where(column == department_id)))
            if has_reference:
                return label
        return None

    async def count_users(self, id: int) -> int:
        """统计部门用户数量"""
        async with self._session() as session:
            result = await session.execute(
                select(func.count(User.id)).where(User.department_id == id, User.is_deleted == 0)
            )
            return result.scalar() or 0

    async def exists_by_name(self, name: str) -> bool:
        """检查部门名称是否存在"""
        async with self._session() as session:
            result = await session.execute(select(Department.id).where(Department.name == name))
            return result.scalar_one_or_none() is not None
