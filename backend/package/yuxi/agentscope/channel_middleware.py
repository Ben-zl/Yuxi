"""将 AgentScope 原生 WPS Channel 运行事实镜像到 Yuxi。"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from agentscope.middleware import MiddlewareBase

from yuxi.agentscope.protocol import ToolEventConverter, reply_end_to_terminal
from yuxi.agentscope.usage import UsageAccumulator
from yuxi.repositories.agent_run_repository import (
    AgentRunRepository,
    TERMINAL_RUN_STATUSES,
)
from yuxi.repositories.agentscope_channel_bindings import (
    AgentScopeChannelBindingRepository,
)
from yuxi.repositories.agentscope_thread_sessions import (
    get_thread_session_by_agentscope_context,
)
from yuxi.repositories.conversation_repository import ConversationRepository
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import Message, ToolCall
from yuxi.utils.logging_config import logger


def _input_message(value: Any) -> tuple[str, dict]:
    """从 AgentScope UserMsg 提取正文和 Channel metadata。"""
    if isinstance(value, list):
        for item in value:
            text, metadata = _input_message(item)
            if metadata.get("channel_message_id"):
                return text, metadata
        return "", {}
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if not isinstance(value, dict):
        return "", {}
    metadata = dict(value.get("metadata") or {})
    content = value.get("content")
    if isinstance(content, str):
        return content, metadata
    if isinstance(content, list):
        text = "".join(
            str(block.get("text") or "") for block in content if isinstance(block, dict) and block.get("type") == "text"
        )
        return text, metadata
    return "", metadata


class ChannelRunMirrorMiddleware(MiddlewareBase):
    """在不接管执行的前提下镜像 WPS Channel Run 生命周期。"""

    def __init__(self, *, uid: str, agent_id: str, session_id: str):
        self.uid = uid
        self.agent_id = agent_id
        self.session_id = session_id

    async def on_reply(self, agent, input_kwargs, next_handler):
        """透传原生 Reply，并以 WPS message ID 幂等保存运行事实。"""
        text, metadata = _input_message(input_kwargs.get("inputs"))
        channel_id = str(metadata.get("channel_id") or "")
        message_id = str(metadata.get("channel_message_id") or "")
        if not channel_id or not message_id:
            async for item in next_handler(**input_kwargs):
                yield item
            return

        run_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"yuxi:wps:{channel_id}:{message_id}"))
        request_id = run_id
        opened = False
        try:
            opened = await self._open_run(
                run_id=run_id,
                request_id=request_id,
                text=text,
                metadata=metadata,
            )
        except Exception as exc:  # noqa: BLE001 - 投影不得中断模型调用
            logger.exception("WPS Channel Run 起始投影失败 run=%s: %s", run_id, exc)

        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        usage = UsageAccumulator(configured_model_spec=None)
        tools = ToolEventConverter(request_id)
        terminal = None
        error_message = None
        finalized = False
        try:
            async for item in next_handler(**input_kwargs):
                event = item.model_dump(mode="json") if hasattr(item, "model_dump") else item
                if opened and isinstance(event, dict):
                    usage.observe(event)
                    event_type = str(event.get("type") or "").upper()
                    if event_type == "TEXT_BLOCK_DELTA":
                        text_parts.append(str(event.get("delta") or ""))
                    elif event_type == "THINKING_BLOCK_DELTA":
                        reasoning_parts.append(str(event.get("delta") or ""))
                    elif event_type == "REPLY_END":
                        terminal = reply_end_to_terminal(event, request_id=request_id)
                        error_message = (event.get("error") or {}).get("message") or terminal.chunk.get(
                            "error_message",
                        )
                    else:
                        tools.feed(event)
                yield item

            if opened:
                try:
                    if terminal is None:
                        await self._finish_run(
                            run_id=run_id,
                            status="failed",
                            error_type="agentscope_channel",
                            error_message="AgentScope Channel reply stream ended without REPLY_END",
                            text="".join(text_parts),
                            reasoning="".join(reasoning_parts),
                            usage=usage.snapshot(complete=False),
                            tool_calls=tools.history_tool_calls(),
                        )
                    else:
                        await self._finish_run(
                            run_id=run_id,
                            status=terminal.run_status,
                            error_type=terminal.chunk.get("error_type"),
                            error_message=error_message,
                            text="".join(text_parts),
                            reasoning="".join(reasoning_parts),
                            usage=usage.snapshot(complete=True),
                            tool_calls=tools.history_tool_calls(),
                        )
                except Exception:  # noqa: BLE001 - 回复投递不依赖 Yuxi 投影
                    logger.exception("WPS Channel Run 终态投影失败 run=%s", run_id)
                finalized = True
        except BaseException as exc:
            if opened and not finalized:
                try:
                    cancelled = isinstance(exc, asyncio.CancelledError)
                    await self._finish_run(
                        run_id=run_id,
                        status="cancelled" if cancelled else "failed",
                        error_type=None if cancelled else "agentscope_channel",
                        error_message=None if cancelled else type(exc).__name__,
                        text="".join(text_parts),
                        reasoning="".join(reasoning_parts),
                        usage=usage.snapshot(complete=False),
                        tool_calls=tools.history_tool_calls(),
                    )
                except Exception:  # noqa: BLE001 - 保留原执行异常
                    logger.exception("WPS Channel Run 异常终态投影失败 run=%s", run_id)
            raise

    async def _open_run(
        self,
        *,
        run_id: str,
        request_id: str,
        text: str,
        metadata: dict,
    ) -> bool:
        """幂等创建用户 Message 和 running AgentRun。"""
        async with pg_manager.get_async_session_context() as db:
            runs = AgentRunRepository(db)
            existing = await runs.get_run_by_request_id(request_id)
            if existing is not None:
                return existing.status not in TERMINAL_RUN_STATUSES
            mapping = await get_thread_session_by_agentscope_context(
                db,
                uid=self.uid,
                agentscope_agent_id=self.agent_id,
                agentscope_session_id=self.session_id,
            )
            if mapping is None:
                raise ValueError("WPS Channel Session 尚未建立 Yuxi Thread 映射")
            binding = await AgentScopeChannelBindingRepository(db).get_by_channel_id(
                str(metadata["channel_id"]),
            )
            if binding is None:
                raise ValueError("WPS Channel binding 不存在")
            conversation = await ConversationRepository(db).get_conversation_by_thread_id(
                mapping.thread_id,
            )
            if conversation is None:
                raise ValueError("WPS Channel Conversation 不存在")

            origin = {
                key: metadata.get(key)
                for key in (
                    "channel_type",
                    "channel_id",
                    "channel_message_id",
                    "chat_id",
                    "chat_type",
                    "company_id",
                    "sender_id",
                    "received_at",
                )
                if metadata.get(key) is not None
            }
            message = Message(
                conversation_id=conversation.id,
                role="user",
                content=text,
                message_type="text",
                request_id=request_id,
                delivery_status="complete",
                extra_metadata={"source": "agentscope_channel", **origin},
            )
            db.add(message)
            await db.flush()
            run = await runs.create_run(
                run_id=run_id,
                conversation_thread_id=mapping.thread_id,
                agent_slug=binding.agent_slug,
                uid=binding.owner_uid,
                request_id=request_id,
                input_payload={"model_spec": mapping.model_spec},
                source="agentscope_channel",
                channel="wps_xiezuo",
                external_id=str(metadata["channel_message_id"]),
                origin_metadata=origin,
                conversation_id=conversation.id,
                run_type="chat",
                input_message_id=message.id,
            )
            await runs.mark_running(run.id)
            await db.commit()
            return True

    async def _finish_run(
        self,
        *,
        run_id: str,
        status: str,
        error_type: str | None,
        error_message: str | None,
        text: str,
        reasoning: str,
        usage: dict,
        tool_calls: list[dict],
    ) -> None:
        """原子保存助手消息、工具事实、用量和终态。"""
        async with pg_manager.get_async_session_context() as db:
            runs = AgentRunRepository(db)
            run = await runs.get_run(run_id)
            if run is None or run.status in TERMINAL_RUN_STATUSES:
                return
            output = None
            if text or reasoning or tool_calls:
                output = Message(
                    conversation_id=run.conversation_id,
                    role="assistant",
                    content=text,
                    message_type="text",
                    run_id=run.id,
                    request_id=run.request_id,
                    delivery_status="complete",
                    extra_metadata={
                        "source": "agentscope_channel",
                        "run_id": run.id,
                        "token_usage": usage,
                        "additional_kwargs": {"reasoning_content": reasoning} if reasoning else {},
                    },
                )
                db.add(output)
                await db.flush()
                for call in tool_calls:
                    db.add(
                        ToolCall(
                            message_id=output.id,
                            langgraph_tool_call_id=call.get("id"),
                            tool_name=call.get("name") or "unknown",
                            tool_input=call.get("args") or {},
                            tool_output=call.get("output") or "",
                            status=call.get("status") or "pending",
                            error_message=call.get("error_message"),
                        ),
                    )
                await runs.set_output_message(run.id, output.id)
            await runs.set_terminal_status(
                run.id,
                status=status,
                error_type=error_type if status == "failed" else None,
                error_message=error_message,
                token_usage=usage,
            )
            await db.commit()


def build_channel_run_mirror_middleware(
    *,
    uid: str,
    agent_id: str,
    session_id: str,
) -> ChannelRunMirrorMiddleware:
    """创建单个 WPS Channel Run 镜像 middleware。"""
    return ChannelRunMirrorMiddleware(uid=uid, agent_id=agent_id, session_id=session_id)
