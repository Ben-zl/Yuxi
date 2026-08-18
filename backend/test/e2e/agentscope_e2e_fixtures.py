"""agentscope e2e 共享夹具（迁移工单）。

mock 供应商行被多个 e2e 文件共用（provider_id 相同），必须幂等写入；
智能体行按 slug 清理。避免重复 INSERT 互相冲突。
"""

import os

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.models_business import (
    Agent,
    AgentScopeThreadSession,
    ModelProvider,
    User,
)

PROVIDER_ID = "e2e-openai-mock"


async def upsert_mock_provider(db: AsyncSession) -> None:
    """幂等写入 OpenAI 兼容 mock 供应商（指向 openai-mock 容器）。"""
    provider = await db.scalar(select(ModelProvider).where(ModelProvider.provider_id == PROVIDER_ID))
    if provider is None:
        provider = ModelProvider(provider_id=PROVIDER_ID)
        db.add(provider)
    provider.display_name = "e2e mock provider"
    provider.provider_type = "openai"
    provider.base_url = os.getenv("OPENAI_MOCK_BASE_URL", os.getenv("OPENAI_MOCK_URL", "http://openai-mock:8080/v1"))
    provider.api_key = "e2e-mock-key"
    provider.capabilities = ["chat"]
    provider.enabled_models = [{"id": "mock-chat-model", "type": "chat", "context_length": 32768}]
    provider.is_enabled = True
    await db.commit()


async def cleanup_fixture_agents(db: AsyncSession, *slugs: str) -> None:
    """删除指定 slug 的测试智能体行（供应商行共用，由 upsert 维护，不在此删除）。

    夹具插入前也调用一次，保证幂等（历史失败运行可能残留行）。
    """
    if slugs:
        await db.execute(delete(Agent).where(Agent.slug.in_(slugs)))
    await db.commit()


async def seed_test_users(db: AsyncSession, *uids: str) -> None:
    """创建具备完整可见性的隔离 E2E 用户。"""
    await cleanup_test_users(db, *uids)
    db.add_all(
        User(
            uid=uid,
            username=uid,
            password_hash="test-only",
            role="superadmin",
        )
        for uid in uids
    )
    await db.commit()


async def cleanup_test_users(db: AsyncSession, *uids: str) -> None:
    """清理 E2E 用户及其 Yuxi 侧 AgentScope 映射。"""
    if uids:
        await db.execute(delete(AgentScopeThreadSession).where(AgentScopeThreadSession.uid.in_(uids)))
        await db.execute(delete(User).where(User.uid.in_(uids)))
    await db.commit()
