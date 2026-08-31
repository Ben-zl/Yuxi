"""WPS 协作 Channel binding 的控制面同步服务。"""

from __future__ import annotations

import os
import re
import uuid

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.agentscope.client import AgentScopeServiceClient
from yuxi.agentscope.config_projection import project_runtime
from yuxi.repositories.agentscope_channel_bindings import (
    AgentScopeChannelBindingRepository,
)
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
    """加密 WPS app secret，数据库和 AgentScope 均只保存密文。"""
    value = secret.strip()
    if not value:
        raise ValueError("app_secret 不能为空")
    return _credential_cipher().encrypt(value.encode()).decode()


def _validate_ciphertext(ciphertext: str) -> None:
    """在同步前验证现有密文可由当前共享 key 解密。"""
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
    """维护 Yuxi binding 与 AgentScope WPS Channel 的最终一致性。"""

    def __init__(self, db: AsyncSession, client: AgentScopeServiceClient):
        self.db = db
        self.client = client
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
    ) -> AgentScopeChannelBinding:
        """先提交本地意图，再执行可重入的远端 reconcile。"""
        binding = await self.bindings.create(
            id=str(uuid.uuid4()),
            owner_uid=owner_uid,
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

    async def update_binding(
        self,
        binding: AgentScopeChannelBinding,
        *,
        updates: dict,
        actor_uid: str,
    ) -> AgentScopeChannelBinding:
        """更新本地意图；secret 未提供时保留原密文。"""
        app_secret = updates.pop("app_secret", None)
        for field, value in updates.items():
            if value is not None:
                setattr(binding, field, value)
        if app_secret is not None:
            binding.encrypted_app_secret = encrypt_app_secret(app_secret)
        if "allow_from" in updates and updates["allow_from"] is not None:
            binding.allow_from = sorted(
                {item.strip() for item in updates["allow_from"] if item.strip()},
            )
        binding.updated_by = actor_uid
        binding.sync_status = "pending"
        binding.last_error = None
        await self.db.commit()
        return await self.reconcile(binding)

    async def reconcile(
        self,
        binding: AgentScopeChannelBinding,
    ) -> AgentScopeChannelBinding:
        """按 credential、agent、channel 顺序幂等同步并回写远端 ID。"""
        try:
            _validate_ciphertext(binding.encrypted_app_secret)
            projection = await project_runtime(
                self.db,
                uid=binding.owner_uid,
                agent_slug=binding.agent_slug,
                model_spec=None,
            )
            binding.model_spec = projection.model_spec

            if binding.agentscope_credential_id:
                await self.client.update_credential(
                    binding.owner_uid,
                    binding.agentscope_credential_id,
                    projection.credential_data,
                )
            else:
                binding.agentscope_credential_id = await self.client.create_credential(
                    binding.owner_uid,
                    projection.credential_data,
                )
                await self.db.commit()

            chat_model_config = {
                **projection.chat_model_config,
                "credential_id": binding.agentscope_credential_id,
            }
            if binding.agentscope_agent_id:
                await self.client.update_agent(
                    binding.owner_uid,
                    binding.agentscope_agent_id,
                    projection.agent_request,
                )
            else:
                binding.agentscope_agent_id = await self.client.create_agent(
                    binding.owner_uid,
                    projection.agent_request,
                )
                await self.db.commit()

            channel_payload = self._channel_payload(binding, chat_model_config)
            if binding.agentscope_channel_id:
                await self.client.update_channel(
                    binding.owner_uid,
                    binding.agentscope_channel_id,
                    channel_payload,
                )
            else:
                record = await self.client.create_channel(
                    binding.owner_uid,
                    channel_payload,
                )
                binding.agentscope_channel_id = str(record["id"])
                await self.db.commit()

            sessions = await self.client.list_channel_sessions(
                binding.owner_uid,
                binding.agentscope_channel_id,
            )
            for session in sessions:
                await self.client.update_session_model(
                    binding.owner_uid,
                    str(session["agent_id"]),
                    str(session["id"]),
                    chat_model_config,
                )
                await self.client.set_permission_mode(
                    binding.owner_uid,
                    str(session["agent_id"]),
                    str(session["id"]),
                    "dont_ask",
                )

            binding.sync_status = "synced"
            binding.last_error = None
            await self.db.commit()
            await self.db.refresh(binding)
            return binding
        except Exception as exc:
            await self.db.rollback()
            current = await self.bindings.get(binding.id)
            if current is None:
                raise
            current.sync_status = "error"
            current.last_error = _safe_error(exc)
            await self.db.commit()
            await self.db.refresh(current)
            return current

    async def set_enabled(
        self,
        binding: AgentScopeChannelBinding,
        enabled: bool,
        *,
        actor_uid: str,
    ) -> AgentScopeChannelBinding:
        """切换远端 Channel 生命周期并更新本地意图。"""
        if not binding.agentscope_channel_id:
            binding.enabled = enabled
            binding.updated_by = actor_uid
            binding.sync_status = "pending"
            await self.db.commit()
            return await self.reconcile(binding)
        try:
            await self.client.set_channel_enabled(
                binding.owner_uid,
                binding.agentscope_channel_id,
                enabled,
            )
            binding.enabled = enabled
            binding.updated_by = actor_uid
            binding.sync_status = "synced"
            binding.last_error = None
        except Exception as exc:
            binding.sync_status = "error"
            binding.last_error = _safe_error(exc)
        await self.db.commit()
        await self.db.refresh(binding)
        return binding

    async def status(self, binding: AgentScopeChannelBinding) -> dict:
        """以 AgentScope 状态接口为 Channel 运行态事实来源。"""
        if not binding.agentscope_channel_id:
            return {"state": "not_created", "last_error": binding.last_error or ""}
        return await self.client.get_channel_status(
            binding.owner_uid,
            binding.agentscope_channel_id,
        )

    async def delete(self, binding: AgentScopeChannelBinding) -> None:
        """按 Channel、Agent、Credential 顺序清理，保留历史 Thread。"""
        try:
            if binding.agentscope_channel_id:
                await self.client.delete_channel(
                    binding.owner_uid,
                    binding.agentscope_channel_id,
                    missing_ok=True,
                )
            if binding.agentscope_agent_id:
                await self.client.delete_agent(
                    binding.owner_uid,
                    binding.agentscope_agent_id,
                    missing_ok=True,
                )
            if binding.agentscope_credential_id:
                await self.client.delete_credential(
                    binding.owner_uid,
                    binding.agentscope_credential_id,
                    missing_ok=True,
                )
        except Exception as exc:
            binding.sync_status = "error"
            binding.last_error = _safe_error(exc)
            await self.db.commit()
            raise
        await self.bindings.delete(binding)
        await self.db.commit()

    @staticmethod
    def _channel_payload(binding: AgentScopeChannelBinding, chat_model_config: dict) -> dict:
        """构造 AgentScope 原生 Channel CRUD 载荷。"""
        return {
            "channel_type": "wps_xiezuo",
            "name": binding.name,
            "credentials": {
                "app_id": binding.app_id,
                "app_secret": binding.encrypted_app_secret,
            },
            "platform_config": {
                "allow_from": binding.allow_from or [],
                "group_reply_policy": binding.group_reply_policy,
            },
            "routing": {
                "bindings": [
                    {
                        "match_key": "chat_id",
                        "match_value": "*",
                        "agent_id": binding.agentscope_agent_id,
                        "session_scope": "per_chat",
                    },
                ],
            },
            "session": {
                "chat_model_config": chat_model_config,
                "permission_mode": "dont_ask",
                "busy_message_policy": "queue",
            },
            "enabled": bool(binding.enabled),
        }
