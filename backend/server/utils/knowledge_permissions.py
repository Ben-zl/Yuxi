"""将知识库领域权限校验适配为 FastAPI 依赖。"""

from fastapi import Depends, HTTPException

from server.utils.auth_middleware import get_required_user
from yuxi.config.runtime import KNOWLEDGE_BACKEND_WEKNORA, knowledge_backend
from yuxi.knowledge.read_models import KnowledgeBaseDetail
from yuxi.knowledge.runtime import knowledge_base
from yuxi.permissions import (
    ResourcePermission,
    ResourcePermissionDenied,
    require_knowledge_base_permission,
    resolve_knowledge_content_write,
)
from yuxi.storage.postgres.models_business import User

_ADMIN_ROLES = ("admin", "superadmin")


def _weknora_backend_selected() -> bool:
    """当前进程是否运行在 WeKnora 知识库后端模式。"""

    return knowledge_backend() == KNOWLEDGE_BACKEND_WEKNORA


def _require_admin_in_builtin_mode(current_user: User) -> None:
    """内置模式保持知识库管理入口的管理员门禁,weknora 模式向部门成员开放。"""

    if not _weknora_backend_selected() and current_user.role not in _ADMIN_ROLES:
        raise HTTPException(status_code=403, detail="需要管理员权限")


async def ensure_knowledge_base_permission(
    kb_id: str,
    current_user: User,
    required: ResourcePermission,
) -> KnowledgeBaseDetail:
    """加载知识库并校验当前用户的有效资源权限。"""

    db_info = await knowledge_base.get_database_info(kb_id)
    if not db_info:
        raise HTTPException(status_code=404, detail=f"知识库 {kb_id} 不存在")

    try:
        require_knowledge_base_permission(current_user, db_info, required)
    except ResourcePermissionDenied as error:
        raise HTTPException(status_code=403, detail="无权操作该知识库") from error
    return db_info


async def require_knowledge_viewer(
    current_user: User = Depends(get_required_user),
) -> User:
    """知识库列表查看身份:weknora 模式全员可入,内置模式保持管理员门禁。"""

    _require_admin_in_builtin_mode(current_user)
    return current_user


async def require_knowledge_base_read(
    kb_id: str,
    current_user: User = Depends(get_required_user),
) -> User:
    """校验对指定知识库的读取权限;weknora 模式下部门成员按授权读取。"""

    _require_admin_in_builtin_mode(current_user)
    await ensure_knowledge_base_permission(kb_id, current_user, ResourcePermission.READ)
    return current_user


async def require_knowledge_base_manage(
    kb_id: str,
    current_user: User = Depends(get_required_user),
) -> User:
    """校验对指定知识库的整库管理权限;整库管理不向普通成员开放。"""

    _require_admin_in_builtin_mode(current_user)
    await ensure_knowledge_base_permission(kb_id, current_user, ResourcePermission.MANAGE)
    return current_user


async def require_knowledge_base_content_write(
    kb_id: str,
    current_user: User = Depends(get_required_user),
) -> KnowledgeBaseDetail:
    """校验内容维护权限:仅 weknora 托管库适用,归属部门成员与管理员可写。

    内容维护蕴含读取:命中归属部门仍需通过读取授权(如超级管理员收窄读取范围后)。
    """

    if not _weknora_backend_selected():
        raise HTTPException(status_code=403, detail="内容维护权限仅 WeKnora 知识库适用")

    db_info = await ensure_knowledge_base_permission(kb_id, current_user, ResourcePermission.READ)
    if not resolve_knowledge_content_write(current_user, db_info):
        raise HTTPException(status_code=403, detail="无权维护该知识库内容")
    return db_info
