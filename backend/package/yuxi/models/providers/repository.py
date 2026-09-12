"""模型供应商配置数据访问层。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.models_business import ModelProvider


async def list_all_model_providers_internal(db: AsyncSession) -> list[ModelProvider]:
    """仅供系统缓存和内置同步读取全部模型供应商。"""
    result = await db.execute(
        select(ModelProvider).order_by(ModelProvider.is_enabled.desc(), ModelProvider.display_name.asc())
    )
    return list(result.scalars().all())


async def list_model_providers(db: AsyncSession, *, user) -> list[ModelProvider]:
    """获取当前用户可读的模型供应商配置。"""
    if user is None:
        raise PermissionError("模型供应商列表需要当前用户授权上下文")
    providers = await list_all_model_providers_internal(db)
    from yuxi.permissions.resource_permission import resolve_model_provider_permission

    return [p for p in providers if resolve_model_provider_permission(user, p).value != "none"]


async def get_model_provider(db: AsyncSession, provider_id: str) -> ModelProvider | None:
    """按 provider_id 获取模型供应商配置。"""
    result = await db.execute(
        select(ModelProvider).where(ModelProvider.provider_id == provider_id).order_by(ModelProvider.id.asc()).limit(1)
    )
    return result.scalars().first()


async def get_model_provider_by_resource_id(db: AsyncSession, resource_id: str) -> ModelProvider | None:
    """按全局不可变资源 ID 获取模型供应商。"""
    result = await db.execute(select(ModelProvider).where(ModelProvider.resource_id == resource_id))
    return result.scalar_one_or_none()


async def get_builtin_model_provider(db: AsyncSession, provider_id: str) -> ModelProvider | None:
    """按逻辑 ID读取代码管理的内置模型供应商。"""
    result = await db.execute(
        select(ModelProvider).where(
            ModelProvider.provider_id == provider_id,
            ModelProvider.is_builtin.is_(True),
        )
    )
    return result.scalar_one_or_none()


async def get_model_provider_for_user(db: AsyncSession, resource_id: str, user) -> ModelProvider | None:
    """按资源 ID 获取当前用户可读的模型供应商。"""
    provider = await get_model_provider_by_resource_id(db, resource_id)
    if provider is None:
        return None
    from yuxi.permissions.resource_permission import resolve_model_provider_permission

    return provider if resolve_model_provider_permission(user, provider).value != "none" else None


async def get_model_provider_reference(db: AsyncSession, reference: str, *, user) -> ModelProvider | None:
    """解析资源 ID；旧 provider_id 仅在唯一时转换，重复时拒绝歧义引用。"""
    if user is None:
        raise PermissionError("模型供应商引用需要当前用户授权上下文")
    provider = await get_model_provider_by_resource_id(db, reference)
    if provider is None:
        result = await db.execute(select(ModelProvider).where(ModelProvider.provider_id == reference))
        matches = list(result.scalars().all())
        provider = matches[0] if len(matches) == 1 else None
    if provider is None:
        return provider
    from yuxi.permissions.resource_permission import resolve_model_provider_permission

    return provider if resolve_model_provider_permission(user, provider).value != "none" else None


async def get_legacy_model_provider_for_user(db: AsyncSession, provider_id: str, *, user) -> ModelProvider | None:
    """仅按旧 provider_id 唯一解析，避免与其他资源的 resource_id 命名空间碰撞。"""

    if user is None:
        raise PermissionError("旧模型供应商引用需要当前用户授权上下文")
    result = await db.execute(select(ModelProvider).where(ModelProvider.provider_id == provider_id))
    matches = list(result.scalars().all())
    if len(matches) != 1:
        return None

    from yuxi.permissions.resource_permission import resolve_model_provider_permission

    provider = matches[0]
    return provider if resolve_model_provider_permission(user, provider).value != "none" else None


async def create_model_provider(db: AsyncSession, data: dict) -> ModelProvider:
    """创建模型供应商配置。"""
    provider = ModelProvider(**data)
    db.add(provider)
    await db.flush()
    await db.refresh(provider)
    return provider


async def update_model_provider(db: AsyncSession, provider: ModelProvider, data: dict) -> ModelProvider:
    """更新模型供应商配置。"""
    for key, value in data.items():
        if key != "provider_id":
            setattr(provider, key, value)
    await db.flush()
    await db.refresh(provider)
    return provider


async def delete_model_provider(db: AsyncSession, provider: ModelProvider) -> None:
    """删除模型供应商配置。"""
    await db.delete(provider)
    await db.flush()
