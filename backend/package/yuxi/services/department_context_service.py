"""部门权限上下文与登录会话切换用例。

认证边界消费不可变 DepartmentContext；部门与成员事实实时来自 PostgreSQL，
不缓存进凭证。事务由调用方拥有（router 注入 db session）。
"""

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.repositories.auth_session_repository import AuthSessionRepository
from yuxi.storage.postgres.models_business import AuthSession, Department, DepartmentMembership, User
from yuxi.utils.datetime_utils import utc_now_naive


class DepartmentContextError(Exception):
    """部门上下文用例错误基类。"""


class AccountUnavailableError(DepartmentContextError):
    """账号不存在、已删除或凭证失效。"""


class AccountLockedError(DepartmentContextError):
    """账号处于登录锁定状态。"""


class DepartmentNotFoundError(DepartmentContextError):
    """目标部门不存在（切换场景区分 404）。"""


class DepartmentAccessDeniedError(DepartmentContextError):
    """账号无目标部门的有效成员关系。"""


class DepartmentRevisionConflictError(DepartmentContextError):
    """会话 revision 与期望值不一致（并发切换）。"""


@dataclass(frozen=True)
class DepartmentContext:
    """单次请求/运行使用的不可变权限上下文。"""

    id: int
    uid: str
    username: str
    account_role: str
    department_id: int | None
    department_name: str | None
    role: str
    session_id: str | None
    revision: int


async def resolve_department_context(
    db: AsyncSession,
    *,
    user_id: int,
    department_id: int | None,
    session_id: str | None = None,
    revision: int = 0,
    strict: bool = False,
) -> DepartmentContext:
    """按账号与目标部门解析有效权限上下文。

    strict=True 用于切换：部门不存在抛 404 语义错误、非成员抛 403 语义错误；
    默认宽松解析把不可用部门折叠为无部门上下文（role=user，超管恒 superadmin）。
    """
    result = await db.execute(select(User).where(User.id == user_id, User.is_deleted == 0))
    user = result.scalar_one_or_none()
    if user is None:
        raise AccountUnavailableError("账号不存在或已删除")
    if user.is_login_locked():
        raise AccountLockedError("登录被锁定，请稍后重试")

    account_role = "superadmin" if user.role == "superadmin" else "user"
    effective_role = "superadmin" if account_role == "superadmin" else "user"
    resolved_department_id: int | None = None
    resolved_department_name: str | None = None

    if department_id is not None:
        # strict（提交/切换）路径与删除部门共用行锁：部门被并发删除时写入在锁内失效
        dept_query = select(Department).where(Department.id == department_id)
        if strict:
            dept_query = dept_query.with_for_update()
        dept_result = await db.execute(dept_query)
        department = dept_result.scalar_one_or_none()
        if department is None:
            if strict:
                raise DepartmentNotFoundError("目标部门不存在")
        elif account_role == "superadmin":
            resolved_department_id, resolved_department_name = department.id, department.name
        else:
            membership_result = await db.execute(
                select(DepartmentMembership).where(
                    DepartmentMembership.user_id == user.id,
                    DepartmentMembership.department_id == department_id,
                )
            )
            membership = membership_result.scalar_one_or_none()
            if membership is None:
                if strict:
                    raise DepartmentAccessDeniedError("非目标部门成员")
            else:
                resolved_department_id = department.id
                resolved_department_name = department.name
                effective_role = membership.role

    return DepartmentContext(
        id=user.id,
        uid=user.uid,
        username=user.username,
        account_role=account_role,
        department_id=resolved_department_id,
        department_name=resolved_department_name,
        role=effective_role,
        session_id=session_id,
        revision=revision,
    )


async def list_available_departments(db: AsyncSession, *, user_id: int) -> list[dict]:
    """列出账号可切换的部门及实时角色；超管返回全部存在部门。"""
    user_result = await db.execute(select(User).where(User.id == user_id, User.is_deleted == 0))
    user = user_result.scalar_one_or_none()
    if user is None:
        raise AccountUnavailableError("账号不存在或已删除")

    if user.role == "superadmin":
        dept_result = await db.execute(select(Department).order_by(Department.id))
        return [{"id": row.id, "name": row.name, "role": "superadmin"} for row in dept_result.scalars().all()]

    result = await db.execute(
        select(DepartmentMembership, Department)
        .join(Department, Department.id == DepartmentMembership.department_id)
        .where(DepartmentMembership.user_id == user.id)
        .order_by(DepartmentMembership.department_id)
    )
    return [
        {"id": department.id, "name": department.name, "role": membership.role}
        for membership, department in result.all()
    ]


async def create_session_for_login(db: AsyncSession, *, user_id: int, ttl_seconds: int) -> AuthSession:
    """登录成功后创建会话：活动部门取可用部门中 ID 最小项，无可用部门为 NULL。"""
    departments = await list_available_departments(db, user_id=user_id)
    active = departments[0]["id"] if departments else None
    return await AuthSessionRepository(db).create(
        user_id=user_id,
        active_department_id=active,
        expires_at=utc_now_naive() + timedelta(seconds=ttl_seconds),
    )


async def switch_department(
    db: AsyncSession,
    *,
    session_id: str,
    department_id: int,
    expected_revision: int,
) -> DepartmentContext:
    """事务内锁会话并切换活动部门；成功才提交，失败不改原会话。"""
    session = await AuthSessionRepository(db).get(session_id, for_update=True)
    if session is None or session.revoked_at is not None:
        raise DepartmentAccessDeniedError("登录会话无效")
    if session.revision != expected_revision:
        raise DepartmentRevisionConflictError("部门上下文已变更，请刷新后重试")

    context = await resolve_department_context(
        db,
        user_id=session.user_id,
        department_id=department_id,
        session_id=session.id,
        revision=session.revision + 1,
        strict=True,
    )
    session.active_department_id = context.department_id
    session.revision = context.revision
    await db.commit()
    return context


async def invalidate_stale_active_department(
    db: AsyncSession, session: AuthSession, *, expected_department_id: int
) -> bool:
    """会话活动部门失效时置空并递增 revision，使旧 revision 写请求立即失配。

    条件更新只在该会话仍指向读取时的旧部门（expected_department_id）时生效：
    期间若用户已成功切换到其他部门（或已被并发失效处理），本次不覆盖新状态，
    防止旧请求的失效处理吞掉刚提交的切换结果。返回是否真正执行了失效。
    """
    result = await db.execute(
        update(AuthSession)
        .where(
            AuthSession.id == session.id,
            AuthSession.active_department_id == expected_department_id,
        )
        .values(active_department_id=None, revision=AuthSession.revision + 1)
    )
    await db.refresh(session)
    return bool(result.rowcount)


async def revoke_auth_session(db: AsyncSession, *, session_id: str, user_id: int) -> None:
    """撤销当前登录会话；幂等，不影响同账号其他登录。"""
    session = await AuthSessionRepository(db).get(session_id)
    if session is None or session.user_id != user_id:
        raise AccountUnavailableError("登录会话不存在")
    await AuthSessionRepository(db).revoke(session_id, revoked_at=utc_now_naive())
    await db.commit()
