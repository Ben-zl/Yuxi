"""部门 repository 的事务行为测试。"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.repositories.department_repository import DepartmentDeletionConflict, DepartmentRepository
from yuxi.storage.postgres.models_business import (
    APIKey,
    AgentMemoryScope,
    Base,
    Department,
    DepartmentMembership,
    User,
)
from yuxi.utils.auth_utils import AuthUtils

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


@pytest_asyncio.fixture()
async def department_session():
    """创建包含默认部门和待删除部门的 SQLite 会话。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        default_department = Department(name="默认部门")
        deleted_department = Department(name="待删除部门")
        session.add_all([default_department, deleted_department])
        await session.flush()
        user = User(
            username="Department User",
            uid="department_user",
            password_hash="$argon2id$placeholder",
            role="user",
            department_id=deleted_department.id,
        )
        session.add(user)
        await session.flush()
        session.add(
            DepartmentMembership(
                user_id=user.id, department_id=deleted_department.id, role="user"
            )
        )
        _secret, key_hash, key_prefix = AuthUtils.generate_api_key()
        api_key = APIKey(
            key_hash=key_hash,
            key_prefix=key_prefix,
            name="department key",
            user_id=user.id,
            department_id=deleted_department.id,
            created_by=str(user.id),
        )
        session.add(api_key)
        session.add(
            AgentMemoryScope(
                uid=user.uid,
                agent_slug="repo-agent",
                workspace_id="ws-repo",
                maintenance_department_id=deleted_department.id,
            )
        )
        await session.commit()
        yield session, default_department, deleted_department, user, api_key
    await engine.dispose()


async def test_delete_empty_department_retains_account_and_revokes_bindings(department_session):
    """删除边界：只删部门与成员关系，账号保留、旧指针清空、Key 撤销、Memory 解绑。"""
    session, default_department, deleted_department, user, api_key = department_session

    await DepartmentRepository(session).delete_empty_department(deleted_department.id)

    assert user.is_deleted == 0
    assert await session.scalar(select(User.id).where(User.id == user.id)) == user.id
    assert user.department_id is None  # 不迁移默认部门
    assert (
        await session.scalar(
            select(DepartmentMembership).where(
                DepartmentMembership.department_id == deleted_department.id
            )
        )
        is None
    )
    assert await session.get(Department, deleted_department.id) is None
    assert await session.get(Department, default_department.id) is not None
    key_result = await session.execute(select(APIKey).where(APIKey.id == api_key.id))
    persisted_key = key_result.scalar_one()
    assert persisted_key.is_enabled is False
    assert persisted_key.revoked_at is not None
    assert persisted_key.department_id is None
    scope = await session.scalar(
        select(AgentMemoryScope).where(AgentMemoryScope.uid == user.uid)
    )
    assert scope.maintenance_department_id is None


async def test_delete_empty_department_blocked_by_channel_binding(department_session):
    """存在绑定引用时抛出冲突且不删除部门。"""
    from yuxi.storage.postgres.models_business import AgentScopeChannelBinding

    session, _default, deleted_department, user, _key = department_session
    session.add(
        AgentScopeChannelBinding(
            id="binding-1",
            owner_uid=user.uid,
            department_id=deleted_department.id,
            agent_slug="repo-agent",
            name="repo 通道",
            channel_type="wps_xiezuo",
            app_id="app",
            encrypted_app_secret="cipher",
            allow_from=[],
            group_reply_policy="mention",
            enabled=True,
            sync_status="pending",
            created_by=user.uid,
            updated_by=user.uid,
        )
    )
    await session.commit()

    with pytest.raises(DepartmentDeletionConflict):
        await DepartmentRepository(session).delete_empty_department(deleted_department.id)

    assert await session.get(Department, deleted_department.id) is not None
