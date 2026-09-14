"""网关侧一轮对话的协议转换与事件写入（迁移工单 04）。

把 agentscope 会话事件流实时转换为 yuxi Run 事件信封并写入 Redis
Stream（run:events:{run_id}），复用既有 XRANGE(after_seq) 断线重连续传
机制与 SSE 投递端点。订阅在先、触发在后（回放日志覆盖订阅空档）。
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
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
from yuxi.agentscope.usage import UsageAccumulator


# team 编排轮次的静默收束参数：REPLY_END 后成员回报（wakeup）可能驱动
# 会话续写，无新事件持续 QUIESCE 秒才真正结束。child Run 的生命周期
# 独立于父 Run，单段等待到期但 child 仍活跃时继续下一段。
TEAM_TOOL_NAMES = {"TeamCreate", "AgentCreate", "TeamSay", "TeamInvite"}
TEAM_QUIESCE_SECONDS = 45.0
TEAM_CHILD_WAIT_SEGMENT_SECONDS = 600.0


@dataclass
class GatewayRoundResult:
    """网关一轮对话的运行结果摘要。"""

    run_status: str
    text: str
    reasoning: str
    event_count: int
    error_type: str | None = None
    error_message: str | None = None  # AgentScope 终态中的可展示错误
    parked: str | None = None  # 挂起原因（permission=等待工具审批）
    usage: dict | None = None  # 聚合的 token 用量（input/output/total）
    pending_confirm: dict | None = None  # 审批挂起原始事件（resume 用）
    tool_calls: list[dict] | None = None  # 历史消息需恢复的工具调用与结果


def tasks_to_todos(tasks_context: dict) -> list[dict]:
    """把 AgentScope TaskContext 转为现有前端 Todo 数据结构。"""
    return [
        {
            "id": str(task.get("id") or ""),
            "content": str(task.get("subject") or task.get("description") or ""),
            "status": str(task.get("state") or "pending"),
        }
        for task in tasks_context.get("tasks", [])
        if isinstance(task, dict)
    ]


def start_cancel_watcher(
    client: AgentScopeServiceClient,
    *,
    uid: str,
    agent_id: str,
    session_id: str,
    run_id: str,
    poll_seconds: float = 1.0,
    event_queue: asyncio.Queue | None = None,
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
                    try:
                        await client.interrupt_session(uid, agent_id, session_id)
                    except Exception as exc:  # noqa: BLE001 - 由事件消费者统一写入终态
                        if event_queue is None:
                            raise RuntimeError("取消信号监听失败") from exc
                        await event_queue.put(RuntimeError("取消信号监听失败"))
                        return
                    if event_queue is not None:
                        await event_queue.put(RuntimeError("运行任务已取消"))
                    return
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 监听失效必须终止当前执行
                raise RuntimeError("取消信号监听失败") from exc

    return asyncio.create_task(_watch())


async def collect_run_events(
    queue: asyncio.Queue,
    client: AgentScopeServiceClient,
    *,
    uid: str,
    agent_id: str,
    session_id: str,
    run_id: str,
    request_id: str,
    thread_id: str,
    read_timeout: float,
    configured_model_spec: str | None = None,
    historical_reply_ids: set[str] | None = None,
    tool_converter: ToolEventConverter | None = None,
    team_tool_seen: bool = False,
    has_active_child_runs: Callable[[], Awaitable[bool]] | None = None,
    idle_callback: Callable[[], Awaitable[None]] | None = None,
    idle_poll_seconds: float | None = None,
    total_timeout: float | None = None,
    emit_parked_end: bool = True,
) -> GatewayRoundResult:
    """收集一次触发或恢复后的事件，统一处理挂起与 Team 静默收束。"""
    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    event_count = 0
    usage = UsageAccumulator(configured_model_spec=configured_model_spec)
    converter = tool_converter or ToolEventConverter(request_id)
    pending_terminal = None
    team_deadline = 0.0
    active_reply_id: str | None = None
    current_reply_id: str | None = None
    loop = asyncio.get_running_loop()
    total_deadline = loop.time() + total_timeout if total_timeout is not None else None

    while True:
        if pending_terminal is not None:
            remaining = team_deadline - time.monotonic()
            if remaining <= 0:
                if has_active_child_runs is not None and await has_active_child_runs():
                    team_deadline = time.monotonic() + TEAM_CHILD_WAIT_SEGMENT_SECONDS
                    continue
                break
            wait_seconds = min(TEAM_QUIESCE_SECONDS, remaining)
        elif idle_callback is not None and idle_poll_seconds is not None:
            if total_deadline is None:
                raise ValueError("空闲回调必须配置总超时时间")
            remaining = total_deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError("resume 收集超时：会话长时间无事件")
            wait_seconds = min(idle_poll_seconds, remaining)
        else:
            wait_seconds = read_timeout

        try:
            event = await asyncio.wait_for(queue.get(), timeout=wait_seconds)
        except TimeoutError:
            if pending_terminal is not None:
                if has_active_child_runs is not None and await has_active_child_runs():
                    continue
                break
            if idle_callback is None:
                from yuxi.services.run_queue_service import has_cancel_signal

                if await has_cancel_signal(run_id):
                    raise RuntimeError("运行任务已取消")
                raise
            await idle_callback()
            continue
        if isinstance(event, Exception):
            raise event

        event_type = str(event.get("type", "")).upper()
        event_reply_id = str(event.get("reply_id") or "")
        usage.observe(event)
        if event_type == "CUSTOM" and event.get("name") == "state_updated":
            state_chunk = make_chunk(
                request_id,
                status="agent_state",
                agent_state={
                    "todos": tasks_to_todos((event.get("value") or {}).get("tasks_context") or {}),
                    "files": {},
                    "files_truncated": False,
                },
            )
            await append_run_stream_event(
                run_id,
                "messages",
                {"items": [state_chunk]},
                thread_id=thread_id,
            )
            event_count += 1
            continue
        if event_type == "CUSTOM" and event.get("name") == "context_compression":
            await append_run_stream_event(
                run_id,
                "messages",
                {"items": event_to_chunks(event, request_id=request_id)},
                thread_id=thread_id,
            )
            event_count += 1
            continue
        if event_type == "CUSTOM" and event.get("name") == "token_context":
            continue

        if historical_reply_ids is not None:
            # AgentScope 的工具事件在部分版本没有 reply_id，但它们仍属于
            # 当前已确认的新回复；只过滤无归属的普通事件，不能丢失工具调用。
            is_tool_event = event_type.startswith("TOOL_")
            if not event_reply_id and not is_tool_event:
                continue
            if event_type == "REPLY_START":
                if event_reply_id in historical_reply_ids:
                    continue
                if active_reply_id is None or pending_terminal is not None:
                    active_reply_id = event_reply_id
            if active_reply_id is None or (event_reply_id and event_reply_id != active_reply_id):
                continue
        elif pending_terminal is not None and not event_reply_id:
            continue

        if event_type == "REPLY_START":
            if current_reply_id is not None and event_reply_id != current_reply_id:
                team_tool_seen = False
            current_reply_id = event_reply_id

        if event_type in {"REQUIRE_USER_CONFIRM", "REQUIRE_EXTERNAL_EXECUTION"}:
            converter.seed_tool_calls(event.get("tool_calls") or [], reply_id=event_reply_id)
            chunks = event_to_chunks(event, request_id=request_id)
            if chunks:
                await append_run_stream_event(
                    run_id,
                    "messages",
                    {"items": chunks},
                    thread_id=thread_id,
                )
                event_count += 1
            parked = "permission" if event_type == "REQUIRE_USER_CONFIRM" else "external"
            if emit_parked_end:
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
                event_count=event_count,
                parked=parked,
                usage=usage.snapshot(complete=False),
                pending_confirm=event,
                tool_calls=converter.history_tool_calls(),
            )

        if event_type == "TOOL_CALL_START" and str(event.get("tool_call_name", "")) in TEAM_TOOL_NAMES:
            team_tool_seen = True
        if event_type == "REPLY_END":
            terminal = reply_end_to_terminal(event, request_id=request_id)
            child_active = has_active_child_runs is not None and await has_active_child_runs()
            if not team_tool_seen and not child_active:
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
                    error_type=terminal.chunk.get("error_type"),
                    error_message=terminal.chunk.get("error_message"),
                    usage=usage.snapshot(complete=True),
                    tool_calls=converter.history_tool_calls(),
                )
            if pending_terminal is None:
                team_deadline = time.monotonic() + TEAM_CHILD_WAIT_SEGMENT_SECONDS
            pending_terminal = terminal
            continue

        if pending_terminal is not None:
            pending_terminal = None
        if event_type == "TEXT_BLOCK_DELTA":
            text_parts.append(event.get("delta", ""))
        elif event_type == "THINKING_BLOCK_DELTA":
            reasoning_parts.append(event.get("delta", ""))
        chunks = event_to_chunks(event, request_id=request_id)
        chunks.extend(converter.feed(event))
        if chunks:
            await append_run_stream_event(
                run_id,
                "messages",
                {"items": chunks},
                thread_id=thread_id,
            )
            event_count += 1

    if pending_terminal is None:
        raise RuntimeError("Team 静默收束缺少终态")
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
        error_type=pending_terminal.chunk.get("error_type"),
        error_message=pending_terminal.chunk.get("error_message"),
        usage=usage.snapshot(complete=True),
        tool_calls=converter.history_tool_calls(),
    )


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
    configured_model_spec: str | None = None,
    has_active_child_runs: Callable[[], Awaitable[bool]] | None = None,
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
    cancel_task = start_cancel_watcher(
        client,
        uid=uid,
        agent_id=agent_id,
        session_id=session_id,
        run_id=run_id,
        event_queue=queue,
    )
    try:
        await asyncio.sleep(SUBSCRIBE_SETTLE_SECONDS)
        await client.trigger_chat(uid, agent_id, session_id, text, image_content=image_content)
        return await collect_run_events(
            queue,
            client,
            uid=uid,
            agent_id=agent_id,
            session_id=session_id,
            run_id=run_id,
            request_id=request_id,
            thread_id=thread_id,
            read_timeout=read_timeout,
            configured_model_spec=configured_model_spec,
            historical_reply_ids=historical_reply_ids,
            has_active_child_runs=has_active_child_runs,
        )
    finally:
        await cancel_tasks(pump_task, cancel_task)
