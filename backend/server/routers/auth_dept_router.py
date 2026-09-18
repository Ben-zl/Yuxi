"""
部门管理路由
提供部门的增删改查接口，仅超级管理员可访问
"""

import re
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from server.utils.auth_middleware import (
    get_admin_user,
    get_db,
    get_superadmin_user,
    require_department_revision,
)
from yuxi.repositories.department_repository import DepartmentDeletionConflict, DepartmentRepository
from yuxi.repositories.user_repository import UserRepository
from yuxi.services.department_context_service import DepartmentContext
from yuxi.services.department_membership_service import (
    DepartmentMembershipService,
    MemberConflictError,
    MemberNotFoundError,
)
from yuxi.services.identity_admin_service import (
    IdentityConflictError,
    create_department_with_admin,
    delete_department as delete_department_use_case,
)
from yuxi.services.operation_log_service import log_operation
from yuxi.services.user_identity_service import is_valid_phone_number
from yuxi.storage.postgres.models_business import User

# 创建路由器
department = APIRouter(prefix="/departments", tags=["department"])


# =============================================================================
# === 请求和响应模型 ===
# =============================================================================


class DepartmentCreate(BaseModel):
    """创建部门请求"""

    name: str
    description: str | None = None
    # 必需的管理员信息
    admin_uid: str
    admin_password: str = Field(min_length=8)
    admin_phone: str | None = None


class DepartmentUpdate(BaseModel):
    """更新部门请求"""

    name: str | None = None
    description: str | None = None


class DepartmentResponse(BaseModel):
    """部门响应"""

    id: int
    name: str
    description: str | None = None
    created_at: str
    user_count: int = 0


# =============================================================================
# === 部门管理路由 ===
# =============================================================================


@department.get("", response_model=list[DepartmentResponse])
async def get_departments(current_user: User = Depends(get_admin_user), db: AsyncSession = Depends(get_db)):
    """获取所有部门列表（管理员可访问）"""
    dept_repo = DepartmentRepository(db)
    return await dept_repo.list_with_user_count()


@department.get("/{department_id}", response_model=DepartmentResponse)
async def get_department(
    department_id: int,
    current_user: User = Depends(get_superadmin_user),
    db: AsyncSession = Depends(get_db),
):
    """获取指定部门详情"""
    department = await DepartmentRepository(db).get_with_user_count(department_id)
    if department is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="部门不存在")
    return department


@department.post("", response_model=DepartmentResponse, status_code=status.HTTP_201_CREATED)
async def create_department(
    department_data: DepartmentCreate,
    request: Request,
    current_user: User = Depends(get_superadmin_user),
    db: AsyncSession = Depends(get_db),
):
    """创建新部门，同时创建该部门的管理员"""
    dept_repo = DepartmentRepository(db)
    user_repo = UserRepository(db)

    # 检查部门名称是否已存在
    if await dept_repo.exists_by_name(department_data.name):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="部门名称已存在")

    # 验证管理员 uid 格式
    admin_uid = department_data.admin_uid
    if not re.match(r"^[a-zA-Z0-9_]+$", admin_uid):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="用户ID只能包含字母、数字和下划线",
        )

    if len(admin_uid) < 3 or len(admin_uid) > 20:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="用户ID长度必须在3-20个字符之间",
        )

    # 检查 uid 是否已存在
    if await user_repo.exists_by_uid(admin_uid):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="用户ID已存在",
        )

    # 检查手机号是否已存在（如果提供了）
    admin_phone = department_data.admin_phone
    if admin_phone:
        if not is_valid_phone_number(admin_phone):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="手机号格式不正确")
        if await user_repo.exists_by_phone(admin_phone):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="手机号已存在",
            )

    try:
        created = await create_department_with_admin(
            db,
            name=department_data.name,
            description=department_data.description,
            admin_uid=admin_uid,
            admin_password=department_data.admin_password,
            admin_phone=admin_phone,
            actor_user_id=current_user.id,
            request=request,
        )
    except IdentityConflictError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return {**created.department.to_dict(), "user_count": 1}


@department.put("/{department_id}", response_model=DepartmentResponse)
async def update_department(
    department_id: int,
    department_data: DepartmentUpdate,
    request: Request,
    current_user: User = Depends(get_superadmin_user),
    db: AsyncSession = Depends(get_db),
):
    """更新部门信息"""
    repository = DepartmentRepository(db)
    department = await repository.get_by_id(department_id)

    if not department:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="部门不存在")

    updates = {}
    # 如果要修改名称，检查新名称是否已存在
    if department_data.name and department_data.name != department.name:
        existing = await repository.get_by_name(department_data.name)
        if existing:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="部门名称已存在")
        updates["name"] = department_data.name

    if department_data.description is not None:
        updates["description"] = department_data.description

    department = await repository.update(department_id, updates)
    if department is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="部门不存在")

    # 记录操作
    await log_operation(db, current_user.id, "更新部门", f"更新部门: {department.name}", request)

    # 获取部门下用户数量
    user_count = await repository.count_users(department_id)
    await db.commit()

    return {**department.to_dict(), "user_count": user_count}


@department.delete("/{department_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_department(
    department_id: int,
    request: Request,
    current_user: User = Depends(get_superadmin_user),
    db: AsyncSession = Depends(get_db),
):
    """删除部门（超管）：按删除边界只删部门与成员关系并撤销绑定凭证；阻断引用 409。"""
    try:
        await delete_department_use_case(db, actor=current_user, department_id=department_id, request=request)
    except DepartmentDeletionConflict as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except PermissionError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except LookupError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    await db.commit()
    return None


# =============================================================================
# === 部门成员管理 ===
# =============================================================================


class MemberAdd(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: int


class MemberRoleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["admin", "user"]


def _map_membership_error(exc: Exception) -> HTTPException:
    """成员用例错误到 HTTP 结论的映射。"""
    if isinstance(exc, PermissionError):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    if isinstance(exc, MemberNotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, MemberConflictError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@department.get("/{department_id}/members")
async def list_department_members(
    department_id: int,
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    current_user: DepartmentContext = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """分页列出部门成员；部门管理员限本部门，超级管理员任意部门。"""
    try:
        return await DepartmentMembershipService(db).list_members(
            current_user, department_id, offset=offset, limit=limit
        )
    except (PermissionError, MemberNotFoundError) as exc:
        raise _map_membership_error(exc) from exc


@department.get("/{department_id}/member-candidates")
async def search_member_candidates(
    department_id: int,
    search: str | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    current_user: DepartmentContext = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """检索可加入的已有账号；仅返回 user_id/uid/username。"""
    try:
        return await DepartmentMembershipService(db).search_candidates(
            current_user, department_id, search, offset=offset, limit=limit
        )
    except (PermissionError, MemberNotFoundError) as exc:
        raise _map_membership_error(exc) from exc


@department.post("/{department_id}/members")
async def add_department_member(
    department_id: int,
    payload: MemberAdd,
    request: Request,
    current_user: DepartmentContext = Depends(require_department_revision),
    db: AsyncSession = Depends(get_db),
):
    """添加已有账号为部门成员，默认普通角色。"""
    try:
        member = await DepartmentMembershipService(db).add_member(
            current_user, department_id, payload.user_id, request=request
        )
    except (PermissionError, MemberNotFoundError, MemberConflictError) as exc:
        raise _map_membership_error(exc) from exc
    return member


@department.patch("/{department_id}/members/{user_id}")
async def update_department_member_role(
    department_id: int,
    user_id: int,
    payload: MemberRoleUpdate,
    request: Request,
    current_user: DepartmentContext = Depends(require_department_revision),
    db: AsyncSession = Depends(get_db),
):
    """修改部门成员角色；任命/降级管理员仅超级管理员。"""
    try:
        member = await DepartmentMembershipService(db).set_role(
            current_user, department_id, user_id, payload.role, request=request
        )
    except (PermissionError, MemberNotFoundError, MemberConflictError) as exc:
        raise _map_membership_error(exc) from exc
    return member


@department.delete("/{department_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_department_member(
    department_id: int,
    user_id: int,
    request: Request,
    current_user: DepartmentContext = Depends(require_department_revision),
    db: AsyncSession = Depends(get_db),
):
    """移除部门成员；部门管理员只能移除普通成员。"""
    try:
        await DepartmentMembershipService(db).remove_member(current_user, department_id, user_id, request=request)
    except (PermissionError, MemberNotFoundError, MemberConflictError) as exc:
        raise _map_membership_error(exc) from exc
