"""WeKnora 部门 workspace 映射仓储。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_knowledge import WeknoraDepartmentWorkspace


class WeknoraWorkspaceRepository:
    async def get_by_department(self, department_id: int) -> WeknoraDepartmentWorkspace | None:
        """按部门 ID 读取 workspace 映射;每部门至多一行。"""

        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(WeknoraDepartmentWorkspace).where(WeknoraDepartmentWorkspace.department_id == int(department_id))
            )
            return result.scalar_one_or_none()

    async def create(self, data: dict[str, Any]) -> WeknoraDepartmentWorkspace:
        """新建映射;department_id 唯一冲突抛 IntegrityError(并发开通收敛)。"""

        row = WeknoraDepartmentWorkspace(**data)
        async with pg_manager.get_async_session_context() as session:
            session.add(row)
        return row

    async def replace_for_department(self, department_id: int, data: dict[str, Any]) -> WeknoraDepartmentWorkspace:
        """以新开通结果覆盖该部门映射;旧 workspace 由调用方记录残留供人工清理。"""

        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(WeknoraDepartmentWorkspace).where(WeknoraDepartmentWorkspace.department_id == int(department_id))
            )
            row = result.scalar_one_or_none()
            if row is None:
                row = WeknoraDepartmentWorkspace(department_id=int(department_id))
                session.add(row)
            for key, value in data.items():
                setattr(row, key, value)
        return row

    async def update_status(self, department_id: int, status: str) -> None:
        """仅更新映射状态(如自检失败标记待核对)。"""

        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(WeknoraDepartmentWorkspace).where(WeknoraDepartmentWorkspace.department_id == int(department_id))
            )
            row = result.scalar_one_or_none()
            if row is not None:
                row.status = status
