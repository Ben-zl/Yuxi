"""部门数据访问层 - Repository"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import cast, delete, exists, func, select, text, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import (
    Agent,
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
    MCPServer,
    ModelProvider,
    Skill,
    TaskExecution,
    User,
)
from yuxi.storage.postgres.models_knowledge import KnowledgeBase, WeknoraDepartmentWorkspace
from yuxi.utils.datetime_utils import utc_now_naive


class DepartmentDeletionConflict(ValueError):
    """部门仍存在资源归属、共享引用或未终态任务，不能删除。"""


# 共享配置（v2 scope JSON）中可能引用部门的资源表
_SHARE_CONFIG_MODELS: tuple[tuple[str, Any], ...] = (
    ("智能体共享", Agent),
    ("技能共享", Skill),
    ("MCP 共享", MCPServer),
    ("模型供应商共享", ModelProvider),
    ("知识库共享", KnowledgeBase),
)


async def lock_share_departments(db: AsyncSession, share_config: dict | None) -> None:
    """共享配置写入前以部门行锁验证引用部门存在。

    与删除部门使用同一锁序，防止“删除检查通过→并发写入引用→部门被删”的孤儿共享引用。
    """
    config = share_config if isinstance(share_config, dict) else {}
    ids: set[int] = set()
    for scope_key in ("read_scope", "manage_scope"):
        scope = config.get(scope_key) or {}
        if scope.get("access_level") == "department":
            ids.update(int(v) for v in scope.get("department_ids") or [])
    if not ids:
        return
    rows = await db.execute(select(Department.id).where(Department.id.in_(ids)).with_for_update())
    found = set(rows.scalars().all())
    if found != ids:
        raise ValueError("共享范围引用的部门不存在")


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

    async def delete_empty_department(self, department_id: int) -> None:
        """按删除边界删除部门；部门不存在时静默返回。

        存在阻断引用时抛出 DepartmentDeletionConflict 且不产生任何写入；成功时只
        删除部门及其成员关系，撤销绑定该部门的凭证（API Key/CLI 批准），解绑 Memory
        维护，把会话当前部门置空并递增 revision。账号与其他部门成员关系保留，
        不迁移到默认部门；资源归属不改变。
        """
        async with self._session() as session:
            # 先锁部门行，与资源/绑定写入使用一致的锁序，防止检查后并发插入引用
            result = await session.execute(
                select(Department).where(Department.id == department_id).with_for_update()
            )
            department = result.scalar_one_or_none()
            if department is None:
                return

            reference = await self._find_blocking_reference(session, department_id)
            if reference is not None:
                raise DepartmentDeletionConflict(f"部门仍存在{reference}引用，不能删除")

            now = utc_now_naive()
            await session.execute(
                delete(DepartmentMembership).where(DepartmentMembership.department_id == department_id)
            )
            await session.execute(
                update(APIKey)
                .where(APIKey.department_id == department_id)
                .values(is_enabled=False, revoked_at=now, department_id=None)
            )
            await session.execute(
                update(CLIAuthSession)
                .where(CLIAuthSession.approved_department_id == department_id)
                .values(approved_department_id=None)
            )
            await session.execute(
                update(AgentMemoryScope)
                .where(AgentMemoryScope.maintenance_department_id == department_id)
                .values(maintenance_department_id=None)
            )
            await session.execute(
                update(AuthSession)
                .where(AuthSession.active_department_id == department_id)
                .values(active_department_id=None, revision=AuthSession.revision + 1)
            )
            # 旧单部门列在 schema 15 前仍带外键：只清空指向，不迁移默认部门；列删除时移除本句
            await session.execute(
                update(User).where(User.department_id == department_id).values(department_id=None)
            )
            await session.delete(department)
            await session.flush()

    @staticmethod
    async def _find_blocking_reference(session: AsyncSession, department_id: int) -> str | None:
        """返回阻断删除的引用类别；无阻断引用时为 None。"""
        # 知识层表属于独立 schema 生命周期：库未初始化知识表时跳过对应检查
        # 探测与后续查询使用同一 search_path 解析，避免限定名探测与查询不一致
        workspace_present = await DepartmentRepository._table_present(session, "weknora_department_workspaces")
        kb_table_present = await DepartmentRepository._table_present(session, "knowledge_bases")
        checks: tuple[tuple[str, Any], ...] = (
            ("Channel 绑定", AgentScopeChannelBinding.department_id == department_id),
            # 运行/请求/执行/任务在 schema 15 移除外键前全量阻断（历史行会触发外键，
            # 故标签用中性词）；外键移除后放宽为仅未终态记录阻断并精确化标签
            ("运行记录", AgentRun.department_id == department_id),
            ("请求记录", AgentRunRequest.department_id == department_id),
            ("任务记录", AgentTask.department_id == department_id),
            ("任务执行记录", TaskExecution.department_id == department_id),
        )
        if workspace_present:
            checks = (
                ("知识库工作区", WeknoraDepartmentWorkspace.department_id == department_id),
                *checks,
            )
        for label, condition in checks:
            if await session.scalar(select(exists().where(condition))):
                return label

        for label, model in _SHARE_CONFIG_MODELS:
            if label == "知识库共享" and not kb_table_present:
                continue
            if await DepartmentRepository._share_config_references(session, model.share_config, department_id):
                return label
        if await DepartmentRepository._share_config_references(session, AgentTask.share_config, department_id):
            return "任务共享"
        return None

    @staticmethod
    async def _table_present(session: AsyncSession, qualified_name: str) -> bool:
        """表是否已在当前库初始化（隔离 schema 测试库可能只建业务表）。"""
        bind = session.get_bind()
        if bind.dialect.name != "postgresql":
            return False
        regclass = await session.scalar(text(f"SELECT to_regclass('{qualified_name}')"))
        return regclass is not None

    @staticmethod
    async def _share_config_references(
        session: AsyncSession, share_column: Any, department_id: int
    ) -> bool:
        """判断任一 read/manage scope 的 department_ids 是否包含目标部门。

        JSON 包含查询依赖 PostgreSQL JSONB；SQLite 单元库不支持该运算，
        由真实 PG 集成测试覆盖共享引用阻断路径。
        """
        bind = session.get_bind()
        if bind.dialect.name != "postgresql":
            return False
        for scope_key in ("read_scope", "manage_scope"):
            condition = cast(share_column, JSONB).contains(
                {scope_key: {"department_ids": [department_id]}}
            )
            if await session.scalar(select(exists().where(condition))):
                return True
        return False

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
