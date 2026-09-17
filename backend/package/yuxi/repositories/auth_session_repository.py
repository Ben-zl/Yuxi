"""交互登录会话数据访问层 - Repository"""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import AuthSession


class AuthSessionRepository:
    """登录会话查询与写入；事务由调用方 service 拥有。"""

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

    async def get(self, session_id: str, *, for_update: bool = False) -> AuthSession | None:
        """按 ID 查询登录会话，可选行锁。"""
        async with self._session() as session:
            stmt = select(AuthSession).where(AuthSession.id == session_id)
            if for_update:
                stmt = stmt.with_for_update()
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def create(
        self,
        user_id: int,
        active_department_id: int | None,
        expires_at: datetime,
    ) -> AuthSession:
        """创建一条登录会话，ID 为 UUID 字符串。"""
        auth_session = AuthSession(
            id=str(uuid.uuid4()),
            user_id=user_id,
            active_department_id=active_department_id,
            revision=0,
            expires_at=expires_at,
        )
        async with self._session() as session:
            session.add(auth_session)
            await session.flush()
            return auth_session

    async def revoke(self, session_id: str, *, revoked_at: datetime) -> None:
        """标记会话撤销，允许幂等重试。"""
        async with self._session() as session:
            await session.execute(
                update(AuthSession)
                .where(AuthSession.id == session_id, AuthSession.revoked_at.is_(None))
                .values(revoked_at=revoked_at)
            )
            await session.flush()
