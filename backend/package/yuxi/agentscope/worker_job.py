"""agentscope 执行体的队列任务（迁移工单 14 · ② 网关翻转）。

替代旧 LangGraph 执行路径：从 AgentRun 列与输入消息恢复参数 → 映射
保障 → 网关协议转换写入 Run 事件流 → yuxi 消息表落库（前端历史视图）
→ 终态回写 → 派发队头。审批挂起经 pending_confirm 存储支撑 resume。
"""

import asyncio
import os
from pathlib import Path

from yuxi.agentscope.client import AgentScopeServiceClient
from yuxi.agentscope.event_stream import READ_TIMEOUT_SECONDS
from yuxi.agentscope.execution import execute_run, finalize_run
from yuxi.agentscope.event_stream import cancel_tasks, start_event_pump
from yuxi.agentscope.gateway import GatewayRoundResult, start_cancel_watcher
from yuxi.agentscope.protocol import ToolEventConverter, event_to_chunks, reply_end_to_terminal
from yuxi.agentscope.runner import SUBSCRIBE_SETTLE_SECONDS, ensure_thread_session
from yuxi.agentscope.thread_guard import (
    LEGACY_THREAD_MESSAGE,
    clear_pending_confirm,
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
from yuxi.services.run_queue_service import append_run_stream_event
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import Message
from yuxi.utils import logger
from yuxi.agentscope.usage import UsageAccumulator


async def execute_agent_run_job(run_id: str) -> None:
    """队列任务入口：执行一个已派发的 AgentRun（agentscope 执行体）。"""
    async with pg_manager.get_async_session_context() as db:
        run_repo = AgentRunRepository(db)
        run = await run_repo.get_run(run_id)
        if run is None:
            logger.warning(f"Run not found: {run_id}")
            return
        if run.status in TERMINAL_RUN_STATUSES:
            if run.status != "interrupted" or not await has_pending_confirm(run.conversation_thread_id):
                await dispatch_next_request(
                    uid=run.uid,
                    agent_slug=run.agent_slug,
                    thread_id=run.conversation_thread_id,
                )
            return

        conv_repo = ConversationRepository(db)
        input_message = await conv_repo.get_message_by_id(run.input_message_id) if run.input_message_id else None
        if input_message is None:
            await _fail_run(db, run_repo, run, "运行任务缺少输入消息")
            return

        await run_repo.mark_running(run_id)
        await db.commit()

        client = AgentScopeServiceClient(os.getenv("AGENTSCOPE_BASE_URL", "http://agentscope:8100"))
        try:
            if run.run_type == "resume":
                result = await _execute_resume(db, client, run, input_message)
            else:
                model_spec = (run.input_payload or {}).get("model_spec")
                mapping = await ensure_thread_session(
                    db,
                    client,
                    uid=run.uid,
                    thread_id=run.conversation_thread_id,
                    agent_slug=run.agent_slug,
                    model_spec=model_spec,
                )
                text = await _materialize_run_attachments(
                    conv_repo,
                    client,
                    run=run,
                    input_message=input_message,
                    mapping=mapping,
                )
                await _apply_permission_mode(client, run, mapping)
                result = await execute_run(
                    db,
                    client,
                    run=run,
                    text=text,
                    model_spec=model_spec,
                    image_content=input_message.image_content,
                )
            if result.parked in {"permission", "external"} and result.pending_confirm:
                await store_pending_confirm(run.conversation_thread_id, result.pending_confirm)

            # yuxi 消息表落库：正文、推理与工具生命周期需和实时流一致。
            if result.text or result.reasoning or result.tool_calls:
                additional_kwargs = {"reasoning_content": result.reasoning} if result.reasoning else {}
                output_message = await conv_repo.add_message_by_thread_id(
                    thread_id=run.conversation_thread_id,
                    role="assistant",
                    content=result.text,
                    message_type="text",
                    extra_metadata={
                        "request_id": run.request_id,
                        "run_id": run_id,
                        "token_usage": result.usage or {},
                        "additional_kwargs": additional_kwargs,
                    },
                    run_id=run_id,
                    request_id=run.request_id,
                )
                if output_message is None:
                    raise RuntimeError("回复消息落库失败：线程不存在")
                for tool_call in result.tool_calls or []:
                    await conv_repo.add_tool_call(
                        message_id=output_message.id,
                        tool_name=tool_call.get("name") or "unknown",
                        tool_input=tool_call.get("args") or {},
                        tool_output=tool_call.get("output") or "",
                        status=tool_call.get("status") or "pending",
                        error_message=tool_call.get("error_message"),
                        langgraph_tool_call_id=tool_call.get("id"),
                    )
                await run_repo.set_output_message(run_id, output_message.id)
            await _sync_input_delivery_status(db, run, result.run_status)
            await db.commit()
        except ValueError as exc:
            message = str(exc)
            await _fail_run(
                db,
                run_repo,
                run,
                message if LEGACY_THREAD_MESSAGE in message else f"执行失败: {message}",
            )
            return
        except Exception as exc:  # noqa: BLE001 - 任务级失败统一终态
            logger.exception(f"agentscope 执行失败 run={run_id}: {exc}")
            await _fail_run(db, run_repo, run, f"执行失败: {exc}")
            return

        # 挂起/中断终态补发 end 帧（前端收尾依赖）；completed 与 steer 中断
        # （非审批挂起）派发队头：引导消息需在被中断的 Run 结束后立即执行。
        if result.run_status == "completed" or (
            result.run_status == "interrupted" and not await has_pending_confirm(run.conversation_thread_id)
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


async def _apply_permission_mode(client, run, mapping) -> None:
    """按 run 的审批模式设置会话权限（完全信任 → bypass 跳过人工确认）。"""
    from yuxi.agentscope.projection import permission_mode_for

    mode = (run.input_payload or {}).get("tool_approval_mode")
    await client.set_permission_mode(
        run.uid,
        mapping.agentscope_agent_id,
        mapping.agentscope_session_id,
        permission_mode_for(mode),
    )


async def _sync_input_delivery_status(db, run, run_status: str) -> None:
    """run 终态回写输入消息投递状态（interrupted 保持原状态以便 UI 区分）。"""
    from yuxi.services.agent_request_queue_service import RUN_STATUS_TO_DELIVERY_STATUS

    delivery = RUN_STATUS_TO_DELIVERY_STATUS.get(run_status)
    if delivery and run.input_message_id:
        await ConversationRepository(db).set_message_delivery_status(run.input_message_id, delivery)


async def _fail_run(db, run_repo, run, message: str) -> None:
    """失败终态提交后续派 FIFO 队头。"""
    await run_repo.set_terminal_status(run.id, status="failed", error_message=message)
    await _emit_end_event(
        run.id,
        run.conversation_thread_id,
        {"status": "error", "error_message": message},
    )
    await _sync_input_delivery_status(db, run, "failed")
    await db.commit()
    await dispatch_next_request(
        uid=run.uid,
        agent_slug=run.agent_slug,
        thread_id=run.conversation_thread_id,
    )


async def _materialize_run_attachments(
    conv_repo: ConversationRepository,
    client: AgentScopeServiceClient,
    *,
    run,
    input_message: Message,
    mapping,
) -> str:
    """绑定并上传本次请求附件，返回包含 workspace 路径的用户文本。"""
    file_ids = (input_message.extra_metadata or {}).get("attachment_file_ids") or []
    if not file_ids:
        return input_message.content
    if not isinstance(file_ids, list) or len(file_ids) > 10:
        raise ValueError("attachment_file_ids 必须是最多 10 项的数组")
    normalized_ids = [str(file_id).strip() for file_id in file_ids]
    if any(not file_id for file_id in normalized_ids) or len(set(normalized_ids)) != len(normalized_ids):
        raise ValueError("attachment_file_ids 不允许空值或重复值")

    attachments = await conv_repo.get_attachments(run.conversation_id)
    requested = set(normalized_ids)
    selected = {str(item.get("file_id")): item for item in attachments if str(item.get("file_id")) in requested}
    if requested != set(selected) or any(item.get("request_id") not in {None, ""} for item in selected.values()):
        raise ValueError("附件不存在、已绑定其他请求或无权访问")

    workspace_paths = []
    for file_id in normalized_ids:
        item = selected[file_id]
        source_path = item.get("storage_path")
        if not isinstance(source_path, str):
            raise ValueError(f"附件 {item.get('file_id')} 缺少存储路径")
        suffix = ".md" if item.get("status") == "parsed" else Path(item.get("file_name") or "file").suffix
        destination = f"/workspace/uploads/{item['file_id']}{suffix}"
        workspace_paths.append(
            await client.upload_workspace_file(
                run.uid,
                mapping.agentscope_agent_id,
                mapping.agentscope_session_id,
                source_path=source_path,
                destination=destination,
            )
        )

    bound = await conv_repo.bind_attachments_to_request(run.conversation_id, run.request_id, normalized_ids)
    if {str(item.get("file_id")) for item in bound} != requested:
        raise RuntimeError("附件绑定状态在上传期间发生变化")

    paths = "\n".join(f"- {path}" for path in workspace_paths)
    return f"{input_message.content}\n\n本次请求附件（workspace 路径）：\n{paths}"


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
    await _apply_permission_mode(client, run, mapping)
    resume_input = (input_message.extra_metadata or {}).get("resume") or {}

    confirm_event = await load_pending_confirm(run.conversation_thread_id)
    decisions = resume_input.get("decisions") if isinstance(resume_input, dict) else None
    event_type = str((confirm_event or {}).get("type", "")).upper()
    if confirm_event and event_type == "REQUIRE_USER_CONFIRM":
        if (
            not isinstance(decisions, list)
            or not decisions
            or any(not isinstance(decision, dict) for decision in decisions)
        ):
            raise ValueError("工具审批恢复缺少有效 decisions")
        approved = [str(d.get("type", "")).lower() in {"approve", "approved", "accept"} for d in decisions]
        result = await _resume_and_collect(client, run, mapping, confirm_event, approved)
        # resume 路径同样必须回写终态，否则 Run 永远停在 running
        await finalize_run(db, run, result)
        await _replace_pending_confirm(run.conversation_thread_id, result)
        return result

    answer = resume_input.get("answer") if isinstance(resume_input, dict) else None
    if confirm_event and event_type == "REQUIRE_EXTERNAL_EXECUTION":
        if answer is None:
            raise ValueError("外部问答恢复缺少 answer")
        result = await _resume_external_and_collect(client, run, mapping, confirm_event, answer)
        await finalize_run(db, run, result)
        await _replace_pending_confirm(run.conversation_thread_id, result)
        return result

    if confirm_event:
        raise ValueError(f"不支持的挂起事件类型: {event_type or 'unknown'}")

    # 没有挂起事件时，文本 resume 仍按普通新一轮输入处理。
    text = answer if isinstance(answer, str) and answer.strip() else input_message.content
    return await execute_run(db, client, run=run, text=text, model_spec=(run.input_payload or {}).get("model_spec"))


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
    调用永久滞留 asking。读取失败必须显式中止，保留挂起状态供重试。
    """
    messages = await client.list_messages(uid, agent_id, session_id)
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
            if isinstance(b, dict) and b.get("type") == "tool_call" and str(b.get("state", "")).lower() == "asking"
        ]
    return []


async def _resume_and_collect(client, run, mapping, confirm_event, approved: list[bool]) -> GatewayRoundResult:
    """订阅在先、恢复在后，收集到 REPLY_END。"""
    queue, pump = start_event_pump(
        client,
        uid=run.uid,
        agent_id=mapping.agentscope_agent_id,
        session_id=mapping.agentscope_session_id,
        read_timeout=READ_TIMEOUT_SECONDS,
    )
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
    total_deadline = asyncio.get_running_loop().time() + READ_TIMEOUT_SECONDS
    try:
        await asyncio.sleep(SUBSCRIBE_SETTLE_SECONDS)
        tool_calls = confirm_event.get("tool_calls") or []
        if len(tool_calls) != len(approved):
            raise ValueError("待审批工具调用数量与审批决定数量不一致")
        decisions_by_id = {call.get("id"): decision for call, decision in zip(tool_calls, approved, strict=True)}
        asking = await _collect_asking_tool_calls(
            client,
            uid=run.uid,
            agent_id=mapping.agentscope_agent_id,
            session_id=mapping.agentscope_session_id,
            reply_id=confirm_event.get("reply_id", ""),
        )
        pending_calls = asking or tool_calls
        unknown_ids = {call.get("id") for call in pending_calls} - set(decisions_by_id)
        if unknown_ids:
            raise ValueError("会话出现未向用户展示的待审批工具调用")
        await client.resume_confirm(
            run.uid,
            mapping.agentscope_agent_id,
            mapping.agentscope_session_id,
            reply_id=confirm_event.get("reply_id", ""),
            tool_calls=pending_calls,
            confirmed=[decisions_by_id[call.get("id")] for call in pending_calls],
        )
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        event_count = 0
        usage = UsageAccumulator(configured_model_spec=mapping.model_spec)
        tool_converter = ToolEventConverter(run.request_id)
        tool_converter.seed_tool_calls(
            tool_calls,
            reply_id=str(confirm_event.get("reply_id") or ""),
        )
        loop = asyncio.get_running_loop()
        while True:
            remaining = total_deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError("resume 收集超时：会话长时间无事件")
            try:
                event = await asyncio.wait_for(queue.get(), timeout=min(stall_poll_seconds, remaining))
            except TimeoutError:
                asking = await _collect_asking_tool_calls(
                    client,
                    uid=run.uid,
                    agent_id=mapping.agentscope_agent_id,
                    session_id=mapping.agentscope_session_id,
                    reply_id=confirm_event.get("reply_id", ""),
                )
                if asking:
                    unknown_ids = {call.get("id") for call in asking} - set(decisions_by_id)
                    if unknown_ids:
                        raise ValueError("会话出现未向用户展示的待审批工具调用")
                    retry_decisions = [decisions_by_id[call.get("id")] for call in asking]
                    await client.resume_confirm(
                        run.uid,
                        mapping.agentscope_agent_id,
                        mapping.agentscope_session_id,
                        reply_id=confirm_event.get("reply_id", ""),
                        tool_calls=asking,
                        confirmed=retry_decisions,
                    )
                continue
            if isinstance(event, Exception):
                raise event
            event_count += 1
            usage.observe(event)
            event_type = str(event.get("type", "")).upper()
            if event_type == "TEXT_BLOCK_DELTA":
                text_parts.append(event.get("delta", ""))
            elif event_type == "THINKING_BLOCK_DELTA":
                reasoning_parts.append(event.get("delta", ""))

            chunks = event_to_chunks(event, request_id=run.request_id)
            chunks.extend(tool_converter.feed(event))
            if chunks:
                await append_run_stream_event(
                    run.id,
                    "messages",
                    {"items": chunks},
                    thread_id=run.conversation_thread_id,
                )

            if event_type in {
                "REQUIRE_USER_CONFIRM",
                "REQUIRE_EXTERNAL_EXECUTION",
            }:
                tool_converter.seed_tool_calls(
                    event.get("tool_calls") or [],
                    reply_id=str(event.get("reply_id") or ""),
                )
                return _parked_resume_result(
                    event,
                    text_parts,
                    reasoning_parts,
                    event_count,
                    tool_converter.history_tool_calls(),
                    usage.snapshot(complete=False),
                )
            if event_type == "REPLY_END":
                terminal = reply_end_to_terminal(event, request_id=run.request_id)
                await append_run_stream_event(
                    run.id,
                    "end",
                    {"status": terminal.run_status, "chunk": terminal.chunk},
                    thread_id=run.conversation_thread_id,
                )
                return GatewayRoundResult(
                    run_status=terminal.run_status,
                    text="".join(text_parts),
                    reasoning="".join(reasoning_parts),
                    event_count=event_count,
                    usage=usage.snapshot(complete=True),
                    error_message=terminal.chunk.get("error_message"),
                    tool_calls=tool_converter.history_tool_calls(),
                )
    finally:
        await cancel_tasks(pump, cancel_task)


async def _resume_external_and_collect(client, run, mapping, pending_event: dict, answer: object) -> GatewayRoundResult:
    """订阅在先并以 ExternalExecutionResultEvent 恢复用户问答。"""
    queue, pump = start_event_pump(
        client,
        uid=run.uid,
        agent_id=mapping.agentscope_agent_id,
        session_id=mapping.agentscope_session_id,
        read_timeout=READ_TIMEOUT_SECONDS,
    )
    cancel_task = start_cancel_watcher(
        client,
        uid=run.uid,
        agent_id=mapping.agentscope_agent_id,
        session_id=mapping.agentscope_session_id,
        run_id=run.id,
    )
    try:
        await asyncio.sleep(SUBSCRIBE_SETTLE_SECONDS)
        await client.resume_external_execution(
            run.uid,
            mapping.agentscope_agent_id,
            mapping.agentscope_session_id,
            reply_id=pending_event.get("reply_id", ""),
            tool_calls=pending_event.get("tool_calls") or [],
            answer=answer,
        )
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        event_count = 0
        usage = UsageAccumulator(configured_model_spec=mapping.model_spec)
        tool_converter = ToolEventConverter(run.request_id)
        tool_converter.seed_tool_calls(
            pending_event.get("tool_calls") or [],
            reply_id=str(pending_event.get("reply_id") or ""),
        )
        while True:
            event = await asyncio.wait_for(queue.get(), timeout=180.0)
            if isinstance(event, Exception):
                raise event
            event_count += 1
            usage.observe(event)
            event_type = str(event.get("type", "")).upper()
            if event_type == "TEXT_BLOCK_DELTA":
                text_parts.append(event.get("delta", ""))
            elif event_type == "THINKING_BLOCK_DELTA":
                reasoning_parts.append(event.get("delta", ""))

            chunks = event_to_chunks(event, request_id=run.request_id)
            chunks.extend(tool_converter.feed(event))
            if chunks:
                await append_run_stream_event(
                    run.id,
                    "messages",
                    {"items": chunks},
                    thread_id=run.conversation_thread_id,
                )

            if event_type in {"REQUIRE_USER_CONFIRM", "REQUIRE_EXTERNAL_EXECUTION"}:
                tool_converter.seed_tool_calls(
                    event.get("tool_calls") or [],
                    reply_id=str(event.get("reply_id") or ""),
                )
                return _parked_resume_result(
                    event,
                    text_parts,
                    reasoning_parts,
                    event_count,
                    tool_converter.history_tool_calls(),
                    usage.snapshot(complete=False),
                )
            if event_type == "REPLY_END":
                terminal = reply_end_to_terminal(event, request_id=run.request_id)
                await append_run_stream_event(
                    run.id,
                    "end",
                    {"status": terminal.run_status, "chunk": terminal.chunk},
                    thread_id=run.conversation_thread_id,
                )
                return GatewayRoundResult(
                    run_status=terminal.run_status,
                    text="".join(text_parts),
                    reasoning="".join(reasoning_parts),
                    event_count=event_count,
                    usage=usage.snapshot(complete=True),
                    error_message=terminal.chunk.get("error_message"),
                    tool_calls=tool_converter.history_tool_calls(),
                )
    finally:
        await cancel_tasks(pump, cancel_task)


def _parked_resume_result(
    event: dict,
    text_parts: list[str],
    reasoning_parts: list[str],
    event_count: int,
    tool_calls: list[dict],
    usage: dict,
) -> GatewayRoundResult:
    """把恢复期间再次出现的审批或问答转换为新的挂起结果。"""
    event_type = str(event.get("type", "")).upper()
    return GatewayRoundResult(
        run_status="interrupted",
        text="".join(text_parts),
        reasoning="".join(reasoning_parts),
        event_count=event_count,
        parked="permission" if event_type == "REQUIRE_USER_CONFIRM" else "external",
        usage=usage,
        pending_confirm=event,
        tool_calls=tool_calls,
    )


async def _replace_pending_confirm(thread_id: str, result: GatewayRoundResult) -> None:
    """成功恢复后清理旧挂起，或原子语义地以新挂起覆盖。"""
    if result.parked in {"permission", "external"} and result.pending_confirm:
        await store_pending_confirm(thread_id, result.pending_confirm)
    else:
        await clear_pending_confirm(thread_id)


async def _emit_end_event(run_id: str, thread_id: str, payload: dict) -> None:
    """补发 end 事件帧（前端终态收尾依赖）。"""
    from yuxi.services.run_queue_service import append_run_stream_event

    try:
        await append_run_stream_event(run_id, "end", payload, thread_id=thread_id)
    except Exception as exc:  # noqa: BLE001 - 事件补发失败不改变终态
        logger.warning(f"补发 end 事件失败 run={run_id}: {exc}")
