"""部门成员管理用例：账号管理与部门角色分离后的事务 Owner。

锁序固定为 department 行 → actor 账号/成员 → target 账号/成员；
授权始终读锁内实时身份，不信任请求开始时快照。
"""

from dataclasses import replace

from fastapi import Request
from sqlalchemy import exists, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.repositories.department_membership_repository import DepartmentMembershipRepository
from yuxi.services.department_context_service import DepartmentContext
from yuxi.services.operation_log_service import log_operation
from yuxi.storage.postgres.models_business import Department, DepartmentMembership, User

VALID_ROLES = ("admin", "user")


class MemberNotFoundError(Exception):
    """目标账号或成员关系不存在。"""


class MemberConflictError(Exception):
    """成员关系重复或角色值非法等冲突。"""


def _assert_manager(actor: DepartmentContext, department_id: int, *, operation: str, target_role: str | None) -> None:
    """锁内实时身份的授权检查：部门管理员只管理本部门普通成员。"""
    if actor.account_role != "superadmin":
        if actor.department_id != department_id or actor.role != "admin":
            raise PermissionError("无权管理该部门成员")
        if operation == "set_role" or (target_role is not None and target_role == "admin"):
            raise PermissionError("无权修改部门管理员")


class DepartmentMembershipService:
    """部门成员列表、候选检索与成员写入用例。"""

    def __init__(self, db: AsyncSession):
        """绑定用例共享的请求事务会话。"""
        self.db = db

    async def _lock_department_or_404(self, department_id: int) -> Department:
        """以部门行锁验证存在后返回部门；与删除部门共用锁序，缺失按 404 语义抛错。"""
        department = (
            await self.db.execute(select(Department).where(Department.id == department_id).with_for_update())
        ).scalar_one_or_none()
        if department is None:
            raise MemberNotFoundError("部门不存在")
        return department

    async def _locked_actor_identity(self, actor: DepartmentContext, department_id: int) -> DepartmentContext:
        """锁 actor 的成员行并以锁内实时角色返回身份（超管原样通过）。

        成员关系已不存在时拒绝：不信任请求开始时解析的快照角色。
        """
        if actor.account_role == "superadmin":
            return actor
        locked = (
            await self.db.execute(
                select(DepartmentMembership)
                .where(
                    DepartmentMembership.user_id == actor.id,
                    DepartmentMembership.department_id == department_id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if locked is None:
            raise PermissionError("操作者已不是该部门成员")
        return replace(actor, role=locked.role)

    async def _require_department_exists(self, department_id: int) -> None:
        """只读确认部门存在（列表/检索类无写入路径不做行锁）；缺失按 404 语义抛错。"""
        exists_row = await self.db.scalar(select(exists().where(Department.id == department_id)))
        if not exists_row:
            raise MemberNotFoundError("部门不存在")

    async def list_members(
        self,
        actor: DepartmentContext,
        department_id: int,
        offset: int = 0,
        limit: int = 20,
    ) -> dict:
        """分页列出部门成员（名称、登录 ID、部门角色）。"""
        await self._require_department_exists(department_id)
        _assert_manager(actor, department_id, operation="list", target_role=None)
        total = await self.db.scalar(
            select(func.count())
            .select_from(DepartmentMembership)
            .join(User, User.id == DepartmentMembership.user_id)
            .where(DepartmentMembership.department_id == department_id, User.is_deleted == 0)
        )
        rows = await self.db.execute(
            select(DepartmentMembership, User)
            .join(User, User.id == DepartmentMembership.user_id)
            .where(DepartmentMembership.department_id == department_id, User.is_deleted == 0)
            .order_by(DepartmentMembership.user_id)
            .offset(offset)
            .limit(limit)
        )
        items = [
            {"user_id": user.id, "uid": user.uid, "username": user.username, "role": membership.role}
            for membership, user in rows.all()
        ]
        return {"items": items, "total": int(total or 0)}

    async def search_candidates(
        self,
        actor: DepartmentContext,
        department_id: int,
        search: str | None,
        offset: int = 0,
        limit: int = 20,
    ) -> dict:
        """检索可加入的已有有效账号；仅返回 user_id/uid/username。"""
        await self._require_department_exists(department_id)
        _assert_manager(actor, department_id, operation="list", target_role=None)
        joined = exists().where(
            DepartmentMembership.user_id == User.id,
            DepartmentMembership.department_id == department_id,
        )
        conditions = [User.is_deleted == 0, ~joined]
        if search:
            like = f"%{search}%"
            conditions.append((User.uid.like(like)) | (User.username.like(like)))
        total = await self.db.scalar(select(func.count()).select_from(User).where(*conditions))
        rows = await self.db.execute(select(User).where(*conditions).order_by(User.id).offset(offset).limit(limit))
        items = [{"user_id": user.id, "uid": user.uid, "username": user.username} for user in rows.scalars().all()]
        return {"items": items, "total": int(total or 0)}

    async def _load_target_user(self, user_id: int) -> User:
        """行锁加载目标账号；软删除账号同样视为不存在，不参与成员操作。"""
        target = (await self.db.execute(select(User).where(User.id == user_id).with_for_update())).scalar_one_or_none()
        if target is None or target.is_deleted:
            raise MemberNotFoundError("目标账号不存在")
        return target

    async def add_member(
        self,
        actor: DepartmentContext,
        department_id: int,
        user_id: int,
        *,
        request: Request | None = None,
    ) -> dict:
        """添加已有账号为成员，默认普通角色；重复添加不降级已有角色。"""
        await self._lock_department_or_404(department_id)
        actor = await self._locked_actor_identity(actor, department_id)
        _assert_manager(actor, department_id, operation="add", target_role=None)
        target = await self._load_target_user(user_id)

        existing = await DepartmentMembershipRepository(self.db).get(user_id, department_id)
        if existing is not None:
            raise MemberConflictError("该账号已是部门成员")

        try:
            await DepartmentMembershipRepository(self.db).add(user_id, department_id, role="user")
        except IntegrityError as exc:
            raise MemberConflictError("该账号已是部门成员") from exc
        await log_operation(self.db, actor.id, "添加部门成员", f"部门 {department_id} 添加成员 {target.uid}", request)
        await self.db.commit()
        return {"user_id": target.id, "uid": target.uid, "username": target.username, "role": "user"}

    async def set_role(
        self,
        actor: DepartmentContext,
        department_id: int,
        user_id: int,
        role: str,
        *,
        request: Request | None = None,
    ) -> dict:
        """修改部门内角色；任命/降级管理员仅超级管理员。"""
        if role not in VALID_ROLES:
            raise MemberConflictError("角色仅支持 admin/user")
        await self._lock_department_or_404(department_id)
        actor = await self._locked_actor_identity(actor, department_id)
        _assert_manager(actor, department_id, operation="set_role", target_role=None)
        target = await self._load_target_user(user_id)

        membership = await DepartmentMembershipRepository(self.db).get(user_id, department_id, for_update=True)
        if membership is None:
            raise MemberNotFoundError("目标账号不是该部门成员")
        membership.role = role
        await log_operation(
            self.db,
            actor.id,
            "修改部门成员角色",
            f"部门 {department_id} 成员 {target.uid} 角色改为 {role}",
            request,
        )
        await self.db.commit()
        return {"user_id": target.id, "uid": target.uid, "username": target.username, "role": role}

    async def remove_member(
        self,
        actor: DepartmentContext,
        department_id: int,
        user_id: int,
        *,
        request: Request | None = None,
    ) -> None:
        """移除部门成员；部门管理员只能移除普通成员。"""
        await self._lock_department_or_404(department_id)
        actor = await self._locked_actor_identity(actor, department_id)
        target_membership = await DepartmentMembershipRepository(self.db).get(user_id, department_id, for_update=True)
        target_role = target_membership.role if target_membership is not None else None
        _assert_manager(actor, department_id, operation="remove", target_role=target_role)
        if target_membership is None:
            raise MemberNotFoundError("目标账号不是该部门成员")
        target = (await self.db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
        await DepartmentMembershipRepository(self.db).remove(user_id, department_id)
        await log_operation(
            self.db,
            actor.id,
            "移除部门成员",
            f"部门 {department_id} 移除成员 {(target.uid if target else user_id)}",
            request,
        )
        await self.db.commit()
