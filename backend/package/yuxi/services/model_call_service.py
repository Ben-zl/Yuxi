"""简单模型调用的当前用户授权边界。"""

import uuid

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.config import config
from yuxi.models import select_model
from yuxi.models.providers.repository import get_model_provider_reference
from yuxi.storage.postgres.models_business import User


async def call_model(*, query: str, meta: dict | None, user: User, db: AsyncSession) -> dict:
    """按数据库当前权限解析供应商，授权成功后才进入模型缓存。"""
    meta = meta or {}
    spec = meta.get("model_spec") or meta.get("model") or config.default_model
    if not isinstance(spec, str) or ":" not in spec:
        raise HTTPException(status_code=400, detail="无效的模型 spec")
    reference, model_id = spec.split(":", 1)
    provider = await get_model_provider_reference(db, reference, user=user)
    if provider is None:
        raise HTTPException(status_code=404, detail="模型不存在或无权访问")
    if not provider.is_enabled or not any(
        item.get("id") == model_id and item.get("type") == "chat" for item in provider.enabled_models or []
    ):
        raise HTTPException(status_code=404, detail="模型不可用")
    model = select_model(model_spec=f"{provider.resource_id}:{model_id}")
    response = await model.call(query)
    return {"response": response.content, "request_id": meta.get("request_id") or str(uuid.uuid4())}
