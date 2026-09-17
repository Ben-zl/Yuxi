from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from server.routers import auth_router as auth_router_module
from server.routers.auth_router import delete_user
from server.routers.user_router import APIKeyCreate, create_api_key
from server.utils.auth_middleware import verify_api_key
from yuxi.repositories import user_repository as user_repository_module
from yuxi.repositories.user_repository import UserRepository
from yuxi.agentscope.client import AgentScopeServiceError
from yuxi.services.department_context_service import DepartmentContext
from yuxi.storage.postgres.models_business import APIKey, Base, Department, DepartmentMembership, User
from yuxi.utils.auth_utils import AuthUtils

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _FakeApiKeySession:
    def __init__(self, api_key: APIKey):
        self.api_key = api_key
        self.execute_calls = 0

    async def execute(self, _statement):
        self.execute_calls += 1
        return _ScalarResult(self.api_key)


@pytest_asyncio.fixture()
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        dept_a = Department(name="Dept A")
        dept_b = Department(name="Dept B")
        superadmin = User(
            username="Super Admin",
            uid="superadmin",
            password_hash="$argon2id$placeholder",
            role="superadmin",
            department=dept_a,
        )
        dept_b_admin = User(
            username="Dept B Admin",
            uid="dept_b_admin",
            password_hash="$argon2id$placeholder",
            role="admin",
            department=dept_b,
        )
        regular_user = User(
            username="Regular",
            uid="regular",
            password_hash="$argon2id$placeholder",
            role="user",
            department=dept_a,
        )
        deleted_user = User(
            username="Deleted",
            uid="deleted",
            password_hash="$argon2id$placeholder",
            role="user",
            department=dept_a,
            is_deleted=1,
        )
        db.add_all([dept_a, dept_b, superadmin, dept_b_admin, regular_user, deleted_user])
        await db.flush()
        db.add(DepartmentMembership(user_id=regular_user.id, department_id=dept_a.id, role="user"))
        await db.commit()
        for item in [dept_a, dept_b, superadmin, dept_b_admin, regular_user, deleted_user]:
            await db.refresh(item)
        yield {
            "db": db,
            "dept_a": dept_a,
            "dept_b": dept_b,
            "superadmin": superadmin,
            "dept_b_admin": dept_b_admin,
            "regular_user": regular_user,
            "deleted_user": deleted_user,
        }
    await engine.dispose()


async def test_api_key_rejects_deleted_bound_user_without_department_or_superadmin_fallback(session):
    db = session["db"]
    secret, key_hash, key_prefix = AuthUtils.generate_api_key()
    api_key = APIKey(
        key_hash=key_hash,
        key_prefix=key_prefix,
        name="deleted user key",
        user_id=session["deleted_user"].id,
        department_id=session["dept_b"].id,
        created_by=str(session["deleted_user"].id),
    )
    db.add(api_key)
    await db.commit()

    user, verified_key = await verify_api_key(secret, db)

    assert user is None
    assert verified_key is None


async def test_api_key_without_user_binding_is_rejected_before_department_mapping(session):
    secret, key_hash, key_prefix = AuthUtils.generate_api_key()
    api_key = APIKey(
        key_hash=key_hash,
        key_prefix=key_prefix,
        name="department key",
        user_id=None,
        department_id=session["dept_b"].id,
        created_by=str(session["superadmin"].id),
    )
    fake_db = _FakeApiKeySession(api_key)

    user, verified_key = await verify_api_key(secret, fake_db)

    assert user is None
    assert verified_key is None
    assert fake_db.execute_calls == 1


def _context(
    user: User, department_id: int | None, *, account_role: str = "user", role: str = "user"
) -> DepartmentContext:
    """构造与认证边界同构的创建者上下文。"""
    return DepartmentContext(
        id=user.id,
        uid=str(user.uid),
        username=user.username,
        account_role=account_role,
        department_id=department_id,
        department_name=None,
        role=role,
        session_id=None,
        revision=0,
    )


async def test_create_api_key_rejects_non_member_binding(session):
    """锁内守卫：非超管创建者绑定的部门必须是 key 主体的成员部门。"""
    db = session["db"]

    with pytest.raises(HTTPException) as exc:
        await create_api_key(
            APIKeyCreate(request_id="wrong-department", name="wrong department"),
            current_user=_context(session["regular_user"], session["dept_b"].id),
            db=db,
        )

    assert exc.value.status_code == 403


async def test_create_api_key_allows_current_user_department(session):
    db = session["db"]

    response = await create_api_key(
        APIKeyCreate(request_id="own-department", name="own department"),
        current_user=_context(session["regular_user"], session["dept_a"].id),
        db=db,
    )

    assert response.api_key.user_id == session["regular_user"].id
    assert response.api_key.department_id == session["dept_a"].id
    assert response.secret.startswith(response.api_key.key_prefix)


async def test_create_api_key_superadmin_binds_any_existing_department(session):
    """超管验证部门存在即可，为目标用户显式绑定任意存在部门。"""
    db = session["db"]

    response = await create_api_key(
        APIKeyCreate(
            request_id="superadmin-bind",
            name="superadmin bind",
            user_id=session["regular_user"].id,
        ),
        current_user=_context(
            session["superadmin"],
            session["dept_b"].id,
            account_role="superadmin",
            role="superadmin",
        ),
        db=db,
    )

    assert response.api_key.user_id == session["regular_user"].id
    assert response.api_key.department_id == session["dept_b"].id


async def test_delete_user_disables_owned_api_keys(session, monkeypatch):
    db = session["db"]
    cleanup_calls = []

    class FakeAgentScopeClient:
        async def clear_memory_user(self, caller_uid, target_uid):
            cleanup_calls.append((caller_uid, target_uid))

    monkeypatch.setattr(
        auth_router_module,
        "AgentScopeServiceClient",
        lambda _base_url: FakeAgentScopeClient(),
    )
    _secret, key_hash, key_prefix = AuthUtils.generate_api_key()
    api_key = APIKey(
        key_hash=key_hash,
        key_prefix=key_prefix,
        name="owned key",
        user_id=session["regular_user"].id,
        created_by=str(session["regular_user"].id),
    )
    db.add(api_key)
    await db.commit()
    await db.refresh(api_key)

    result = await delete_user(session["regular_user"].id, None, session["superadmin"], db)
    await db.refresh(api_key)

    assert result["success"] is True
    assert api_key.is_enabled is False
    assert cleanup_calls == [(session["superadmin"].uid, session["regular_user"].uid)]


async def test_delete_user_stops_when_memory_cleanup_fails(session, monkeypatch):
    """用户记忆清理失败时保持用户和 API Key 原状。"""
    db = session["db"]

    class FailingAgentScopeClient:
        async def clear_memory_user(self, _caller_uid, _target_uid):
            raise AgentScopeServiceError("cleanup failed", status_code=500)

    monkeypatch.setattr(
        auth_router_module,
        "AgentScopeServiceClient",
        lambda _base_url: FailingAgentScopeClient(),
    )

    with pytest.raises(HTTPException) as exc:
        await delete_user(session["regular_user"].id, None, session["superadmin"], db)

    assert exc.value.status_code == 502
    assert session["regular_user"].is_deleted == 0


async def test_user_repository_soft_delete_disables_owned_api_keys(session, monkeypatch):
    db = session["db"]
    _secret, key_hash, key_prefix = AuthUtils.generate_api_key()
    api_key = APIKey(
        key_hash=key_hash,
        key_prefix=key_prefix,
        name="repository owned key",
        user_id=session["regular_user"].id,
        created_by=str(session["regular_user"].id),
    )
    db.add(api_key)
    await db.commit()
    await db.refresh(api_key)

    @asynccontextmanager
    async def fake_session_context():
        yield db
        await db.commit()

    monkeypatch.setattr(user_repository_module.pg_manager, "get_async_session_context", fake_session_context)

    assert await UserRepository().soft_delete(session["regular_user"].id) is True
    await db.refresh(api_key)

    assert api_key.is_enabled is False
