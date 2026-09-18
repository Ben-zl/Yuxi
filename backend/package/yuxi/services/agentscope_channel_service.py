"""WPS 协作 Channel binding 的 Yuxi 控制面服务。"""

from __future__ import annotations

import json
import os
import re
import uuid

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.agentscope.config_projection import project_runtime
from yuxi.repositories.agentscope_channel_bindings import AgentScopeChannelBindingRepository
from yuxi.services.run_queue_service import get_redis_client
from yuxi.services.wps_channel_runtime import wps_channel_status_key
from yuxi.storage.postgres.models_business import AgentScopeChannelBinding


def _credential_cipher() -> Fernet:
    """读取 WPS Channel 专用的共享 Fernet key。"""
    key = os.getenv("AGENTSCOPE_CHANNEL_CREDENTIAL_KEY", "")
    if not key:
        raise RuntimeError("缺少 AGENTSCOPE_CHANNEL_CREDENTIAL_KEY")
    try:
        return Fernet(key.encode())
    except ValueError as exc:
        raise RuntimeError("AGENTSCOPE_CHANNEL_CREDENTIAL_KEY 不是有效 Fernet key") from exc


def encrypt_app_secret(secret: str) -> str:
    """加密 WPS app secret，数据库只保存密文。"""
    value = secret.strip()
    if not value:
        raise ValueError("app_secret 不能为空")
    return _credential_cipher().encrypt(value.encode()).decode()


def _validate_ciphertext(ciphertext: str) -> None:
    """验证现有密文可由当前共享 key 解密。"""
    try:
        _credential_cipher().decrypt(ciphertext.encode())
    except InvalidToken as exc:
        raise RuntimeError("WPS app_secret 无法使用当前凭据 key 解密") from exc


def _safe_error(exc: Exception) -> str:
    """生成不包含请求正文和凭据的同步错误摘要。"""
    message = re.sub(r"https?://\S+", "[URL]", str(exc))
    message = re.sub(r"(?i)(api[_ -]?key|secret|token)\s*[:=]\s*\S+", r"\1=[REDACTED_SECRET]", message)
    return f"{type(exc).__name__}: {message}"[:500]


class AgentScopeChannelService:
    """维护 WPS binding；实际执行始终由 Yuxi RunSubmissionCommand 提交。"""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.bindings = AgentScopeChannelBindingRepository(db)

    async def create_intent(
        self,
        *,
        owner_uid: str,
        agent_slug: str,
        name: str,
        app_id: str,
        app_secret: str,
        allow_from: list[str],
        group_reply_policy: str,
        enabled: bool,
        actor_uid: str,
        department_id: int,
    ) -> AgentScopeChannelBinding:
        """提交本地 binding，并验证其 AgentScope 运行时投影。"""
        # 与删除部门共用行锁：部门不存在或删除中时拒绝创建，避免产生孤儿绑定
        from yuxi.storage.postgres.models_business import Department

        locked = await self.db.execute(select(Department.id).where(Department.id == department_id).with_for_update())
        if locked.scalar_one_or_none() is None:
            raise LookupError("部门不存在")
        binding = await self.bindings.create(
            id=str(uuid.uuid4()),
            owner_uid=owner_uid,
            department_id=department_id,
            agent_slug=agent_slug,
            name=name.strip(),
            channel_type="wps_xiezuo",
            app_id=app_id.strip(),
            encrypted_app_secret=encrypt_app_secret(app_secret),
            allow_from=sorted({item.strip() for item in allow_from if item.strip()}),
            group_reply_policy=group_reply_policy,
            enabled=enabled,
            model_spec=None,
            sync_status="pending",
            created_by=actor_uid,
            updated_by=actor_uid,
        )
        await self.db.commit()
        return await self.reconcile(binding)

    async def update_binding(self, binding: AgentScopeChannelBinding, *, updates: dict, actor_uid: str):
        """更新本地意图；secret 未提供时保留原密文。"""
        app_secret = updates.pop("app_secret", None)
        for field, value in updates.items():
            if value is not None:
                setattr(binding, field, value)
        if app_secret is not None:
            binding.encrypted_app_secret = encrypt_app_secret(app_secret)
        if "allow_from" in updates and updates["allow_from"] is not None:
            binding.allow_from = sorted({item.strip() for item in updates["allow_from"] if item.strip()})
        binding.updated_by = actor_uid
        binding.sync_status = "pending"
        binding.last_error = None
        await self.db.commit()
        return await self.reconcile(binding)

    async def reconcile(self, binding: AgentScopeChannelBinding) -> AgentScopeChannelBinding:
        """验证凭据和 Agent 投影，不创建 AgentScope 原生 Channel。"""
        try:
            if binding.department_id is None:
                # 未绑定部门的旧 binding 不猜测归属，沿既有 error 状态展示
                raise ValueError("Channel binding 未绑定部门，请重新创建以固定部门")
            _validate_ciphertext(binding.encrypted_app_secret)
            await project_runtime(
                self.db,
                uid=binding.owner_uid,
                agent_slug=binding.agent_slug,
                model_spec=None,
                department_id=binding.department_id,
            )
            binding.model_spec = None
            binding.agentscope_channel_id = None
            binding.agentscope_agent_id = None
            binding.agentscope_credential_id = None
            binding.sync_status = "synced"
            binding.last_error = None
        except Exception as exc:
            await self.db.rollback()
            binding = await self.bindings.get(binding.id)
            if binding is None:
                raise
            binding.sync_status = "error"
            binding.last_error = _safe_error(exc)
        await self.db.commit()
        await self.db.refresh(binding)
        return binding

    async def set_enabled(self, binding, enabled: bool, *, actor_uid: str):
        """更新连接意图，独立 WPS runtime 将按数据库状态 reconcile。"""
        binding.enabled = enabled
        binding.updated_by = actor_uid
        binding.last_error = None
        if binding.sync_status == "error":
            binding.sync_status = "pending"
        await self.db.commit()
        if binding.sync_status == "pending":
            return await self.reconcile(binding)
        await self.db.refresh(binding)
        return binding

    async def status(self, binding: AgentScopeChannelBinding) -> dict:
        """读取 WPS runtime 发布的短 TTL 连接状态。"""
        if not binding.enabled or binding.sync_status != "synced":
            return {"state": "stopped", "last_error": binding.last_error or ""}
        redis = await get_redis_client()
        raw = await redis.get(wps_channel_status_key(binding.id))
        if not raw:
            return {"state": "stopped", "last_error": binding.last_error or ""}
        try:
            value = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return {"state": "stopped", "last_error": "invalid runtime status"}
        return {
            "state": str(value.get("state") or "stopped"),
            "last_error": str(value.get("last_error") or ""),
        }

    async def delete(self, binding: AgentScopeChannelBinding) -> None:
        """删除本地 binding；runtime reconcile 将关闭对应连接。"""
        await self.bindings.delete(binding)
        await self.db.commit()
