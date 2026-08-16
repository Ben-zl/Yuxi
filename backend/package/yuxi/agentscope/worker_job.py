"""agentscope 执行体的队列任务（迁移工单 14 · ② 网关翻转）。

替代旧 LangGraph 执行路径：从 AgentRun 列与输入消息恢复参数 → 映射
保障 → 网关协议转换写入 Run 事件流 → yuxi 消息表落库（前端历史视图）
→ 终态回写 → 派发队头。审批挂起经 pending_confirm 存储支撑 resume。
"""

import asyncio
import contextlib
import json
import os

from sqlalchemy import select

from yuxi.agentscope.client import AgentScopeServiceClient
from yuxi.agentscope.execution import execute_run, finalize_run
from yuxi.agentscope.gateway import GatewayRoundResult, start_cancel_watcher
from yuxi.agentscope.runner import ensure_thread_session
from yuxi.agentscope.thread_guard import (
    LEGACY_THREAD_MESSAGE,
    has_pending_confirm,
    load_pending_confirm,
    store_pending_confirm,
)
from yuxi.repositories.agent_run_repository import (
    TERMINAL_RUN_STATUSES,
    AgentRunRepository,
)
from yuxi.repositories.conversation_repository import ConversationRepository
from yuxi.services.agent_request_queue_service import dispatch_next_request
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import Message
from yuxi.utils import logger

_EMPTY_USAGE = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


async def execute_agent_run_job(run_id: str) -> None:
    """队列任务入口：执行一个已派发的 AgentRun（agentscope 执行体）。"""
    async with pg_manager.get_async_session_context() as db:
        run_repo = AgentRunRepository(db)
        run = await run_repo.get_run(run_id)
        if run is None:
            logger.warning(f"Run not found: {run_id}")
            return
        if run.status in TERMINAL_RUN_STATUSES:
            if run.status == "completed" or (
                run.status == "interrupted"
                and not await has_pending_confirm(run.conversation_thread_id)
            ):
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
                model_spec = (run.input_payload or {}).get("model_spec")
                await ensure_thread_session(
                    db,
                    client,
                    uid=run.uid,
                    thread_id=run.conversation_thread_id,
                    agent_slug=run.agent_slug,
                    model_spec=model_spec,
                )
                result = await execute_run(
                    db, client, run=run, text=input_message.content, model_spec=model_spec
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

        # 挂起/中断终态补发 end 帧（前端收尾依赖）；completed 与 steer 中断
        #（非审批挂起）派发队头：引导消息需在被中断的 Run 结束后立即执行。
        if result.run_status == "completed" or (
            result.run_status == "interrupted"
            and not await has_pending_confirm(run.conversation_thread_id)
        ):
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
        model_spec=(run.input_payload or {}).get("model_spec"),
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
        result = await _resume_and_collect(
            client, run, mapping, confirm_event, approved
        )
        # resume 路径同样必须回写终态，否则 Run 永远停在 running
        await finalize_run(db, run, result)
        return result

    # 文本回答（ask_user 语义）：作为新一轮用户输入执行
    answer = resume_input.get("answer") if isinstance(resume_input, dict) else None
    text = answer if isinstance(answer, str) and answer.strip() else input_message.content
    return await execute_run(
        db, client, run=run, text=text, model_spec=(run.input_payload or {}).get("model_spec")
    )


async def _collect_asking_tool_calls(
    client: AgentScopeServiceClient,
    *,
    uid: str,
    agent_id: str,
    session_id: str,
    reply_id: str,
) -> list[dict]:
    """从会话 reply 消息收集全部 asking 状态的工具调用。

    并行多工具审批时，REQUIRE_USER_CONFIRM 事件可能只含先完成解析的
    调用（fork 流式时序）；以会话消息事实为准补全确认集合，避免其余
    调用永久滞留 asking。读取失败时返回空列表，调用方回退事件载荷。
    """
    try:
        messages = await client.list_messages(uid, agent_id, session_id)
    except Exception as exc:  # noqa: BLE001 - 补全失败回退事件载荷
        logger.warning(f"读取会话消息补全审批集合失败: {exc}")
        return []
    for msg in reversed(messages or []):
        if not isinstance(msg, dict) or msg.get("id") != reply_id:
            continue
        return [
            {
                "id": b.get("id"),
                "name": b.get("name"),
                "input": b.get("input"),
            }
            for b in msg.get("content") or []
            if isinstance(b, dict)
            and b.get("type") == "tool_call"
            and str(b.get("state", "")).lower() == "asking"
        ]
    return []


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
    cancel_task = start_cancel_watcher(
        client,
        uid=run.uid,
        agent_id=mapping.agentscope_agent_id,
        session_id=mapping.agentscope_session_id,
        run_id=run.id,
    )
    # 停滞自愈：并行多工具审批时，REQUIRE 事件与会话落库存在时序差，
    # 个别 tool_call 可能在确认后才进入 asking。事件流停滞期间周期性
    # 检查会话，发现滞留 asking 即按本次决定补发确认。
    stall_poll_seconds = 15.0
    total_deadline = asyncio.get_running_loop().time() + 180.0
    try:
        await asyncio.sleep(0.5)
        # 发出确认前先以会话中仍处 asking 的完整集合为准
        tool_calls = confirm_event.get("tool_calls") or []
        asking = await _collect_asking_tool_calls(
            client,
            uid=run.uid,
            agent_id=mapping.agentscope_agent_id,
            session_id=mapping.agentscope_session_id,
            reply_id=confirm_event.get("reply_id", ""),
        )
        if asking:
            tool_calls = asking
        await client.resume_confirm(
            run.uid,
            mapping.agentscope_agent_id,
            mapping.agentscope_session_id,
            reply_id=confirm_event.get("reply_id", ""),
            tool_calls=tool_calls,
            confirmed=approved,
        )
        text_parts: list[str] = []
        event_count = 0
        loop = asyncio.get_running_loop()
        while True:
            remaining = total_deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError("resume 收集超时：会话长时间无事件")
            try:
                event = await asyncio.wait_for(
                    queue.get(), timeout=min(stall_poll_seconds, remaining)
                )
            except asyncio.TimeoutError:
                asking = await _collect_asking_tool_calls(
                    client,
                    uid=run.uid,
                    agent_id=mapping.agentscope_agent_id,
                    session_id=mapping.agentscope_session_id,
                    reply_id=confirm_event.get("reply_id", ""),
                )
                if asking:
                    await client.resume_confirm(
                        run.uid,
                        mapping.agentscope_agent_id,
                        mapping.agentscope_session_id,
                        reply_id=confirm_event.get("reply_id", ""),
                        tool_calls=asking,
                        confirmed=approved,
                    )
                continue
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
        cancel_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await pump
        with contextlib.suppress(asyncio.CancelledError):
            await cancel_task


async def _emit_end_event(run_id: str, thread_id: str, payload: dict) -> None:
    """补发 end 事件帧（前端终态收尾依赖）。"""
    from yuxi.services.run_queue_service import append_run_stream_event

    try:
        await append_run_stream_event(run_id, "end", payload, thread_id=thread_id)
    except Exception as exc:  # noqa: BLE001 - 事件补发失败不改变终态
        logger.warning(f"补发 end 事件失败 run={run_id}: {exc}")
