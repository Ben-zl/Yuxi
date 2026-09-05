"""用 AgentScope 持久化消息收束失去 worker 所有权的 Yuxi Run。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select

from yuxi.agentscope.protocol import reply_end_to_terminal, sanitize_persisted_value
from yuxi.repositories.agent_run_repository import TERMINAL_RUN_STATUSES, AgentRunRepository
from yuxi.repositories.agentscope_team_workers import AgentScopeTeamWorkerRepository
from yuxi.repositories.agentscope_thread_sessions import get_thread_session
from yuxi.repositories.conversation_repository import ConversationRepository
from yuxi.services.run_queue_service import append_run_stream_event
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import AgentRun, Message, ToolCall
from yuxi.utils.logging_config import logger


@dataclass(frozen=True)
class RecoveredReply:
    """一条已持久化 AgentScope reply 的 Yuxi 可落库投影。"""

    status: str
    text: str
    reasoning: str
    tool_calls: list[dict]
    usage: dict
    error_type: str | None
    error_message: str | None


def parse_recovered_reply(message: dict, *, request_id: str, team_worker: bool = False) -> RecoveredReply:
    """从 AgentScope Message 重建 Run 输出；TeamSay 成功优先视为 worker 完成。"""
    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    calls: dict[str, dict] = {}
    call_order: list[str] = []

    for block in message.get("content") or []:
        if not isinstance(block, dict):
            continue
        block_type = str(block.get("type") or "").lower()
        if block_type == "text":
            text_parts.append(str(block.get("text") or ""))
            continue
        if block_type in {"thinking", "reasoning"}:
            reasoning_parts.append(str(block.get("thinking") or block.get("reasoning") or block.get("text") or ""))
            continue
        if block_type == "tool_call":
            tool_call_id = str(block.get("id") or "")
            if not tool_call_id:
                continue
            raw_input = block.get("input")
            try:
                args = json.loads(raw_input) if isinstance(raw_input, str) else raw_input or {}
            except (TypeError, ValueError):
                args = {"raw": str(raw_input or "")}
            if not isinstance(args, dict):
                args = {"value": args}
            calls[tool_call_id] = {
                "id": tool_call_id,
                "name": str(block.get("name") or "unknown"),
                "args": args,
                "output": "",
                "status": "pending",
                "error_message": None,
                "metadata": {},
            }
            call_order.append(tool_call_id)
            continue
        if block_type != "tool_result":
            continue
        tool_call_id = str(block.get("id") or "")
        call = calls.get(tool_call_id)
        if call is None:
            continue
        call["output"] = _content_text(block.get("output"))
        call["status"] = str(block.get("state") or "success").lower()
        call["metadata"] = block.get("metadata") if isinstance(block.get("metadata"), dict) else {}
        if call["status"] != "success":
            call["error_message"] = call["metadata"].get("error_message") or call["metadata"].get("message")

    tool_calls = sanitize_persisted_value([calls[tool_call_id] for tool_call_id in call_order])
    team_reported = team_worker and any(
        call["name"] == "TeamSay" and call["status"] == "success" for call in tool_calls
    )
    terminal = reply_end_to_terminal(
        {
            "finished_reason": "completed" if team_reported else message.get("finished_reason"),
            "error": message.get("error"),
        },
        request_id=request_id,
    )
    usage = sanitize_persisted_value(message.get("usage") if isinstance(message.get("usage"), dict) else {})
    input_tokens = int(usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or 0)
    usage.setdefault("total_tokens", input_tokens + output_tokens)
    return RecoveredReply(
        status=terminal.run_status,
        text=sanitize_persisted_value("".join(text_parts)),
        reasoning=sanitize_persisted_value("".join(reasoning_parts)),
        tool_calls=tool_calls,
        usage=usage,
        error_type=terminal.chunk.get("error_type"),
        error_message=terminal.chunk.get("error_message"),
    )


async def reconcile_active_team_child_runs(client, *, parent_run_id: str, uid: str) -> bool:
    """收束已 idle 且 reply 已落 AgentScope 的 child Run，返回是否仍有活动 child。"""
    async with pg_manager.get_async_session_context() as db:
        runs = AgentRunRepository(db)
        active_runs = await runs.list_active_child_runs_for_user(parent_run_id, uid)
        bindings = await AgentScopeTeamWorkerRepository(db).list_for_active_runs(
            uid=uid,
            run_ids=[run.id for run in active_runs],
        )
    bindings_by_run = {binding.active_run_id: binding for binding in bindings}

    for run in active_runs:
        binding = bindings_by_run.get(run.id)
        if binding is None:
            continue
        try:
            status = await client.get_session_status(
                uid,
                binding.worker_agent_id,
                binding.worker_session_id,
            )
            if status != "idle":
                continue
            messages = await client.list_messages(
                uid,
                binding.worker_agent_id,
                binding.worker_session_id,
            )
            message = next(
                (
                    item
                    for item in reversed(messages)
                    if isinstance(item, dict)
                    and item.get("role") == "assistant"
                    and item.get("id") == binding.last_reply_id
                    and item.get("finished_reason")
                ),
                None,
            )
            if message is None:
                continue
            recovered = parse_recovered_reply(message, request_id=run.request_id, team_worker=True)
            await _persist_recovered_run(run.id, recovered)
        except Exception:
            logger.exception("恢复 idle Team child Run 失败 run=%s", run.id)

    async with pg_manager.get_async_session_context() as db:
        remaining = await AgentRunRepository(db).list_active_child_runs_for_user(parent_run_id, uid)
        return bool(remaining)


async def reconcile_stale_running_runs(client) -> int:
    """worker 启动时从 idle Session 的最终消息恢复失去所有权的顶层 Run。"""
    async with pg_manager.get_async_session_context() as db:
        result = await db.execute(
            select(AgentRun).where(
                AgentRun.status == "running",
                AgentRun.run_type.in_(("chat", "resume")),
            )
        )
        runs = list(result.scalars().all())

    recovered_count = 0
    for run in runs:
        if await reconcile_active_team_child_runs(client, parent_run_id=run.id, uid=run.uid):
            continue
        async with pg_manager.get_async_session_context() as db:
            mapping = await get_thread_session(db, uid=run.uid, thread_id=run.conversation_thread_id)
        if mapping is None:
            continue
        try:
            status = await client.get_session_status(
                run.uid,
                mapping.agentscope_agent_id,
                mapping.agentscope_session_id,
            )
            if status != "idle":
                continue
            messages = await client.list_messages(
                run.uid,
                mapping.agentscope_agent_id,
                mapping.agentscope_session_id,
            )
        except Exception:
            logger.exception("读取 stale Run 的 AgentScope 状态失败 run=%s", run.id)
            continue

        message = _latest_finished_reply(messages, started_at=run.started_at)
        if message is None:
            continue
        from yuxi.agentscope.thread_guard import clear_pending_confirm, load_pending_confirm

        pending = await load_pending_confirm(run.conversation_thread_id)
        pending_tool_calls = None
        if pending:
            pending_reply_id = str(pending.get("reply_id") or "")
            pending_message = next(
                (
                    item
                    for item in messages
                    if isinstance(item, dict)
                    and item.get("id") == pending_reply_id
                    and item.get("finished_reason")
                    and _reply_finished_after(item, run.started_at)
                ),
                None,
            )
            if pending_message is None:
                continue
            pending_reply = parse_recovered_reply(
                pending_message,
                request_id=run.request_id,
            )
            pending_tool_calls = pending_reply.tool_calls
        recovered = parse_recovered_reply(message, request_id=run.request_id)
        if await _persist_recovered_run(
            run.id,
            recovered,
            existing_tool_calls=pending_tool_calls,
        ):
            if pending:
                await clear_pending_confirm(run.conversation_thread_id)
            from yuxi.services.agent_request_queue_service import dispatch_next_request

            await dispatch_next_request(
                uid=run.uid,
                agent_slug=run.agent_slug,
                thread_id=run.conversation_thread_id,
            )
            recovered_count += 1
    return recovered_count


async def _persist_recovered_run(
    run_id: str,
    recovered: RecoveredReply,
    *,
    existing_tool_calls: list[dict] | None = None,
) -> bool:
    """原子写入恢复输出和终态；并发正常收尾获胜时保持幂等。"""
    async with pg_manager.get_async_session_context() as db:
        runs = AgentRunRepository(db)
        run = await runs.get_run(run_id)
        if run is None or run.status in TERMINAL_RUN_STATUSES:
            return False
        worker_id = getattr(run, "worker_id", None)
        owner_kwargs = {"worker_id": worker_id} if worker_id else {}
        existing_call_ids = set()
        existing_message_ids = set()
        if existing_tool_calls:
            existing_call_ids, existing_message_ids = await update_existing_tool_calls(
                db,
                existing_tool_calls,
                conversation_id=run.conversation_id,
            )
        text, reasoning = await _remove_existing_resume_prefixes(
            db,
            text=recovered.text,
            reasoning=recovered.reasoning,
            message_ids=existing_message_ids,
        )
        tool_calls = [call for call in recovered.tool_calls if str(call.get("id") or "") not in existing_call_ids]
        output_message = None
        if text or reasoning or tool_calls:
            output_message = Message(
                conversation_id=run.conversation_id,
                role="assistant",
                content=text,
                message_type="text",
                run_id=run.id,
                request_id=run.request_id,
                delivery_status="complete",
                extra_metadata={
                    "request_id": run.request_id,
                    "run_id": run.id,
                    "token_usage": recovered.usage,
                    "additional_kwargs": ({"reasoning_content": reasoning} if reasoning else {}),
                },
            )
            db.add(output_message)
            await db.flush()
            for call in tool_calls:
                db.add(
                    ToolCall(
                        message_id=output_message.id,
                        langgraph_tool_call_id=call.get("id"),
                        tool_name=call.get("name") or "unknown",
                        tool_input=call.get("args") or {},
                        tool_output=call.get("output") or "",
                        status=call.get("status") or "pending",
                        error_message=call.get("error_message"),
                    )
                )
            await runs.set_output_message(run.id, output_message.id, **owner_kwargs)
        persisted, changed = await runs.set_terminal_status(
            run.id,
            status=recovered.status,
            error_type=recovered.error_type,
            error_message=recovered.error_message,
            token_usage=recovered.usage,
            **owner_kwargs,
        )
        if persisted is None or not changed:
            return False
        await ConversationRepository(db).set_message_delivery_status(
            run.input_message_id,
            "complete" if recovered.status == "completed" else "failed",
        )
        await db.commit()

    await append_run_stream_event(
        run.id,
        "end",
        {
            "status": recovered.status,
            "chunk": {
                "request_id": run.request_id,
                "response": None,
                "thread_id": None,
                "status": "finished" if recovered.status == "completed" else "error",
                "error_type": recovered.error_type,
                "error_message": recovered.error_message,
            },
        },
        thread_id=run.conversation_thread_id,
    )
    return True


async def update_existing_tool_calls(
    db,
    tool_calls: list[dict],
    *,
    conversation_id: int,
) -> tuple[set[str], set[int]]:
    """在当前恢复事务中更新同一对话的 pending 工具。"""
    updated_ids = set()
    message_ids = set()
    conversations = ConversationRepository(db)
    for call in tool_calls:
        tool_call_id = str(call.get("id") or "")
        if not tool_call_id:
            continue
        existing = await conversations.get_pending_tool_call_for_conversation(
            tool_call_id,
            conversation_id,
        )
        if existing is None:
            continue
        existing.tool_name = call.get("name") or existing.tool_name
        existing.tool_input = call.get("args") or {}
        existing.tool_output = call.get("output") or ""
        existing.status = call.get("status") or "pending"
        existing.error_message = call.get("error_message")
        updated_ids.add(tool_call_id)
        message_ids.add(existing.message_id)
    return updated_ids, message_ids


async def _remove_existing_resume_prefixes(
    db,
    *,
    text: str,
    reasoning: str,
    message_ids: set[int],
) -> tuple[str, str]:
    """移除审批前已保存的正文和推理前缀，避免 resume 恢复重复展示。"""
    if not message_ids:
        return text, reasoning
    result = await db.execute(select(Message).where(Message.id.in_(message_ids)))
    for message in result.scalars().all():
        previous_text = str(message.content or "")
        if previous_text and text.startswith(previous_text):
            text = text[len(previous_text) :]
        metadata = message.extra_metadata if isinstance(message.extra_metadata, dict) else {}
        additional = metadata.get("additional_kwargs") if isinstance(metadata.get("additional_kwargs"), dict) else {}
        previous_reasoning = str(additional.get("reasoning_content") or "")
        if previous_reasoning and reasoning.startswith(previous_reasoning):
            reasoning = reasoning[len(previous_reasoning) :]
    return text, reasoning


def _latest_finished_reply(messages: list, *, started_at: datetime | None) -> dict | None:
    """选择本次 Run 开始后最后完成的 assistant reply。"""
    fallback_completed = None
    for message in reversed(messages):
        if not isinstance(message, dict) or message.get("role") != "assistant" or not message.get("finished_reason"):
            continue
        finished_at = _parse_datetime(message.get("finished_at"))
        if started_at is not None and (finished_at is None or finished_at < started_at):
            continue
        if fallback_completed is None and message.get("finished_reason") == "completed" and _has_reply_output(message):
            fallback_completed = message
        error = message.get("error") if isinstance(message.get("error"), dict) else {}
        if (
            message.get("finished_reason") == "error"
            and error.get("type") == "setup"
            and not _has_reply_output(message)
        ):
            continue
        return message
    return fallback_completed


def _reply_finished_after(message: dict, started_at: datetime | None) -> bool:
    """判断一条 AgentScope reply 是否已在当前 Run 开始后结束。"""
    finished_at = _parse_datetime(message.get("finished_at"))
    return finished_at is not None and (started_at is None or finished_at >= started_at)


def _parse_datetime(value: Any) -> datetime | None:
    """把 AgentScope ISO 时间归一为无时区 UTC 比较值。"""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _content_text(value: Any) -> str:
    """提取 AgentScope ToolResultBlock 的文本输出。"""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(_content_text(item) for item in value)
    if isinstance(value, dict):
        return str(value.get("text") or value.get("hint") or "")
    return ""


def _has_reply_output(message: dict) -> bool:
    """判断 reply 是否包含可恢复的正文或已执行工具。"""
    return any(
        isinstance(block, dict)
        and (
            (block.get("type") == "text" and bool(str(block.get("text") or "").strip()))
            or block.get("type") in {"tool_call", "tool_result"}
        )
        for block in message.get("content") or []
    )
