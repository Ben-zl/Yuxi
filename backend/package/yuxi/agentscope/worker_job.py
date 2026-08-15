"""agentscope 执行体的队列任务（迁移工单 14 · ② 网关翻转）。

替代旧 LangGraph 执行路径：从 AgentRun 列与输入消息恢复参数 → 映射
保障 → 网关协议转换写入 Run 事件流 → yuxi 消息表落库（前端历史视图）
→ 终态回写 → 派发队头。审批挂起经 pending_confirm 存储支撑 resume。
"""

import asyncio
import json
import os

from sqlalchemy import select

from yuxi.agentscope.client import AgentScopeServiceClient
from yuxi.agentscope.execution import execute_run
from yuxi.agentscope.gateway import GatewayRoundResult
from yuxi.agentscope.runner import ensure_thread_session
from yuxi.agentscope.thread_guard import LEGACY_THREAD_MESSAGE
from yuxi.repositories.agent_run_repository import (
    TERMINAL_RUN_STATUSES,
    AgentRunRepository,
)
from yuxi.repositories.conversation_repository import ConversationRepository
from yuxi.services.agent_request_queue_service import dispatch_next_request
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import Message
from yuxi.storage.redis.manager import get_async_redis_client
from yuxi.utils import logger

# 审批挂起事件缓存（thread 维度，resume 时取回 reply_id/tool_calls）
PENDING_CONFIRM_KEY = "agentscope:pending_confirm:{thread_id}"
PENDING_CONFIRM_TTL_SECONDS = 86400

_EMPTY_USAGE = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


async def store_pending_confirm(thread_id: str, confirm_event: dict) -> None:
    """缓存审批挂起事件，供 resume 请求构造 UserConfirmResultEvent。"""
    redis = await get_async_redis_client()
    await redis.set(
        PENDING_CONFIRM_KEY.format(thread_id=thread_id),
        json.dumps(confirm_event, ensure_ascii=False),
        ex=PENDING_CONFIRM_TTL_SECONDS,
    )


async def load_pending_confirm(thread_id: str) -> dict | None:
    """读取并清除线程的审批挂起事件。"""
    redis = await get_async_redis_client()
    key = PENDING_CONFIRM_KEY.format(thread_id=thread_id)
    raw = await redis.get(key)
    if not raw:
        return None
    await redis.delete(key)
    return json.loads(raw)


async def execute_agent_run_job(run_id: str) -> None:
    """队列任务入口：执行一个已派发的 AgentRun（agentscope 执行体）。"""
    async with pg_manager.get_async_session_context() as db:
        run_repo = AgentRunRepository(db)
        run = await run_repo.get_run(run_id)
        if run is None:
            logger.warning(f"Run not found: {run_id}")
            return
        if run.status in TERMINAL_RUN_STATUSES:
            if run.status == "completed":
                await dispatch_next_request(
                    uid=run.uid,
                    agent_slug=run.agent_slug,
                    thread_id=run.conversation_thread_id,
                )
            return

        input_message = (
            await db.execute(select(Message).where(Message.id == run.input_message_id))
        ).scalar_one_or_none() if run.input_message_id else None
        if input_message is None:
            await run_repo.set_terminal_status(
                run_id, status="failed", error_message="运行任务缺少输入消息"
            )
            await db.commit()
            return

        await run_repo.mark_running(run_id)
        await db.commit()

        client = AgentScopeServiceClient(
            os.getenv("AGENTSCOPE_BASE_URL", "http://agentscope:8100")
        )
        conv_repo = ConversationRepository(db)
        try:
            if run.run_type == "resume":
                result = await _execute_resume(db, client, run, input_message)
            else:
                await ensure_thread_session(
                    db,
                    client,
                    uid=run.uid,
                    thread_id=run.conversation_thread_id,
                    agent_slug=run.agent_slug,
                )
                result = await execute_run(
                    db, client, run=run, text=input_message.content
                )
                if result.parked == "permission" and result.pending_confirm:
                    await store_pending_confirm(
                        run.conversation_thread_id, result.pending_confirm
                    )

            # yuxi 消息表落库：前端线程历史视图的数据源
            if result.text:
                await conv_repo.add_message_by_thread_id(
                    thread_id=run.conversation_thread_id,
                    role="assistant",
                    content=result.text,
                    message_type="text",
                    extra_metadata={
                        "request_id": run.request_id,
                        "run_id": run_id,
                        "token_usage": result.usage or {},
                    },
                    run_id=run_id,
                    request_id=run.request_id,
                )
            await db.commit()
        except ValueError as exc:
            if LEGACY_THREAD_MESSAGE in str(exc):
                await _fail_run(db, run_repo, run, str(exc))
                return
            raise
        except Exception as exc:  # noqa: BLE001 - 任务级失败统一终态
            logger.exception(f"agentscope 执行失败 run={run_id}: {exc}")
            await _fail_run(db, run_repo, run, f"执行失败: {exc}")
            return

        # 挂起/中断终态补发 end 帧（前端收尾依赖）；完成后派发队头
        if result.run_status == "completed":
            await dispatch_next_request(
                uid=run.uid,
                agent_slug=run.agent_slug,
                thread_id=run.conversation_thread_id,
            )
        else:
            await _emit_end_event(
                run.id,
                run.conversation_thread_id,
                {"status": result.run_status},
            )


async def _fail_run(db, run_repo, run, message: str) -> None:
    """失败终态 + end 帧 + 提交。"""
    await run_repo.set_terminal_status(
        run.id, status="failed", error_message=message
    )
    await _emit_end_event(
        run.id,
        run.conversation_thread_id,
        {"status": "error", "error_message": message},
    )
    await db.commit()


async def _execute_resume(db, client, run, input_message) -> GatewayRoundResult:
    """resume 执行：审批决定 → resume_confirm；文本回答 → 新一轮输入。

    兼容旧栈 resume 载荷 {"decisions":[{"type":"approve"}]} 与
    ask_user 文本回答（answer 字段或回退到消息正文）。
    """
    mapping = await ensure_thread_session(
        db,
        client,
        uid=run.uid,
        thread_id=run.conversation_thread_id,
        agent_slug=run.agent_slug,
    )
    resume_input = (input_message.extra_metadata or {}).get("resume") or {}

    confirm_event = await load_pending_confirm(run.conversation_thread_id)
    decisions = resume_input.get("decisions") if isinstance(resume_input, dict) else None
    if confirm_event and isinstance(decisions, list) and decisions:
        approved = any(
            str(d.get("type", "")).lower() in {"approve", "approved", "accept"}
            for d in decisions
            if isinstance(d, dict)
        )
        return await _resume_and_collect(
            client, run, mapping, confirm_event, approved
        )

    # 文本回答（ask_user 语义）：作为新一轮用户输入执行
    answer = resume_input.get("answer") if isinstance(resume_input, dict) else None
    text = answer if isinstance(answer, str) and answer.strip() else input_message.content
    return await execute_run(db, client, run=run, text=text)


async def _resume_and_collect(
    client, run, mapping, confirm_event, approved: bool
) -> GatewayRoundResult:
    """订阅在先、恢复在后，收集到 REPLY_END。"""
    queue: asyncio.Queue = asyncio.Queue()

    async def _pump():
        async for event in client.stream_events(
            run.uid,
            mapping.agentscope_agent_id,
            mapping.agentscope_session_id,
            read_timeout=180.0,
        ):
            await queue.put(event)

    pump = asyncio.create_task(_pump())
    try:
        await asyncio.sleep(0.5)
        await client.resume_confirm(
            run.uid,
            mapping.agentscope_agent_id,
            mapping.agentscope_session_id,
            reply_id=confirm_event.get("reply_id", ""),
            tool_calls=confirm_event.get("tool_calls") or [],
            confirmed=approved,
        )
        text_parts: list[str] = []
        event_count = 0
        while True:
            event = await asyncio.wait_for(queue.get(), timeout=180.0)
            if isinstance(event, Exception):
                raise event
            event_count += 1
            if str(event.get("type", "")).upper() == "TEXT_BLOCK_DELTA":
                text_parts.append(event.get("delta", ""))
            if str(event.get("type", "")).upper() == "REPLY_END":
                finished = str(event.get("finished_reason", "")).lower()
                return GatewayRoundResult(
                    run_status=(
                        "completed" if finished == "completed" else "interrupted"
                    ),
                    text="".join(text_parts),
                    reasoning="",
                    event_count=event_count,
                    usage=_EMPTY_USAGE,
                )
    finally:
        pump.cancel()


async def _emit_end_event(run_id: str, thread_id: str, payload: dict) -> None:
    """补发 end 事件帧（前端终态收尾依赖）。"""
    from yuxi.services.run_queue_service import append_run_stream_event

    try:
        await append_run_stream_event(run_id, "end", payload, thread_id=thread_id)
    except Exception as exc:  # noqa: BLE001 - 事件补发失败不改变终态
        logger.warning(f"补发 end 事件失败 run={run_id}: {exc}")
