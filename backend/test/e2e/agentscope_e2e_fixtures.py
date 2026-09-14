"""agentscope e2e 共享夹具（迁移工单）。

mock 供应商行被多个 e2e 文件共用（provider_id 相同），必须幂等写入；
智能体行按 slug 清理。避免重复 INSERT 互相冲突。
"""

import asyncio
import os
from collections.abc import Awaitable, Callable

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.models_business import (
    Agent,
    ModelProvider,
    User,
)
from yuxi.utils.auth_utils import AuthUtils

PROVIDER_ID = "e2e-openai-mock"
PROVIDER_RESOURCE_ID = "e2e-openai-mock-resource"
FIXTURE_PASSWORD = "e2e-fixture-password"


async def run_e2e_cleanup_steps(
    steps: list[tuple[str, Callable[[], Awaitable[None]]]],
    *,
    primary_error: BaseException | None,
) -> None:
    """顺序执行全部清理，保留主体异常并单独报告清理失败。"""
    failures: list[tuple[str, BaseException]] = []
    for label, cleanup in steps:
        try:
            await cleanup()
        except asyncio.CancelledError as exc:
            exc.add_note(f"E2E cleanup step: {label}")
            failures.append((label, exc))
        except BaseExceptionGroup as exc:
            exc.add_note(f"E2E cleanup step: {label}")
            failures.append((label, exc))
        except Exception as exc:
            exc.add_note(f"E2E cleanup step: {label}")
            failures.append((label, exc))

    if not failures:
        return
    if primary_error is not None:
        summary = ", ".join(f"{label} ({type(exc).__name__})" for label, exc in failures)
        primary_error.add_note(f"E2E cleanup also failed: {summary}")
        return
    raise BaseExceptionGroup("E2E cleanup failed", [exc for _, exc in failures])


async def open_fixture_http_client(uid: str) -> tuple[httpx.AsyncClient, dict[str, str]]:
    """登录夹具用户，返回正式 Yuxi HTTP Run 客户端和授权头。"""
    client = httpx.AsyncClient(
        base_url=os.getenv("TEST_BASE_URL", "http://api:5050").rstrip("/"),
        timeout=httpx.Timeout(300.0, connect=10.0),
        follow_redirects=True,
    )
    response = await client.post("/api/auth/token", data={"username": uid, "password": FIXTURE_PASSWORD})
    if response.status_code != 200:
        await client.aclose()
        raise AssertionError(f"fixture user login failed (status={response.status_code}): {response.text}")
    token = response.json().get("access_token")
    if not token:
        await client.aclose()
        raise AssertionError("fixture user login returned no access token")
    return client, {"Authorization": f"Bearer {token}"}


async def upsert_mock_provider(db: AsyncSession) -> None:
    """幂等写入 OpenAI 兼容 mock 供应商（指向 openai-mock 容器）。"""
    provider = await db.scalar(select(ModelProvider).where(ModelProvider.provider_id == PROVIDER_ID))
    if provider is not None and provider.resource_id != PROVIDER_RESOURCE_ID:
        # 旧运行可能留下随机 resource_id；测试固定资源 ID，避免把历史缓存
        # 或旧配置误当成当前夹具资源。生产资源 ID 不允许这样修改。
        await db.delete(provider)
        await db.flush()
        provider = None
    if provider is None:
        provider = ModelProvider(provider_id=PROVIDER_ID, resource_id=PROVIDER_RESOURCE_ID)
        db.add(provider)
    provider.display_name = "e2e mock provider"
    provider.provider_type = "openai"
    provider.base_url = os.getenv("OPENAI_MOCK_BASE_URL", os.getenv("OPENAI_MOCK_URL", "http://openai-mock:8080/v1"))
    provider.api_key = "e2e-mock-key"
    provider.capabilities = ["chat"]
    provider.enabled_models = [{"id": "mock-chat-model", "type": "chat", "context_length": 32768}]
    provider.is_enabled = True
    provider.share_config = {
        "version": 2,
        "read_scope": {"access_level": "global", "department_ids": [], "user_uids": []},
        "manage_scope": None,
    }
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
            password_hash=AuthUtils.hash_password(FIXTURE_PASSWORD),
            role="superadmin",
            department_id=1,
        )
        for uid in uids
    )
    await db.commit()


async def cleanup_test_users(db: AsyncSession, *uids: str) -> None:
    """清理 E2E 用户及其 Yuxi 侧 AgentScope 映射。"""
    if uids:
        await db.commit()
        from test.live_api_cleanup import cleanup_static_test_user_resources

        for uid in uids:
            await cleanup_static_test_user_resources(uid)
        db.expire_all()
    await db.commit()
