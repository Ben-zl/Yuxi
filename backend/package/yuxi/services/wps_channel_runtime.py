"""Yuxi 所有的 WPS 长连接和回复投递运行时。"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

from yuxi.channels.wps_xiezuo import WPSXiezuoTransport
from yuxi.repositories.agent_run_repository import AgentRunRepository, TERMINAL_RUN_STATUSES
from yuxi.repositories.agent_run_request_repository import AgentRunRequestRepository
from yuxi.repositories.agentscope_channel_bindings import AgentScopeChannelBindingRepository
from yuxi.repositories.channel_delivery_repository import ChannelDeliveryRepository
from yuxi.services.run_queue_service import get_redis_client
from yuxi.services.wps_channel_ingress_service import submit_wps_channel_event
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import Message
from yuxi.utils.logging_config import logger

STATUS_TTL_SECONDS = 30
RECONCILE_INTERVAL_SECONDS = 5.0
DELIVERY_INTERVAL_SECONDS = 1.0


def wps_channel_status_key(binding_id: str) -> str:
    """返回单个 WPS binding 的短期运行状态 key。"""
    return f"yuxi:wps-channel:status:{binding_id}"


@dataclass
class _RunningTransport:
    fingerprint: str
    transport: WPSXiezuoTransport
    task: asyncio.Task


class WPSChannelRuntime:
    """维护 WPS 连接，并只从 Yuxi PostgreSQL Run 事实发送回复。"""

    def __init__(self) -> None:
        self._running: dict[str, _RunningTransport] = {}
        self._stopping = False

    async def run(self) -> None:
        """持续 reconcile 连接和处理持久化回复投递。"""
        pg_manager.initialize()
        await pg_manager.require_current_schema(include_knowledge=False)
        try:
            await asyncio.gather(self._reconcile_forever(), self._deliver_forever())
        finally:
            self._stopping = True
            await self._stop_all()
            await pg_manager.close()

    async def _reconcile_forever(self) -> None:
        while not self._stopping:
            try:
                await self._reconcile_once()
                await self._publish_statuses()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("WPS Channel runtime reconcile failed")
            await asyncio.sleep(RECONCILE_INTERVAL_SECONDS)

    async def _reconcile_once(self) -> None:
        async with pg_manager.get_async_session_context() as db:
            bindings = await AgentScopeChannelBindingRepository(db).list_all()
        desired = {item.id: item for item in bindings if item.enabled and item.sync_status == "synced"}

        for binding_id in list(self._running):
            binding = desired.get(binding_id)
            current = self._running[binding_id]
            if binding is None or current.fingerprint != self._fingerprint(binding) or current.task.done():
                await self._stop(binding_id)

        for binding_id, binding in desired.items():
            if binding_id in self._running:
                continue
            transport = WPSXiezuoTransport(
                binding.id,
                WPSXiezuoTransport.Credentials(
                    app_id=binding.app_id,
                    app_secret=binding.encrypted_app_secret,
                ),
                WPSXiezuoTransport.Config(
                    allow_from=binding.allow_from or [],
                    group_reply_policy=binding.group_reply_policy,
                ),
            )
            task = asyncio.create_task(
                transport.start_listening(
                    lambda event, current_id=binding.id: submit_wps_channel_event(current_id, event),
                ),
                name=f"wps-channel-{binding.id}",
            )
            self._running[binding_id] = _RunningTransport(self._fingerprint(binding), transport, task)

    async def _deliver_forever(self) -> None:
        while not self._stopping:
            try:
                await self._deliver_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("WPS Channel delivery loop failed")
            await asyncio.sleep(DELIVERY_INTERVAL_SECONDS)

    async def _deliver_once(self) -> None:
        async with pg_manager.get_async_session_context() as db:
            deliveries = ChannelDeliveryRepository(db)
            claimed = await deliveries.claim_ready(limit=10)
            for delivery in claimed:
                binding = await AgentScopeChannelBindingRepository(db).get(delivery.binding_id)
                transport = self._running.get(delivery.binding_id)
                request = await AgentRunRequestRepository(db).get_by_request_id(delivery.request_id)
                run = await AgentRunRepository(db).get_run_by_request_id(delivery.request_id)
                if binding is None or not binding.enabled:
                    await deliveries.mark_retry(delivery, RuntimeError("binding unavailable"))
                    continue
                if request is None or run is None or run.status not in TERMINAL_RUN_STATUSES:
                    await deliveries.mark_pending(delivery)
                    continue
                if transport is None or transport.task.done():
                    await deliveries.mark_retry(delivery, RuntimeError("transport unavailable"))
                    continue

                text = await self._delivery_text(db, run)
                try:
                    await transport.transport.send_markdown(delivery.chat_id, text)
                except Exception as exc:
                    await deliveries.mark_retry(delivery, exc)
                else:
                    await deliveries.mark_sent(delivery)
            await db.commit()

    @staticmethod
    async def _delivery_text(db, run) -> str:
        """从同一 Run 的输出或终态构造 WPS 回复。"""
        if run.status == "completed" and run.output_message_id:
            output = await db.get(Message, run.output_message_id)
            if output is not None and output.run_id == run.id:
                return output.content or "任务已完成。"
        if run.status == "interrupted":
            return "任务正在等待用户确认，请在 Yuxi 对话中完成审批或回答。"
        if run.status == "completed":
            return "任务已完成，但未找到该次运行的输出，请在 Yuxi 对话中查看详情。"
        if run.status == "cancelled":
            return "任务已取消。"
        return f"任务执行失败：{getattr(run, 'error_message', None) or '未提供错误详情'}"

    async def _publish_statuses(self) -> None:
        redis = await get_redis_client()
        for binding_id, running in self._running.items():
            status = running.transport.status
            payload = json.dumps(
                {"state": status.state, "last_error": status.last_error},
                ensure_ascii=False,
            )
            await redis.set(wps_channel_status_key(binding_id), payload, ex=STATUS_TTL_SECONDS)

    @staticmethod
    def _fingerprint(binding) -> str:
        return json.dumps(
            {
                "app_id": binding.app_id,
                "secret": binding.encrypted_app_secret,
                "allow_from": binding.allow_from or [],
                "group_reply_policy": binding.group_reply_policy,
            },
            sort_keys=True,
            ensure_ascii=False,
        )

    async def _stop(self, binding_id: str) -> None:
        current = self._running.pop(binding_id, None)
        try:
            if current is not None:
                current.task.cancel()
                await asyncio.gather(current.task, return_exceptions=True)
                await current.transport.aclose()
        finally:
            await self._delete_status(binding_id)

    @staticmethod
    async def _delete_status(binding_id: str) -> None:
        """尽力删除已停止连接的短期状态，不影响 transport 收束。"""
        try:
            redis = await get_redis_client()
            await redis.delete(wps_channel_status_key(binding_id))
        except Exception:
            logger.exception("Failed to delete stopped WPS Channel runtime status: %s", binding_id)

    async def _stop_all(self) -> None:
        for binding_id in list(self._running):
            await self._stop(binding_id)
