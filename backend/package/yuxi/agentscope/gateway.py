"""网关侧一轮对话的协议转换与事件写入（迁移工单 04）。

把 agentscope 会话事件流实时转换为 yuxi Run 事件信封并写入 Redis
Stream（run:events:{run_id}），复用既有 XRANGE(after_seq) 断线重连续传
机制与 SSE 投递端点。订阅在先、触发在后（回放日志覆盖订阅空档）。
"""

import asyncio
import contextlib
from dataclasses import dataclass

from yuxi.agentscope.client import AgentScopeServiceClient
from yuxi.agentscope.protocol import (
    event_to_chunks,
    init_chunk,
    reply_end_to_terminal,
)
from yuxi.agentscope.runner import SUBSCRIBE_SETTLE_SECONDS
from yuxi.services.run_queue_service import append_run_stream_event


@dataclass
class GatewayRoundResult:
    """网关一轮对话的运行结果摘要。"""

    run_status: str
    text: str
    reasoning: str
    event_count: int


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
    read_timeout: float = 180.0,
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

    queue: asyncio.Queue = asyncio.Queue()

    async def _pump() -> None:
        try:
            async for event in client.stream_events(
                uid, agent_id, session_id, read_timeout=read_timeout
            ):
                await queue.put(event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 泵任务异常需带回消费方
            await queue.put(exc)

    pump_task = asyncio.create_task(_pump())
    try:
        await asyncio.sleep(SUBSCRIBE_SETTLE_SECONDS)
        await client.trigger_chat(uid, agent_id, session_id, text)

        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        event_count = 0
        while True:
            event = await asyncio.wait_for(queue.get(), timeout=read_timeout)
            if isinstance(event, Exception):
                raise event
            event_type = str(event.get("type", "")).upper()
            if event_type == "REPLY_END":
                terminal = reply_end_to_terminal(event, request_id=request_id)
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
                )
            if event_type == "TEXT_BLOCK_DELTA":
                text_parts.append(event.get("delta", ""))
            elif event_type == "THINKING_BLOCK_DELTA":
                reasoning_parts.append(event.get("delta", ""))
            chunks = event_to_chunks(event, request_id=request_id)
            if chunks:
                event_count += 1
                await append_run_stream_event(
                    run_id, "messages", {"items": chunks}, thread_id=thread_id
                )
    finally:
        pump_task.cancel()
        # 等待取消传播完成，让 httpx 流上下文在协程栈展开中正常关闭
        with contextlib.suppress(asyncio.CancelledError):
            await pump_task
