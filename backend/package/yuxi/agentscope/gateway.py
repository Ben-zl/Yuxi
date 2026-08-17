"""网关侧一轮对话的协议转换与事件写入（迁移工单 04）。

把 agentscope 会话事件流实时转换为 yuxi Run 事件信封并写入 Redis
Stream（run:events:{run_id}），复用既有 XRANGE(after_seq) 断线重连续传
机制与 SSE 投递端点。订阅在先、触发在后（回放日志覆盖订阅空档）。
"""

import asyncio
import time
from dataclasses import dataclass

from yuxi.agentscope.client import AgentScopeServiceClient
from yuxi.agentscope.event_stream import cancel_tasks, start_event_pump
from yuxi.agentscope.protocol import (
    ToolEventConverter,
    make_chunk,
    event_to_chunks,
    init_chunk,
    reply_end_to_terminal,
)
from yuxi.agentscope.event_stream import READ_TIMEOUT_SECONDS
from yuxi.agentscope.runner import SUBSCRIBE_SETTLE_SECONDS
from yuxi.services.run_queue_service import append_run_stream_event


# team 编排轮次的静默收束参数：REPLY_END 后成员回报（wakeup）可能驱动
# 会话续写，无新事件持续 QUIESCE 秒才真正结束；续写可多次触发，总预算
# 不超过 EXTENSION_BUDGET。
TEAM_TOOL_NAMES = {"TeamCreate", "AgentCreate", "TeamSay", "TeamInvite"}
TEAM_QUIESCE_SECONDS = 45.0
TEAM_EXTENSION_BUDGET_SECONDS = 600.0


def _usage(input_tokens: int, output_tokens: int) -> dict:
    """按 AgentRun.token_usage 口径聚合一轮对话的模型用量。"""
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
    }


@dataclass
class GatewayRoundResult:
    """网关一轮对话的运行结果摘要。"""

    run_status: str
    text: str
    reasoning: str
    event_count: int
    error_message: str | None = None  # AgentScope 终态中的可展示错误
    parked: str | None = None  # 挂起原因（permission=等待工具审批）
    usage: dict | None = None  # 聚合的 token 用量（input/output/total）
    pending_confirm: dict | None = None  # 审批挂起原始事件（resume 用）


def start_cancel_watcher(
    client: AgentScopeServiceClient,
    *,
    uid: str,
    agent_id: str,
    session_id: str,
    run_id: str,
    poll_seconds: float = 1.0,
) -> asyncio.Task:
    """监听 run 取消信号，收到即中断会话使事件流尽快终止。

    与事件流消费并行运行；中断后 REPLY_END(interrupted) 自然结束本轮，
    终态由 finalize_run 依据取消信号归一为 cancelled。
    """
    from yuxi.services.run_queue_service import has_cancel_signal

    async def _watch() -> None:
        while True:
            await asyncio.sleep(poll_seconds)
            try:
                if await has_cancel_signal(run_id):
                    await client.interrupt_session(uid, agent_id, session_id)
                    return
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 监听失效必须终止当前执行
                raise RuntimeError("取消信号监听失败") from exc

    return asyncio.create_task(_watch())


async def stream_round_to_run_events(
    client: AgentScopeServiceClient,
    *,
    uid: str,
    agent_id: str,
    session_id: str,
    text: str,
    run_id: str,
    request_id: str,
    thread_id: str,
    read_timeout: float = READ_TIMEOUT_SECONDS,
    image_content: str | None = None,
) -> GatewayRoundResult:
    """触发一轮对话，事件实时转换为 chunk 并写入 run 事件流，直至终态。

    写入顺序：metadata → messages(init) → messages(loading…) → end(终态)。
    """
    await append_run_stream_event(
        run_id,
        "metadata",
        {"run_type": "chat", "source": "chat", "request_id": request_id},
        thread_id=thread_id,
    )
    await append_run_stream_event(
        run_id,
        "messages",
        {"items": [init_chunk(request_id, text=text)]},
        thread_id=thread_id,
    )

    existing_messages = await client.list_messages(uid, agent_id, session_id)
    historical_reply_ids = {
        str(message["id"]) for message in existing_messages or [] if isinstance(message, dict) and message.get("id")
    }
    queue, pump_task = start_event_pump(
        client,
        uid=uid,
        agent_id=agent_id,
        session_id=session_id,
        read_timeout=read_timeout,
    )
    cancel_task = start_cancel_watcher(client, uid=uid, agent_id=agent_id, session_id=session_id, run_id=run_id)
    try:
        await asyncio.sleep(SUBSCRIBE_SETTLE_SECONDS)
        await client.trigger_chat(uid, agent_id, session_id, text, image_content=image_content)

        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        event_count = 0
        input_tokens = 0
        output_tokens = 0
        tool_converter = ToolEventConverter(request_id)
        team_tool_seen = False
        pending_terminal = None  # team 静默期内暂存的终态，收束时落 end 帧
        team_deadline = 0.0
        active_reply_id: str | None = None
        while True:
            if pending_terminal is not None:
                remaining = team_deadline - time.monotonic()
                if remaining <= 0:
                    break  # 续写总预算耗尽：以暂存终态收束
                wait_seconds = min(TEAM_QUIESCE_SECONDS, remaining)
            else:
                wait_seconds = read_timeout
            try:
                event = await asyncio.wait_for(queue.get(), timeout=wait_seconds)
            except TimeoutError:
                if pending_terminal is None:
                    raise
                break  # 静默窗口到期：以暂存终态收束
            if isinstance(event, Exception):
                raise event
            event_type = str(event.get("type", "")).upper()
            event_reply_id = str(event.get("reply_id") or "")
            if not event_reply_id:
                continue
            if event_type == "REPLY_START":
                if event_reply_id in historical_reply_ids:
                    continue
                if active_reply_id is None or pending_terminal is not None:
                    active_reply_id = event_reply_id
            if active_reply_id is None:
                continue
            if event_reply_id != active_reply_id:
                continue
            if event_type in {"REQUIRE_USER_CONFIRM", "REQUIRE_EXTERNAL_EXECUTION"}:
                # 工具审批挂起：run 进入 interrupted 终态（挂起互斥依据），
                # 审批结果经新一轮 resume 请求续跑（与旧栈 resume 语义一致）
                chunks = event_to_chunks(event, request_id=request_id)
                await append_run_stream_event(run_id, "messages", {"items": chunks}, thread_id=thread_id)
                parked = "permission" if event_type == "REQUIRE_USER_CONFIRM" else "external"
                message = "等待工具审批" if parked == "permission" else "等待用户回答"
                terminal_chunk = make_chunk(request_id, status="interrupted", message=message)
                await append_run_stream_event(
                    run_id,
                    "end",
                    {"status": "interrupted", "chunk": terminal_chunk},
                    thread_id=thread_id,
                )
                return GatewayRoundResult(
                    run_status="interrupted",
                    text="".join(text_parts),
                    reasoning="".join(reasoning_parts),
                    event_count=event_count + 1,
                    parked=parked,
                    usage=_usage(input_tokens, output_tokens),
                    pending_confirm=event,
                )
            if event_type == "TOOL_CALL_START" and str(event.get("tool_call_name", "")) in TEAM_TOOL_NAMES:
                team_tool_seen = True
            if event_type == "REPLY_END":
                terminal = reply_end_to_terminal(event, request_id=request_id)
                if not team_tool_seen:
                    await append_run_stream_event(
                        run_id,
                        "end",
                        {"status": terminal.run_status, "chunk": terminal.chunk},
                        thread_id=thread_id,
                    )
                    return GatewayRoundResult(
                        run_status=terminal.run_status,
                        text="".join(text_parts),
                        reasoning="".join(reasoning_parts),
                        event_count=event_count,
                        error_message=terminal.chunk.get("error_message"),
                        usage=_usage(input_tokens, output_tokens),
                    )
                # team 轮次：暂存终态，等待成员回报驱动的续写（静默窗口）
                if pending_terminal is None:
                    team_deadline = time.monotonic() + TEAM_EXTENSION_BUDGET_SECONDS
                pending_terminal = terminal
                continue
            if pending_terminal is not None:
                # 续写活动到达：取消静默计时，回到正常收集
                pending_terminal = None
            if event_type == "TEXT_BLOCK_DELTA":
                text_parts.append(event.get("delta", ""))
            elif event_type == "THINKING_BLOCK_DELTA":
                reasoning_parts.append(event.get("delta", ""))
            elif event_type == "MODEL_CALL_END":
                input_tokens += int(event.get("input_tokens") or 0)
                output_tokens += int(event.get("output_tokens") or 0)
            chunks = event_to_chunks(event, request_id=request_id)
            chunks.extend(tool_converter.feed(event))
            if chunks:
                event_count += 1
                await append_run_stream_event(run_id, "messages", {"items": chunks}, thread_id=thread_id)
        # team 静默收束：end 帧 + 聚合结果（含续写文本与用量）
        await append_run_stream_event(
            run_id,
            "end",
            {"status": pending_terminal.run_status, "chunk": pending_terminal.chunk},
            thread_id=thread_id,
        )
        return GatewayRoundResult(
            run_status=pending_terminal.run_status,
            text="".join(text_parts),
            reasoning="".join(reasoning_parts),
            event_count=event_count,
            error_message=pending_terminal.chunk.get("error_message"),
            usage=_usage(input_tokens, output_tokens),
        )
    finally:
        await cancel_tasks(pump_task, cancel_task)
