"""管理员 WPS 协作 Channel 管理接口。"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from server.utils.auth_middleware import get_admin_user, get_db
from yuxi.agentscope.client import AgentScopeServiceClient
from yuxi.repositories.agentscope_channel_bindings import (
    AgentScopeChannelBindingRepository,
)
from yuxi.services.agentscope_channel_service import AgentScopeChannelService
from yuxi.storage.postgres.models_business import User

import os


agent_channels = APIRouter(prefix="/agent-channels", tags=["agent-channels"])


class ChannelCreate(BaseModel):
    """创建 WPS 协作 Channel。"""

    owner_uid: str | None = None
    agent_slug: str
    name: str = Field(min_length=1, max_length=128)
    app_id: str = Field(min_length=1, max_length=128)
    app_secret: str = Field(min_length=1)
    allow_from: list[str] = Field(default_factory=list)
    group_reply_policy: Literal["mention_only", "all"] = "mention_only"
    enabled: bool = True


class ChannelUpdate(BaseModel):
    """更新 WPS 协作 Channel；未提供 secret 时保持原凭据。"""

    agent_slug: str | None = None
    name: str | None = Field(default=None, min_length=1, max_length=128)
    app_id: str | None = Field(default=None, min_length=1, max_length=128)
    app_secret: str | None = Field(default=None, min_length=1)
    allow_from: list[str] | None = None
    group_reply_policy: Literal["mention_only", "all"] | None = None


def _service(db: AsyncSession) -> AgentScopeChannelService:
    """构造无状态 Channel 控制面服务。"""
    return AgentScopeChannelService(
        db,
        AgentScopeServiceClient(os.getenv("AGENTSCOPE_BASE_URL", "http://agentscope:8100")),
    )


async def _binding_or_404(db: AsyncSession, binding_id: str):
    """读取 binding，不存在时返回统一 404。"""
    binding = await AgentScopeChannelBindingRepository(db).get(binding_id)
    if binding is None:
        raise HTTPException(status_code=404, detail="协作渠道不存在")
    return binding


@agent_channels.get("")
async def list_channels(
    _current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """列出全部 WPS Channel binding，不返回 secret。"""
    items = await AgentScopeChannelBindingRepository(db).list_all()
    return {"success": True, "data": [item.to_dict() for item in items]}


@agent_channels.post("", status_code=status.HTTP_201_CREATED)
async def create_channel(
    payload: ChannelCreate,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """创建 binding intent 并立即 reconcile。"""
    owner_uid = payload.owner_uid or str(current_user.uid)
    binding = await _service(db).create_intent(
        **payload.model_dump(exclude={"owner_uid"}),
        owner_uid=owner_uid,
        actor_uid=str(current_user.uid),
    )
    return {"success": binding.sync_status == "synced", "data": binding.to_dict()}


@agent_channels.put("/{binding_id}")
async def update_channel(
    binding_id: str,
    payload: ChannelUpdate,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """更新 binding 并同步 Agent、模型、Session 和 WPS 配置。"""
    binding = await _binding_or_404(db, binding_id)
    updates = payload.model_dump(exclude_unset=True)
    binding = await _service(db).update_binding(
        binding,
        updates=updates,
        actor_uid=str(current_user.uid),
    )
    return {"success": binding.sync_status == "synced", "data": binding.to_dict()}


@agent_channels.post("/{binding_id}/reconcile")
async def reconcile_channel(
    binding_id: str,
    _current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """重试未完成或失败的跨库同步。"""
    binding = await _service(db).reconcile(await _binding_or_404(db, binding_id))
    return {"success": binding.sync_status == "synced", "data": binding.to_dict()}


@agent_channels.post("/{binding_id}/{action}")
async def set_channel_enabled(
    binding_id: str,
    action: Literal["enable", "disable"],
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """启用或禁用远端 AgentScope Channel。"""
    binding = await _binding_or_404(db, binding_id)
    binding = await _service(db).set_enabled(
        binding,
        action == "enable",
        actor_uid=str(current_user.uid),
    )
    return {"success": binding.sync_status == "synced", "data": binding.to_dict()}


@agent_channels.get("/{binding_id}/status")
async def get_channel_status(
    binding_id: str,
    _current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """读取 AgentScope 权威连接状态。"""
    binding = await _binding_or_404(db, binding_id)
    try:
        runtime = await _service(db).status(binding)
    except Exception as exc:
        raise HTTPException(status_code=502, detail="AgentScope Channel 状态读取失败") from exc
    return {"success": True, "data": {**binding.to_dict(), "runtime": runtime}}


@agent_channels.delete("/{binding_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_channel(
    binding_id: str,
    _current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """清理远端运行资源并删除 binding，历史 Thread 保留。"""
    binding = await _binding_or_404(db, binding_id)
    try:
        await _service(db).delete(binding)
    except Exception as exc:
        raise HTTPException(status_code=502, detail="协作渠道清理失败，可稍后重试") from exc
