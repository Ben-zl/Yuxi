"""WPS Channel 投递事实的真实 PostgreSQL 并发回归。"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.repositories.channel_delivery_repository import ChannelDeliveryRepository
from yuxi.storage.postgres.models_business import AgentScopeChannelBinding, ChannelDelivery

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


def _binding(binding_id: str) -> AgentScopeChannelBinding:
    """创建不依赖 AgentScope 原生 Channel 的 Yuxi binding。"""
    return AgentScopeChannelBinding(
        id=binding_id,
        owner_uid=f"owner-{binding_id}",
        agent_slug="assistant",
        name="WPS delivery integration",
        channel_type="wps_xiezuo",
        app_id=f"app-{binding_id}",
        encrypted_app_secret="encrypted-test-secret",
        allow_from=[],
        group_reply_policy="mention_only",
        enabled=True,
        sync_status="synced",
        created_by="pytest",
        updated_by="pytest",
    )


async def test_delivery_intent_is_idempotent_and_reports_creation() -> None:
    """同一 request 只创建一个回复投递事实。"""
    binding_id = str(uuid.uuid4())
    request_id = f"wps-request-{uuid.uuid4()}"
    engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with factory() as db:
            db.add(_binding(binding_id))
            await db.flush()
            repo = ChannelDeliveryRepository(db)
            first, first_created = await repo.create_if_missing(
                binding_id=binding_id,
                request_id=request_id,
                channel_message_id="message-1",
                chat_id="chat-1",
            )
            second, second_created = await repo.create_if_missing(
                binding_id=binding_id,
                request_id=request_id,
                channel_message_id="message-1",
                chat_id="chat-1",
            )
            await db.commit()

            assert first.id == second.id
            assert first_created is True
            assert second_created is False

        async with factory() as db:
            rows = (
                await db.execute(select(ChannelDelivery).where(ChannelDelivery.request_id == request_id))
            ).scalars().all()
            assert len(rows) == 1
    finally:
        async with factory() as db:
            await db.execute(delete(ChannelDelivery).where(ChannelDelivery.binding_id == binding_id))
            await db.execute(delete(AgentScopeChannelBinding).where(AgentScopeChannelBinding.id == binding_id))
            await db.commit()
        await engine.dispose()


async def test_concurrent_delivery_intake_creates_one_fact() -> None:
    """两个 WPS intake 并发到达时必须复用同一投递事实。"""
    binding_id = str(uuid.uuid4())
    request_id = f"wps-request-{uuid.uuid4()}"
    engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def create_delivery() -> tuple[int, bool]:
        async with factory() as db:
            delivery, created = await ChannelDeliveryRepository(db).create_if_missing(
                binding_id=binding_id,
                request_id=request_id,
                channel_message_id="message-concurrent",
                chat_id="chat-concurrent",
            )
            await db.commit()
            return delivery.id, created

    try:
        async with factory() as db:
            db.add(_binding(binding_id))
            await db.commit()

        results = await asyncio.gather(create_delivery(), create_delivery())
        assert len({delivery_id for delivery_id, _created in results}) == 1
        assert sorted(created for _delivery_id, created in results) == [False, True]

        async with factory() as db:
            rows = (
                await db.execute(select(ChannelDelivery).where(ChannelDelivery.request_id == request_id))
            ).scalars().all()
            assert len(rows) == 1
    finally:
        async with factory() as db:
            await db.execute(delete(ChannelDelivery).where(ChannelDelivery.binding_id == binding_id))
            await db.execute(delete(AgentScopeChannelBinding).where(AgentScopeChannelBinding.id == binding_id))
            await db.commit()
        await engine.dispose()


async def test_delivery_claim_uses_skip_locked_across_runtime_instances() -> None:
    """两个运行时不能同时取得同一条待发送记录。"""
    binding_id = str(uuid.uuid4())
    request_id = f"wps-request-{uuid.uuid4()}"
    engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with factory() as db:
            db.add(_binding(binding_id))
            await db.flush()
            await ChannelDeliveryRepository(db).create_if_missing(
                binding_id=binding_id,
                request_id=request_id,
                channel_message_id="message-1",
                chat_id="chat-1",
            )
            await db.commit()

        async with factory() as first_db, factory() as second_db:
            first_claim = await ChannelDeliveryRepository(first_db).claim_ready(limit=1)
            second_claim = await ChannelDeliveryRepository(second_db).claim_ready(limit=1)

            assert [item.request_id for item in first_claim] == [request_id]
            assert second_claim == []

            await first_db.rollback()
            await second_db.rollback()

        async with factory() as db:
            delivery = await db.scalar(select(ChannelDelivery).where(ChannelDelivery.request_id == request_id))
            assert delivery is not None
            assert delivery.status == "pending"
            assert delivery.attempt_count == 0
    finally:
        async with factory() as db:
            await db.execute(delete(ChannelDelivery).where(ChannelDelivery.binding_id == binding_id))
            await db.execute(delete(AgentScopeChannelBinding).where(AgentScopeChannelBinding.id == binding_id))
            await db.commit()
        await engine.dispose()
