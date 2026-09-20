import dataclasses
import hashlib

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from yuxi.services.department_context_service import (
    AccountLockedError,
    AccountUnavailableError,
    DepartmentContext,
    invalidate_stale_active_department,
    resolve_department_context,
)
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import APIKey, AuthSession, User
from yuxi.utils.datetime_utils import utc_now_naive

from yuxi.utils.auth_utils import AuthUtils

# 定义OAuth2密码承载器，指定token URL
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/token", auto_error=False)


# 获取数据库会话（异步版本）
async def get_db():
    async with pg_manager.get_async_session_context() as db:
        yield db


async def verify_api_key(key: str, db: AsyncSession) -> tuple[User | None, APIKey | None]:
    """验证 API Key 并返回关联用户和 APIKey 对象"""
    key_hash = hashlib.sha256(key.encode()).hexdigest()

    result = await db.execute(select(APIKey).filter(APIKey.key_hash == key_hash))
    api_key = result.scalar_one_or_none()

    if api_key is None:
        return None, None

    if not api_key.is_enabled or api_key.revoked_at is not None:
        return None, None

    if api_key.expires_at and utc_now_naive() > api_key.expires_at:
        return None, None

    if not api_key.user_id:
        return None, None

    result = await db.execute(select(User).filter(User.id == api_key.user_id))
    user = result.scalar_one_or_none()
    if user and not user.is_deleted:
        return user, api_key

    return None, None


def _context_exception(exc: Exception) -> HTTPException:
    """把部门上下文用例错误映射为认证边界 HTTP 结论。"""
    if isinstance(exc, AccountLockedError):
        return HTTPException(status_code=status.HTTP_423_LOCKED, detail=str(exc))
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=str(exc),
        headers={"WWW-Authenticate": "Bearer"},
    )


# 获取当前权限上下文（可选登录；无凭证返回 None）
async def get_current_user(
    authorization: str | None = Header(None),
    db: AsyncSession = Depends(get_db),
) -> DepartmentContext | None:
    if authorization is None or not authorization.startswith("Bearer "):
        return None
    token = authorization.split("Bearer ")[1]
    if not token:
        return None

    # API Key：按 key 绑定部门解析上下文（不依赖登录会话）
    if token.startswith("yxkey_"):
        user, api_key_obj = await verify_api_key(token, db)
        if user is None or api_key_obj is None:
            return None
        api_key_obj.last_used_at = utc_now_naive()
        await db.commit()
        try:
            return await resolve_department_context(db, user_id=user.id, department_id=api_key_obj.department_id)
        except (AccountUnavailableError, AccountLockedError) as exc:
            raise _context_exception(exc) from exc

    # 交互 JWT：必须携带会话 sid；旧无 sid 凭证失效
    try:
        payload = AuthUtils.verify_access_token(token)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
            headers={"WWW-Authenticate": "Bearer"},
        )
    user_id = payload.get("sub")
    session_id = payload.get("sid")
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效的凭证",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not isinstance(session_id, str) or not session_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="凭证缺少会话标识，请重新登录",
            headers={"WWW-Authenticate": "Bearer"},
        )

    session_result = await db.execute(select(AuthSession).where(AuthSession.id == session_id))
    session = session_result.scalar_one_or_none()
    if (
        session is None
        or session.user_id != int(user_id)
        or session.revoked_at is not None
        or (session.expires_at and utc_now_naive() > session.expires_at)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="登录会话已失效，请重新登录",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        context = await resolve_department_context(
            db,
            user_id=int(user_id),
            department_id=session.active_department_id,
            session_id=session.id,
            revision=session.revision,
        )
    except (AccountUnavailableError, AccountLockedError) as exc:
        raise _context_exception(exc) from exc

    # 成员失效/部门消失：折叠为无部门并递增 revision，使旧 revision 写请求立即失配；
    # 返回的上下文携带递增后的 revision，保证 /me 立即可用于后续切换/写请求。
    # 条件更新未命中说明期间已有并发切换提交：按会话现值重新解析而非覆盖
    stale_department_id = session.active_department_id
    if context.department_id is None and stale_department_id is not None:
        invalidated = await invalidate_stale_active_department(
            db, session, expected_department_id=stale_department_id
        )
        await db.commit()
        if invalidated:
            context = dataclasses.replace(context, revision=session.revision)
        else:
            session_result = await db.execute(
                select(AuthSession).where(AuthSession.id == session.id).execution_options(populate_existing=True)
            )
            session = session_result.scalar_one()
            try:
                context = await resolve_department_context(
                    db,
                    user_id=int(user_id),
                    department_id=session.active_department_id,
                    session_id=session.id,
                    revision=session.revision,
                )
            except (AccountUnavailableError, AccountLockedError) as exc:
                raise _context_exception(exc) from exc
    return context


# 强制登录（允许无部门）
async def get_authenticated_user(context: DepartmentContext | None = Depends(get_current_user)):
    if context is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="请登录后再访问",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return context


# 强制登录且要求有效部门
async def get_required_user(context: DepartmentContext = Depends(get_authenticated_user)):
    if context.department_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "department_context_invalid", "message": "当前没有有效部门上下文"},
        )
    return context


# 部门级写请求的 revision 守卫：交互会话必须携带当前 revision，防旧页面误写新部门
async def require_department_revision(
    x_department_revision: str | None = Header(None, alias="X-Department-Revision"),
    context: DepartmentContext = Depends(get_required_user),
):
    if context.session_id is None:
        return context  # API Key 固定部门路径不依赖会话 revision
    try:
        provided_revision = int(x_department_revision or "")
    except ValueError:
        provided_revision = None
    if provided_revision is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "department_revision_required", "message": "部门写请求必须携带 X-Department-Revision"},
        )
    if provided_revision != context.revision:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "department_context_stale", "message": "部门上下文已变更，请刷新后重试"},
        )
    return context


# 管理员：全局超管直接放行，否则要求有效部门且部门角色为 admin
async def get_admin_user(current_user: DepartmentContext = Depends(get_authenticated_user)):
    if current_user.account_role == "superadmin":
        return current_user
    if current_user.department_id is None or current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要管理员权限",
        )
    return current_user


# 超级管理员：仅检查全局身份，不要求活动部门
async def get_superadmin_user(current_user: DepartmentContext = Depends(get_authenticated_user)):
    if current_user.account_role != "superadmin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要超级管理员权限",
        )
    return current_user
