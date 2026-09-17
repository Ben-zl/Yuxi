"""部门上下文 service 的纯库级行为测试（SQLite 内存库）。"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.services.department_context_service import (
    AccountUnavailableError,
    DepartmentAccessDeniedError,
    DepartmentContext,
    DepartmentNotFoundError,
    DepartmentRevisionConflictError,
    resolve_department_context,
    revoke_auth_session,
    switch_department,
)
from yuxi.storage.postgres.models_business import (
    AuthSession,
    Department,
    DepartmentMembership,
    User,
)
from yuxi.utils.datetime_utils import utc_now_naive


async def _setup_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(User.__table__.create)
        await conn.run_sync(Department.__table__.create)
        await conn.run_sync(DepartmentMembership.__table__.create)
        await conn.run_sync(AuthSession.__table__.create)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        async with session.begin():
            dept_a = Department(id=11, name="A")
            dept_b = Department(id=12, name="B")
            member = User(id=7, username="成员", uid="member", password_hash="x", role="user")
            superadmin = User(id=1, username="超管", uid="root", password_hash="x", role="superadmin")
            session.add_all([dept_a, dept_b, member, superadmin])
            session.add(DepartmentMembership(user_id=7, department_id=11, role="admin"))
            session.add(DepartmentMembership(user_id=7, department_id=12, role="user"))
            session.add(
                AuthSession(
                    id="sess-1",
                    user_id=7,
                    active_department_id=11,
                    revision=0,
                    expires_at=utc_now_naive() + timedelta(hours=1),
                )
            )
    return engine, factory


async def test_resolve_uses_realtime_membership_role():
    engine, factory = await _setup_db()
    try:
        async with factory() as session:
            context = await resolve_department_context(session, user_id=7, department_id=12)
            assert (context.account_role, context.role, context.department_id) == ("user", "user", 12)
            context_a = await resolve_department_context(session, user_id=7, department_id=11)
            assert context_a.role == "admin"
            # 宽松解析：非成员部门折叠为无部门，不抛错
            ghost = Department(id=99, name="幽灵")
            session.add(ghost)
            await session.flush()
            none_ctx = await resolve_department_context(session, user_id=7, department_id=99)
            assert none_ctx.department_id is None and none_ctx.role == "user"
    finally:
        await engine.dispose()


async def test_superadmin_role_is_always_superadmin_without_membership():
    engine, factory = await _setup_db()
    try:
        async with factory() as session:
            context = await resolve_department_context(session, user_id=1, department_id=12)
            assert (context.account_role, context.role, context.department_id, context.department_name) == (
                "superadmin",
                "superadmin",
                12,
                "B",
            )
            no_dept = await resolve_department_context(session, user_id=1, department_id=None)
            assert no_dept.role == "superadmin" and no_dept.department_id is None
    finally:
        await engine.dispose()


async def test_switch_department_strict_errors_and_revision_bump():
    engine, factory = await _setup_db()
    try:
        async with factory() as session:
            with pytest.raises(DepartmentNotFoundError):
                await switch_department(session, session_id="sess-1", department_id=404, expected_revision=0)
            with pytest.raises(DepartmentAccessDeniedError):
                new_dept = Department(id=13, name="C")
                session.add(new_dept)
                await session.flush()
                await switch_department(session, session_id="sess-1", department_id=13, expected_revision=0)
            with pytest.raises(DepartmentRevisionConflictError):
                await switch_department(session, session_id="sess-1", department_id=12, expected_revision=5)
            context = await switch_department(session, session_id="sess-1", department_id=12, expected_revision=0)
            assert (context.department_id, context.role, context.revision) == (12, "user", 1)
    finally:
        await engine.dispose()


async def test_revoke_auth_session_is_idempotent_and_scoped():
    engine, factory = await _setup_db()
    try:
        async with factory() as session:
            session.add(
                AuthSession(
                    id="sess-2",
                    user_id=1,
                    active_department_id=None,
                    revision=0,
                    expires_at=utc_now_naive() + timedelta(hours=1),
                )
            )
            await session.commit()
            await revoke_auth_session(session, session_id="sess-1", user_id=7)
            await revoke_auth_session(session, session_id="sess-1", user_id=7)  # 幂等
            other = await session.get(AuthSession, "sess-2")
            assert other.revoked_at is None  # 不影响其他登录
            revoked = await session.get(AuthSession, "sess-1")
            assert revoked.revoked_at is not None
            with pytest.raises(AccountUnavailableError):
                await revoke_auth_session(session, session_id="sess-1", user_id=1)  # 归属不符
    finally:
        await engine.dispose()


def test_department_context_is_frozen():
    context = DepartmentContext(7, "member", "成员", "user", 11, "A", "admin", None, 0)
    with pytest.raises(Exception):
        context.role = "superadmin"
