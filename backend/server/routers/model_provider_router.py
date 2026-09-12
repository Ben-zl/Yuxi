"""独立模型供应商配置路由。"""

from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from server.utils.auth_middleware import get_admin_user, get_db, get_required_user, get_superadmin_user
from yuxi.models.providers.service import (
    check_credential_status,
    create_provider_config,
    delete_provider_config,
    fetch_remote_models,
    get_all_model_providers,
    get_all_model_providers_internal,
    get_model_provider_by_id,
    test_model_status_by_spec,
    update_provider_config,
)
from yuxi.permissions.resource_permission import (
    ResourcePermission,
    require_resource_permission,
    resolve_model_provider_permission,
)
from yuxi.storage.postgres.models_business import User
from yuxi.storage.postgres.manager import pg_manager
from yuxi.utils import logger

model_providers = APIRouter(prefix="/system/model-providers", tags=["model-providers"])


async def _get_manageable_provider(db: AsyncSession, provider_id: str, current_user: User):
    """读取可管理的模型供应商，避免用 READ 权限执行连接型操作。"""
    provider = await get_model_provider_by_id(db, provider_id, user=current_user)
    if provider is None:
        raise HTTPException(status_code=404, detail=f"供应商 {provider_id} 不存在")
    try:
        require_resource_permission(
            resolve_model_provider_permission(current_user, provider),
            ResourcePermission.MANAGE,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return provider


async def _refresh_model_cache() -> None:
    """刷新模型缓存（CRUD 操作后调用）。"""
    from yuxi.models.providers.cache import model_cache

    try:
        async with pg_manager.get_async_session_context() as session:
            providers = await get_all_model_providers_internal(session)
            model_cache.rebuild(providers)
            logger.info(f"Model cache refreshed: {len(model_cache.get_all_specs())} models loaded")
    except Exception as e:
        logger.error(f"Failed to refresh model cache: error_type={type(e).__name__}")


class ModelProviderPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: str | None = Field(None, description="供应商稳定标识")
    display_name: str | None = Field(None, description="展示名称")
    provider_type: str | None = Field(None, description="供应商适配类型，默认 openai")
    default_protocol: str | None = Field(None, description="默认协议")
    base_url: str | None = Field(None, description="API 基础 URL")
    embedding_base_url: str | None = Field(None, description="Embedding 模型请求基础 URL")
    rerank_base_url: str | None = Field(None, description="Rerank 模型请求基础 URL")
    models_endpoint: str | None = Field(None, description="聊天/通用模型列表端点")
    embedding_models_endpoint: str | None = Field(None, description="Embedding 模型列表端点")
    rerank_models_endpoint: str | None = Field(None, description="Rerank 模型列表端点")
    api_key_env: str | None = Field(None, description="API Key 环境变量名")
    api_key: str | None = Field(None, description="直接配置的 API Key")
    capabilities: list[str] | None = Field(None, description="支持能力")
    enabled_models: list[dict[str, Any]] | None = Field(None, description="已启用模型配置")
    headers_json: dict[str, Any] | None = Field(None, description="额外请求头")
    extra_json: dict[str, Any] | None = Field(None, description="扩展配置")
    is_enabled: bool | None = Field(None, description="是否启用")
    share_config: dict[str, Any] | None = Field(None, description="共享权限配置，仅支持 global/department")


@model_providers.get("")
async def list_providers(
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """获取独立模型供应商配置列表。"""
    providers = await get_all_model_providers(db, user=current_user)
    data = []
    for p in providers:
        d = p.to_dict(sanitize=True)
        d["credential_status"] = check_credential_status(p)
        data.append(d)
    return {"success": True, "data": data}


@model_providers.post("")
async def create_provider(
    payload: ModelProviderPayload,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """创建独立模型供应商配置。"""
    try:
        provider = await create_provider_config(
            db,
            payload.model_dump(exclude_none=True),
            current_user.username,
            operator=current_user,
        )
        await db.commit()
        await _refresh_model_cache()
        return {"success": True, "data": provider.to_dict(sanitize=True)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        logger.error(f"创建模型供应商失败: error_type={type(e).__name__}")
        raise HTTPException(status_code=500, detail="创建模型供应商失败")


@model_providers.get("/{provider_id}")
async def get_provider(
    provider_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """获取单个独立模型供应商配置。"""
    provider = await get_model_provider_by_id(db, provider_id, user=current_user)
    if provider is None:
        raise HTTPException(status_code=404, detail=f"供应商 {provider_id} 不存在")
    data = provider.to_dict(sanitize=True)
    data["credential_status"] = check_credential_status(provider)
    return {"success": True, "data": data}


@model_providers.put("/{provider_id}")
async def update_provider(
    provider_id: str,
    payload: ModelProviderPayload,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """更新独立模型供应商配置。"""
    try:
        # 获取用户显式设置过的字段（即使值为 None），以便正确处理清空操作
        unset_fields = payload.model_fields_set
        data = payload.model_dump(exclude_none=True)
        for nullable_field in (
            "api_key_env",
            "api_key",
            "default_protocol",
            "embedding_base_url",
            "rerank_base_url",
            "models_endpoint",
            "embedding_models_endpoint",
            "rerank_models_endpoint",
        ):
            if nullable_field in unset_fields and getattr(payload, nullable_field) is None:
                data[nullable_field] = None
        provider = await update_provider_config(db, provider_id, data, current_user.username, operator=current_user)
        if provider is None:
            raise HTTPException(status_code=404, detail=f"供应商 {provider_id} 不存在")
        await db.commit()
        await _refresh_model_cache()
        return {"success": True, "data": provider.to_dict(sanitize=True)}
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        logger.error(f"更新模型供应商失败 {provider_id}: error_type={type(e).__name__}")
        raise HTTPException(status_code=500, detail="更新模型供应商失败")


@model_providers.delete("/{provider_id}")
async def delete_provider(
    provider_id: str,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """删除独立模型供应商配置。"""
    try:
        deleted = await delete_provider_config(db, provider_id, operator=current_user)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"供应商 {provider_id} 不存在")
        await db.commit()
        await _refresh_model_cache()
        return {"success": True}
    except HTTPException:
        raise
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))


@model_providers.get("/{provider_id}/remote-models")
async def get_remote_models(
    provider_id: str,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """实时拉取远端 /models，不落库。"""
    provider = await _get_manageable_provider(db, provider_id, current_user)
    try:
        models = await fetch_remote_models(provider)
        return {"success": True, "data": models}
    except httpx.HTTPStatusError as e:
        # 远程 API 返回的错误，不透传状态码避免前端误判为系统认证失败
        if e.response.status_code == 401:
            raise HTTPException(status_code=502, detail="远端 API 认证失败，请检查 API Key 配置")
        raise HTTPException(status_code=502, detail="远端 Models 请求失败")
    except Exception as e:
        logger.error(f"拉取远端模型失败 {provider_id}: error_type={type(e).__name__}")
        raise HTTPException(status_code=502, detail="拉取远端模型失败")


@model_providers.post("/models/cache/refresh")
async def refresh_model_cache(
    current_user: User = Depends(get_superadmin_user),
):
    """强制刷新模型缓存，从数据库重新加载所有供应商配置到 Redis。"""
    await _refresh_model_cache()
    from yuxi.models.providers.cache import model_cache

    return {"success": True, "message": "缓存已刷新", "model_count": len(model_cache.get_all_specs())}


@model_providers.get("/models/v2")
async def get_v2_models(
    model_type: str = "chat",
    _current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """获取 v2 格式的模型列表，按 provider 分组。

    v2 模型 spec 格式: provider_id:model_id（冒号分隔）
    返回数据供前端模型选择器使用。
    """
    from yuxi.models.providers.cache import model_cache

    grouped = model_cache.get_specs_grouped_by_provider(model_type)
    providers = await get_all_model_providers(db, user=_current_user)
    provider_name_by_id = {
        provider.resource_id: provider.display_name or provider.provider_id for provider in providers
    }

    result = {}
    visible_provider_by_id = {provider.resource_id: provider for provider in providers}
    for resource_id, models in grouped.items():
        provider = visible_provider_by_id.get(resource_id)
        if provider is None:
            continue
        result[resource_id] = {
            "resource_id": resource_id,
            "provider_id": provider.provider_id if provider else resource_id,
            "provider_display_name": provider_name_by_id.get(resource_id, resource_id),
            "share_config": provider.share_config if provider else None,
            "models": [
                {
                    "spec": m.spec,
                    "model_id": m.model_id,
                    "display_name": m.display_name,
                    "dimension": m.dimension,
                    "batch_size": m.batch_size,
                }
                for m in models
            ],
        }

    return {"success": True, "data": result}


@model_providers.get("/models/status")
async def get_model_status_by_spec(
    spec: str,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """根据 full spec 检查模型状态（自动识别 V1/V2、Chat/Embedding）。"""
    try:
        from yuxi.models.providers.cache import model_cache

        canonical_spec = model_cache.canonicalize_spec(spec)
        if canonical_spec is None:
            raise HTTPException(status_code=404, detail="模型资源不存在或存在歧义")
        resource_id = canonical_spec.split(":", 1)[0]
        if not resource_id:
            raise HTTPException(status_code=404, detail="模型资源不存在或不可访问")
        await _get_manageable_provider(db, resource_id, current_user)
        result = await test_model_status_by_spec(canonical_spec)
        return {"success": True, "data": result}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"测试模型状态失败 {spec}: error_type={type(e).__name__}")
        return {"success": False, "data": {"spec": spec, "status": "error", "message": "模型连接检查失败"}}
