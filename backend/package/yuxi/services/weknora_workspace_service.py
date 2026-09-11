"""WeKnora 部门 workspace 开通与专属 Key 解析。

每个部门对应一个远端 workspace:开通用部署级 Key(``WEKNORA_API_KEY``,仅此用途),
日常知识库操作一律使用部门专属 Key,以 Fernet 密文保存在映射表中,不回退、不落日志。
"""

from __future__ import annotations

import os

from cryptography.fernet import Fernet, InvalidToken

from yuxi.knowledge.base import KBOperationError
from yuxi.knowledge.weknora import (
    WeKnoraClient,
    WeKnoraClientError,
    load_weknora_settings,
    weknora_instance_fingerprint,
)
from yuxi.utils import logger

WEKNORA_WORKSPACE_CREDENTIAL_ENV = "WEKNORA_WORKSPACE_CREDENTIAL_KEY"
WORKSPACE_CONFIRMED = "confirmed"
WORKSPACE_PENDING_REVIEW = "pending_review"


def _credential_cipher() -> Fernet:
    """读取 workspace 专属 Key 的 Fernet 凭据 Key;缺失或非法直接失败。"""

    key = os.environ.get(WEKNORA_WORKSPACE_CREDENTIAL_ENV, "").strip()
    if not key:
        raise RuntimeError(f"缺少 {WEKNORA_WORKSPACE_CREDENTIAL_ENV}")
    try:
        return Fernet(key.encode())
    except ValueError as exc:
        raise RuntimeError(f"{WEKNORA_WORKSPACE_CREDENTIAL_ENV} 不是有效 Fernet key") from exc


def encrypt_workspace_key(api_key: str) -> str:
    """加密部门 workspace 专属 Key,数据库只保存密文。"""

    value = api_key.strip()
    if not value:
        raise ValueError("workspace api_key 不能为空")
    return _credential_cipher().encrypt(value.encode()).decode()


def decrypt_workspace_key(ciphertext: str) -> str:
    """解密专属 Key;密文损坏或凭据 Key 轮换失配时给出明确错误。"""

    try:
        return _credential_cipher().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise RuntimeError("workspace 专属 Key 无法用当前凭据 Key 解密") from exc


def _extract_data(response) -> dict:
    """提取 WeKnora 响应的 data 载荷;非字典一律按空处理。"""

    try:
        payload = response.json()
    except Exception:  # noqa: BLE001
        return {}
    data = payload.get("data") if isinstance(payload, dict) else None
    return data if isinstance(data, dict) else {}


def _build_client(settings) -> WeKnoraClient:
    """以给定配置构造客户端;自检路径经此工厂便于测试注入传输。"""

    return WeKnoraClient(settings)


async def ensure_department_workspace(
    department_id: int,
    *,
    client: WeKnoraClient | None = None,
) -> tuple[str, object]:
    """幂等开通部门 workspace,返回 (专属 Key, workspace 映射行)。

    已有当前实例的 confirmed 映射直接复用;否则用开通 Key 创建新空间,
    以专属 Key 自检后落库。任何一步失败都保留明确错误与远端空间 ID 供人工核对,
    不自动重试、不自动复用结果不确定的空间。
    """

    from yuxi.repositories.weknora_workspace_repository import WeknoraWorkspaceRepository

    settings = load_weknora_settings()
    if not settings.ready:
        raise KBOperationError("WeKnora 部署配置不完整,无法开通部门 workspace")

    department_id = int(department_id)
    fingerprint = weknora_instance_fingerprint(settings)
    repo = WeknoraWorkspaceRepository()

    existing = await repo.get_by_department(department_id)
    if existing is not None and existing.status == WORKSPACE_CONFIRMED and existing.instance == fingerprint:
        try:
            api_key = decrypt_workspace_key(existing.encrypted_api_key)
        except RuntimeError as error:
            raise KBOperationError(f"部门 {department_id} 的 workspace 专属 Key 解密失败: {error}") from error
        return api_key, existing
    if existing is not None:
        logger.warning(
            f"WeKnora 部门 {department_id} 既有映射不可用"
            f"(status={existing.status}, instance={existing.instance}),将重新开通;"
            f"旧 workspace tenant={existing.workspace_tenant_id} 需人工在远端 UI 清理"
        )

    from yuxi.repositories.department_repository import DepartmentRepository

    workspace_name = await DepartmentRepository().get_name_by_id(department_id) or f"yuxi-dept-{department_id}"

    provisioning_client = client or WeKnoraClient(settings)
    try:
        response = await provisioning_client.request("POST", "tenants", json={"name": workspace_name})
    except WeKnoraClientError as error:
        raise KBOperationError(f"WeKnora workspace 开通失败(未确认是否已创建),需人工核对后重试: {error}") from error

    data = _extract_data(response)
    tenant_id = str(data.get("id") or "")
    api_key = str(data.get("api_key") or "")
    if not tenant_id or not api_key:
        raise KBOperationError(
            f"WeKnora workspace 开通响应缺少 id/api_key(tenant={tenant_id or '缺失'}),"
            "远端空间需人工核对;该空间不会自动复用"
        )

    # 自检:专属 Key 必须能独立访问新空间,否则不落 confirmed 映射
    dept_settings = settings.with_api_key(api_key)
    try:
        await _build_client(dept_settings).request("GET", "knowledge-bases")
    except WeKnoraClientError as error:
        await repo.replace_for_department(
            department_id,
            {
                "workspace_tenant_id": tenant_id,
                "workspace_name": workspace_name,
                "encrypted_api_key": encrypt_workspace_key(api_key),
                "instance": fingerprint,
                "status": WORKSPACE_PENDING_REVIEW,
            },
        )
        raise KBOperationError(
            f"部门 workspace 开通自检失败(tenant={tenant_id}),映射已标待核对,请人工确认后再试: {error}"
        ) from error

    payload = {
        "workspace_tenant_id": tenant_id,
        "workspace_name": workspace_name,
        "encrypted_api_key": encrypt_workspace_key(api_key),
        "instance": fingerprint,
        "status": WORKSPACE_CONFIRMED,
    }
    if existing is not None:
        # 重新开通:覆盖不可用旧行(待核对或旧实例),旧空间由上方告警留档
        row = await repo.replace_for_department(department_id, payload)
        return api_key, row
    try:
        row = await repo.create({"department_id": department_id, **payload})
    except Exception as error:  # noqa: BLE001
        from sqlalchemy.exc import IntegrityError

        if isinstance(error, IntegrityError):
            # 并发开通:复用先落库的映射,本分支创建的空间成为残留
            winner = await repo.get_by_department(department_id)
            if winner is not None and winner.status == WORKSPACE_CONFIRMED and winner.instance == fingerprint:
                logger.warning(f"WeKnora 部门 {department_id} 并发开通,残留空间 tenant={tenant_id} 需人工清理")
                try:
                    winner_key = decrypt_workspace_key(winner.encrypted_api_key)
                except RuntimeError as decrypt_error:
                    raise KBOperationError(
                        f"部门 {department_id} 的 workspace 专属 Key 解密失败: {decrypt_error}"
                    ) from decrypt_error
                return winner_key, winner
        raise KBOperationError(
            f"部门 workspace 映射落库失败,远端空间 tenant={tenant_id} 已创建且需人工登记: {error}"
        ) from error
    return api_key, row


async def resolve_department_settings(department_id: int) -> tuple[object, object]:
    """解析部门专属 Key 对应的出站配置,返回 (WeKnoraSettings, 映射行)。

    映射缺失、状态待核对、实例指纹不符或解密失败均明确失败;
    任何情况下不回退使用开通 Key 操作部门知识库。
    """

    from yuxi.repositories.weknora_workspace_repository import WeknoraWorkspaceRepository

    settings = load_weknora_settings()
    if not settings.ready:
        raise KBOperationError("WeKnora 部署配置不完整,无法执行托管知识库用例")

    department_id = int(department_id)
    mapping = await WeknoraWorkspaceRepository().get_by_department(department_id)
    if mapping is None:
        raise KBOperationError(f"部门 {department_id} 的 WeKnora workspace 未开通;创建该部门托管知识库时会自动开通")
    if mapping.status != WORKSPACE_CONFIRMED:
        raise KBOperationError(f"部门 {department_id} 的 WeKnora workspace 状态为 {mapping.status},需人工核对后使用")
    if mapping.instance != weknora_instance_fingerprint(settings):
        raise KBOperationError(
            f"当前 WeKnora 服务地址与部门 {department_id} 空间开通时不同,映射不可用;重新开通后会生成新空间"
        )
    try:
        api_key = decrypt_workspace_key(mapping.encrypted_api_key)
    except RuntimeError as error:
        raise KBOperationError(f"部门 {department_id} 的 workspace 专属 Key 解密失败: {error}") from error
    return settings.with_api_key(api_key), mapping
