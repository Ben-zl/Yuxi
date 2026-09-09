"""WeKnora 托管知识库的建库、绑定与删除用例。

跨本地持久化与远端调用的协调在此闭合:先登记本地意向,再创建远端资源,
绑定确认后才宣告成功;结果不确定时保留待核对状态,不自动重试、不补偿删除。
"""

from __future__ import annotations

import secrets
import string
from typing import Any

from yuxi.knowledge.base import KBNameConflictError, KBNotFoundError, KBOperationError
from yuxi.knowledge.weknora import (
    BINDING_CONFIRMED,
    BINDING_PENDING_REVIEW,
    WeKnoraClient,
    WeKnoraClientError,
    load_weknora_settings,
    weknora_instance_fingerprint,
)
from yuxi.permissions import normalize_permission_config
from yuxi.utils import logger


def build_default_share_config(owning_department_id: int) -> dict:
    """WeKnora 库默认授权:归属部门可读,归属部门管理员可管理。"""

    department_ids = [int(owning_department_id)]
    return normalize_permission_config(
        {
            "version": 2,
            "read_scope": {"access_level": "department", "department_ids": department_ids, "user_uids": []},
            "manage_scope": {"access_level": "department", "department_ids": department_ids, "user_uids": []},
        },
        strict=True,
    )


def _require_ready_settings():
    """加载并校验 WeKnora 部署配置;不完整时明确拒绝。"""

    settings = load_weknora_settings()
    if not settings.ready:
        raise KBOperationError("WeKnora 部署配置不完整,无法执行托管知识库用例")
    return settings


def _extract_data(response: Any) -> dict:
    """提取 WeKnora 响应的 data 载荷。"""

    try:
        payload = response.json()
    except Exception as error:  # noqa: BLE001
        raise KBOperationError(f"WeKnora 响应不是合法 JSON: {error}") from error
    data = payload.get("data") if isinstance(payload, dict) else None
    return data if isinstance(data, dict) else {}


async def _ensure_department_exists(department_id: int) -> None:
    """确认归属部门存在,防止把库绑定到不存在的部门。"""

    from yuxi.repositories.department_repository import DepartmentRepository

    department = await DepartmentRepository().get_by_id(department_id)
    if department is None:
        raise KBOperationError(f"归属部门 {department_id} 不存在")


async def create_weknora_database(
    *,
    database_name: str,
    description: str,
    owning_department_id: int,
    created_by: str | None,
    client: WeKnoraClient | None = None,
):
    """创建托管知识库:本地登记 → 远端创建 → 回读确认 → 绑定落定。"""

    from yuxi.knowledge.runtime import knowledge_base
    from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository

    settings = _require_ready_settings()
    missing = settings.missing_model_fields
    if missing:
        raise KBOperationError(f"建库需要 WeKnora 模型配置,缺少: {', '.join(missing)}")
    await _ensure_department_exists(int(owning_department_id))

    if await knowledge_base.database_name_exists(database_name):
        raise KBNameConflictError(f"知识库名称 '{database_name}' 已存在，请使用其他名称")

    kb_repo = KnowledgeBaseRepository()
    alphabet = string.ascii_lowercase + string.digits
    while True:
        kb_id = "kb_" + "".join(secrets.choice(alphabet) for _ in range(10))
        if await kb_repo.get_by_kb_id(kb_id) is None:
            break

    fingerprint = weknora_instance_fingerprint(settings)
    await kb_repo.create(
        {
            "kb_id": kb_id,
            "name": database_name,
            "description": description,
            "kb_type": "weknora",
            "embedding_model_spec": None,
            "llm_model_spec": None,
            "query_params": {},
            "additional_params": {},
            "share_config": build_default_share_config(owning_department_id),
            "created_by": created_by,
            "owning_department_id": int(owning_department_id),
            "remote_binding": {
                "instance": fingerprint,
                "remote_kb_id": None,
                "status": BINDING_PENDING_REVIEW,
            },
        }
    )

    remote_client = client or WeKnoraClient(settings)
    try:
        response = await remote_client.request(
            "POST",
            "knowledge-bases",
            json={
                "name": database_name,
                "description": description,
                "embedding_model_id": settings.embedding_model_id,
                "summary_model_id": settings.summary_model_id,
            },
        )
    except WeKnoraClientError as error:
        if error.status_code is not None and 400 <= error.status_code < 500:
            # 远端明确拒绝,请求未创建资源,本地登记可安全撤销
            await kb_repo.delete(kb_id)
            raise KBOperationError(f"WeKnora 拒绝创建知识库: {error}") from error
        logger.error(f"WeKnora create outcome uncertain: kb_id={kb_id}: {error}")
        raise KBOperationError(
            f"WeKnora 创建结果不确定(status={error.status_code or '传输失败'}),"
            f"知识库 {kb_id} 保留待核对状态,请核对远端结果后再处理"
        ) from error

    remote_kb_id = str(_extract_data(response).get("id") or "")
    if not remote_kb_id:
        logger.error(f"WeKnora create response missing id: kb_id={kb_id}")
        raise KBOperationError(f"WeKnora 创建响应缺少知识库 ID,结果不确定,知识库 {kb_id} 保留待核对状态")

    try:
        confirm = await remote_client.request("GET", f"knowledge-bases/{remote_kb_id}")
    except WeKnoraClientError as error:
        logger.error(f"WeKnora create confirm failed: kb_id={kb_id}, remote={remote_kb_id}: {error}")
        # 需要同时保留 remote_kb_id 供核对,更新绑定后抛出明确错误
        await kb_repo.update(
            kb_id,
            {
                "remote_binding": {
                    "instance": fingerprint,
                    "remote_kb_id": remote_kb_id,
                    "status": BINDING_PENDING_REVIEW,
                }
            },
        )
        raise KBOperationError(
            f"WeKnora 创建已提交(remote_kb_id={remote_kb_id})但确认失败,知识库 {kb_id} 保留待核对状态: {error}"
        ) from error
    remote_name = str(_extract_data(confirm).get("name") or "")

    await kb_repo.update(
        kb_id,
        {
            "remote_binding": {
                "instance": fingerprint,
                "remote_kb_id": remote_kb_id,
                "status": BINDING_CONFIRMED,
                "remote_name": remote_name,
            }
        },
    )

    database = await knowledge_base.get_database_info(kb_id)
    if database is None:
        raise KBOperationError(f"知识库 {kb_id} 创建后读取失败")
    return database


async def _load_confirmed_binding(kb_id: str, *, action: str):
    """加载并校验已确认的托管绑定;所有远端写操作前共用。"""

    from yuxi.knowledge.runtime import knowledge_base

    detail = await knowledge_base.get_database_info(kb_id)
    if detail is None:
        raise KBNotFoundError(f"知识库 {kb_id} 不存在")

    binding = detail.remote_binding or {}
    if binding.get("status") != BINDING_CONFIRMED or not binding.get("remote_kb_id"):
        raise KBOperationError(
            f"知识库 {kb_id} 的远端绑定未确认(状态: {binding.get('status') or '缺失'}),{action}前需先核对远端结果"
        )

    settings = _require_ready_settings()
    fingerprint = weknora_instance_fingerprint(settings)
    if binding.get("instance") != fingerprint:
        raise KBOperationError(
            f"当前 WeKnora 服务地址与绑定时不同,拒绝{action}以免影响另一实例的资源;请先恢复原地址或人工核对绑定"
        )
    return detail, binding, settings


async def delete_weknora_database(kb_id: str, *, client: WeKnoraClient | None = None) -> dict:
    """删除托管知识库:远端确认删除后才执行本地收尾。"""

    from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository

    _, binding, settings = await _load_confirmed_binding(kb_id, action="删除")

    remote_client = client or WeKnoraClient(settings)
    remote_kb_id = str(binding["remote_kb_id"])
    try:
        await remote_client.request("DELETE", f"knowledge-bases/{remote_kb_id}")
    except WeKnoraClientError as error:
        if error.status_code == 404:
            logger.warning(f"WeKnora knowledge base already gone: kb_id={kb_id}, remote={remote_kb_id}")
        else:
            raise KBOperationError(f"WeKnora 删除未确认,知识库 {kb_id} 保留: {error}") from error

    await KnowledgeBaseRepository().delete(kb_id)
    return {"message": "删除成功", "kb_id": kb_id}


async def update_weknora_database(
    kb_id: str,
    *,
    name: str | None = None,
    description: str | None = None,
    share_config: dict | None = None,
    llm_model_spec: str | None = None,
    update_llm_model_spec: bool = False,
    operator_uid: str | None = None,
    operator_department_id: int | str | None = None,
    client: WeKnoraClient | None = None,
):
    """更新托管知识库:名称/描述先同步远端,再落本地;更新幂等,失败可直接重试。"""

    from yuxi.knowledge.runtime import knowledge_base

    _, binding, settings = await _load_confirmed_binding(kb_id, action="更新")

    if name or description:
        remote_payload: dict[str, Any] = {}
        if name:
            remote_payload["name"] = name
        if description:
            remote_payload["description"] = description
        remote_client = client or WeKnoraClient(settings)
        try:
            await remote_client.request("PUT", f"knowledge-bases/{binding['remote_kb_id']}", json=remote_payload)
        except WeKnoraClientError as error:
            raise KBOperationError(f"WeKnora 名称/描述同步失败,本地未更新: {error}") from error

    return await knowledge_base.update_database(
        kb_id,
        name,
        description,
        llm_model_spec,
        update_llm_model_spec=update_llm_model_spec,
        additional_params=None,
        share_config=share_config,
        operator_uid=operator_uid,
        operator_department_id=operator_department_id,
    )
