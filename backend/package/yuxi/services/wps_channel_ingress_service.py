"""把 WPS 消息信封转换为统一 Yuxi Run 提交。"""

from __future__ import annotations

from yuxi.channels.wps_xiezuo import WPSChannelEvent

from yuxi.repositories.agent_run_repository import AgentRunRepository
from yuxi.repositories.agent_run_request_repository import AgentRunRequestRepository
from yuxi.repositories.agentscope_channel_bindings import AgentScopeChannelBindingRepository
from yuxi.repositories.channel_delivery_repository import ChannelDeliveryRepository
from yuxi.repositories.user_repository import UserRepository
from yuxi.services.input_message_service import build_chat_input_message
from yuxi.services.run_submission_service import (
    RunOrigin,
    RunSubmissionAttachment,
    RunSubmissionCommand,
    submit_run_command,
)
from yuxi.storage.postgres.manager import pg_manager
from yuxi.utils.hash_utils import hash_id


async def submit_wps_channel_event(binding_id: str, event: WPSChannelEvent) -> dict:
    """验证 binding，并在执行任何 Agent 前持久化投递与 Run 请求。"""
    async with pg_manager.get_async_session_context() as db:
        binding = await AgentScopeChannelBindingRepository(db).get(binding_id)
        if binding is None or not binding.enabled or binding.sync_status != "synced":
            raise ValueError("WPS Channel binding 未启用或尚未就绪")
        if event.channel_id != binding.id:
            raise ValueError("WPS Channel event 与 binding 不匹配")
        allowed_senders = {str(item).strip() for item in (binding.allow_from or []) if str(item).strip()}
        sender_id = str(event.channel_user_id or "").strip()
        if not allowed_senders or sender_id not in allowed_senders:
            raise ValueError("WPS Channel sender 不在 binding allow_from 白名单中")

        current_user = await UserRepository(db).get_by_uid(binding.owner_uid)
        if current_user is None or current_user.is_deleted:
            raise ValueError("WPS Channel owner 不存在或已停用")

        message_id = str(event.channel_message_id or "").strip()
        chat_id = str(event.chat_id or "").strip()
        if not message_id or not chat_id:
            raise ValueError("WPS Channel 消息缺少稳定标识")

        thread_id = hash_id(
            "channel_",
            f"{binding.owner_uid}:wps_xiezuo:{binding.id}:{chat_id}",
            length=64,
        )
        request_id = hash_id(
            "wps_request_",
            f"{binding.id}:{message_id}",
            length=64,
        )
        text, attachments = _convert_event_content(event)
        origin_metadata = {
            key: value
            for key, value in {
                "binding_id": binding.id,
                "chat_id": chat_id,
                "chat_type": event.metadata.get("chat_type"),
                "company_id": event.metadata.get("company_id"),
                "sender_id": event.channel_user_id,
                "received_at": event.received_at,
            }.items()
            if value not in {None, ""}
        }

        deliveries = ChannelDeliveryRepository(db)
        delivery, delivery_created = await deliveries.create_if_missing(
            binding_id=binding.id,
            request_id=request_id,
            channel_message_id=message_id,
            chat_id=chat_id,
        )
        await db.commit()
        existing_request = await AgentRunRequestRepository(db).get_by_request_id(request_id)

        try:
            return await submit_run_command(
                command=RunSubmissionCommand(
                    agent_slug=binding.agent_slug,
                    thread_id=thread_id,
                    request_id=request_id,
                    input_message=build_chat_input_message(text),
                    origin=RunOrigin(
                        source="channel",
                        channel="wps_xiezuo",
                        external_id=message_id,
                        metadata=origin_metadata,
                    ),
                    request_metadata={"message_type": "text"},
                    model_spec=None,
                    queue_policy="enqueue",
                    create_conversation=True,
                    conversation_title=f"WPS 协作 · {binding.name}",
                    attachments=() if existing_request is not None else attachments,
                ),
                current_user=current_user,
                db=db,
            )
        except Exception:
            await db.rollback()
            accepted_request = await AgentRunRequestRepository(db).get_by_request_id(request_id)
            accepted_run = await AgentRunRepository(db).get_run_by_request_id(request_id)
            existing = await deliveries.get_by_request_id(request_id)
            if (
                delivery_created
                and accepted_request is None
                and accepted_run is None
                and existing is not None
                and existing.id == delivery.id
            ):
                await deliveries.delete(existing)
                await db.commit()
            raise


def _convert_event_content(event: WPSChannelEvent) -> tuple[str, tuple[RunSubmissionAttachment, ...]]:
    """把 WPS 文本和附件转换为 Yuxi 输入。"""
    attachments = tuple(
        RunSubmissionAttachment(
            file_name=item.name,
            media_type=item.media_type,
            content=item.content,
        )
        for item in event.attachments
    )
    text = event.text.strip()
    if not text:
        if not attachments:
            raise ValueError("WPS 消息不包含可执行内容")
        text = "请处理本次请求所附文件。"
    return text, attachments
