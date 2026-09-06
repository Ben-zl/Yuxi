"""外部 Channel 回复投递数据访问层。"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.models_business import ChannelDelivery
from yuxi.utils.datetime_utils import utc_now_naive


class ChannelDeliveryRepository:
    """维护 WPS 回复的幂等投递与重试状态。"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_request_id(self, request_id: str) -> ChannelDelivery | None:
        """按统一 Run request_id 查询投递。"""
        result = await self.db.execute(select(ChannelDelivery).where(ChannelDelivery.request_id == request_id))
        return result.scalar_one_or_none()

    async def create_if_missing(
        self,
        *,
        binding_id: str,
        request_id: str,
        channel_message_id: str,
        chat_id: str,
    ) -> tuple[ChannelDelivery, bool]:
        """原子创建投递事实，并安全复用并发写入的同一事实。"""
        statement = (
            insert(ChannelDelivery)
            .values(
                binding_id=binding_id,
                request_id=request_id,
                channel_message_id=channel_message_id,
                chat_id=chat_id,
                status="pending",
            )
            .on_conflict_do_nothing()
            .returning(ChannelDelivery.id)
        )
        delivery_id = await self.db.scalar(statement)
        if delivery_id is not None:
            delivery = await self.db.get(ChannelDelivery, delivery_id)
            if delivery is None:
                raise RuntimeError("新建 Channel 投递事实后无法回读")
            return delivery, True

        result = await self.db.execute(
            select(ChannelDelivery).where(
                or_(
                    ChannelDelivery.request_id == request_id,
                    and_(
                        ChannelDelivery.binding_id == binding_id,
                        ChannelDelivery.channel_message_id == channel_message_id,
                    ),
                )
            )
        )
        delivery = result.scalars().first()
        if delivery is None:
            raise RuntimeError("Channel 投递唯一键冲突后无法回读")
        if (
            delivery.request_id != request_id
            or delivery.binding_id != binding_id
            or delivery.channel_message_id != channel_message_id
            or delivery.chat_id != chat_id
        ):
            raise ValueError("Channel 消息标识已绑定其他投递事实")
        return delivery, False

    async def delete(self, delivery: ChannelDelivery) -> None:
        """删除尚未对应已接受 Run 的投递意图。"""
        await self.db.delete(delivery)
        await self.db.flush()

    async def claim_ready(self, *, limit: int = 20) -> list[ChannelDelivery]:
        """锁定一批可检查或重试的投递。"""
        now = utc_now_naive()
        result = await self.db.execute(
            select(ChannelDelivery)
            .where(
                ChannelDelivery.status.in_(("pending", "retry")),
                or_(ChannelDelivery.next_attempt_at.is_(None), ChannelDelivery.next_attempt_at <= now),
            )
            .order_by(ChannelDelivery.created_at.asc())
            .limit(limit)
            .with_for_update(skip_locked=True),
        )
        deliveries = list(result.scalars().all())
        for delivery in deliveries:
            delivery.status = "sending"
            delivery.attempt_count += 1
        await self.db.flush()
        return deliveries

    async def mark_pending(self, delivery: ChannelDelivery, *, delay_seconds: float = 1.0) -> None:
        """Run 尚未终止时延后检查，不计为平台发送失败。"""
        delivery.status = "pending"
        delivery.attempt_count = max(0, delivery.attempt_count - 1)
        delivery.next_attempt_at = utc_now_naive() + timedelta(seconds=max(0.1, delay_seconds))
        await self.db.flush()

    async def mark_retry(self, delivery: ChannelDelivery, error: Exception) -> None:
        """记录脱敏失败摘要并按次数退避。"""
        delay = min(60, 2 ** min(delivery.attempt_count, 6))
        delivery.status = "retry"
        delivery.next_attempt_at = utc_now_naive() + timedelta(seconds=delay)
        delivery.last_error = f"{type(error).__name__}: WPS delivery failed"[:500]
        await self.db.flush()

    async def mark_sent(self, delivery: ChannelDelivery) -> None:
        """提交已成功发送的终态。"""
        delivery.status = "sent"
        delivery.next_attempt_at = None
        delivery.last_error = None
        delivery.sent_at = utc_now_naive()
        await self.db.flush()
