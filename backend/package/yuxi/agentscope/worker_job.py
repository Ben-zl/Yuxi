"""agentscope 执行体的队列任务（迁移工单 14 · ② 网关翻转）。

替代旧 LangGraph 执行路径：从 AgentRun 列与输入消息恢复参数 → 映射
保障 → 网关协议转换写入 Run 事件流 → yuxi 消息表落库（前端历史视图）
→ 终态回写 → 派发队头。审批挂起经 pending_confirm 存储支撑 resume。
"""

import asyncio
import os
from pathlib import Path

from sqlalchemy import select
from yuxi.agentscope.client import AgentScopeServiceClient
from yuxi.agentscope.event_stream import READ_TIMEOUT_SECONDS
from yuxi.agentscope.execution import execute_run, finalize_run, has_active_child_runs, persist_run_output
from yuxi.agentscope.event_stream import cancel_tasks, start_event_pump
from yuxi.agentscope.gateway import (
    TEAM_TOOL_NAMES,
    GatewayRoundResult,
    collect_run_events,
    start_cancel_watcher,
)
from yuxi.agentscope.protocol import ToolEventConverter
from yuxi.agentscope.run_lease import (
    RUN_LEASE_SECONDS,
    start_run_lease_heartbeat,
    stop_run_lease_heartbeat,
)
from yuxi.agentscope.runner import SUBSCRIBE_SETTLE_SECONDS, ensure_thread_session
from yuxi.agentscope.thread_guard import (
    LEGACY_THREAD_MESSAGE,
    clear_pending_confirm,
    has_pending_confirm,
    load_pending_confirm,
    store_pending_confirm,
)
from yuxi.repositories.agent_run_repository import (
    DEFAULT_WORKER_ID,
    TERMINAL_RUN_STATUSES,
    AgentRunRepository,
)
from yuxi.repositories.conversation_repository import ConversationRepository
from yuxi.services.agent_request_queue_service import dispatch_next_request
from yuxi.services.agent_run_manifest_service import (
    build_run_manifest_result,
    compute_manifest_fingerprint,
)
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import Message, User
from yuxi.utils import logger

WORKER_ID = DEFAULT_WORKER_ID


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
            await _fail_run(
                db,
                run_repo,
                run_id=run.id,
                uid=run.uid,
                agent_slug=run.agent_slug,
                thread_id=run.conversation_thread_id,
                input_message_id=run.input_message_id,
                worker_id=run.worker_id,
                message="运行任务缺少输入消息",
            )
            return

        claim_result = await run_repo.mark_running(
            run_id,
            worker_id=WORKER_ID,
            lease_seconds=RUN_LEASE_SECONDS,
        )
        lease_owned = isinstance(claim_result, tuple)
        if lease_owned:
            claimed_run, acquired = claim_result
        else:
            acquired = claim_result is not None
            claimed_run = claim_result if getattr(claim_result, "id", None) == run_id else run
        if claimed_run is None or not acquired:
            await db.rollback()
            return
        run = claimed_run
        worker_id = WORKER_ID if lease_owned else getattr(run, "worker_id", None)
        owner_kwargs = {"worker_id": worker_id} if worker_id else {}
        run_uid = run.uid
        run_agent_slug = run.agent_slug
        run_thread_id = run.conversation_thread_id
        run_input_message_id = run.input_message_id
        # 先发布 lease/Attempt ownership；manifest 构建失败时 rollback 不能撤销
        # ownership，否则失败终态会失去合法的 fencing owner。
        await db.commit()
        try:
            await _record_run_manifest(db, run_repo, run, worker_id=worker_id)
        except Exception as exc:  # noqa: BLE001 - manifest 是执行前置事实
            logger.exception(f"AgentScope Run manifest 固化失败 run={run_id}: {exc}")
            await db.rollback()
            await _fail_run(
                db,
                run_repo,
                run_id=run_id,
                uid=run_uid,
                agent_slug=run_agent_slug,
                thread_id=run_thread_id,
                input_message_id=run_input_message_id,
                worker_id=worker_id,
                message=f"运行清单固化失败: {exc}",
            )
            return
        await db.commit()

        client = AgentScopeServiceClient(os.getenv("AGENTSCOPE_BASE_URL", "http://agentscope:8100"))
        heartbeat_task = start_run_lease_heartbeat(run.id, worker_id=worker_id) if lease_owned and worker_id else None
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
                await _verify_run_manifest(db, run)
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
                    mapping=mapping,
                    persist_result=False,
                )
            if result.parked in {"permission", "external"} and result.pending_confirm:
                await store_pending_confirm(run.conversation_thread_id, result.pending_confirm)

            output_message = await persist_run_output(db, run, result)
            if output_message is not None:
                await run_repo.set_output_message(run_id, output_message.id, **owner_kwargs)
            await finalize_run(db, run, result, **owner_kwargs)
            await _sync_input_delivery_status(
                db,
                input_message_id=run_input_message_id,
                run_status=result.run_status,
            )
            await db.commit()
        except ValueError as exc:
            await db.rollback()
            message = str(exc)
            await _fail_run(
                db,
                run_repo,
                run_id=run_id,
                uid=run_uid,
                agent_slug=run_agent_slug,
                thread_id=run_thread_id,
                input_message_id=run_input_message_id,
                worker_id=worker_id,
                message=message if LEGACY_THREAD_MESSAGE in message else f"执行失败: {message}",
            )
            return
        except Exception as exc:  # noqa: BLE001 - 任务级失败统一终态
            logger.exception(f"agentscope 执行失败 run={run_id}: {exc}")
            await db.rollback()
            await _fail_run(
                db,
                run_repo,
                run_id=run_id,
                uid=run_uid,
                agent_slug=run_agent_slug,
                thread_id=run_thread_id,
                input_message_id=run_input_message_id,
                worker_id=worker_id,
                message=f"执行失败: {exc}",
            )
            return
        except BaseException:
            # ARQ 重载和任务取消走 BaseException；由 finally 统一停止 heartbeat。
            raise
        finally:
            if heartbeat_task is not None:
                await stop_run_lease_heartbeat(run.id)

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
        await _notify_agent_task(run.id, result.run_status)


async def _record_run_manifest(db, run_repo, run, *, worker_id: str | None) -> None:
    """在 AgentScope 执行前固化当前 Run 的实际运行资产。"""

    if not worker_id:
        raise RuntimeError("Run 缺少有效 lease owner")
    user = await db.scalar(select(User).where(User.uid == run.uid))
    if user is None:
        raise RuntimeError("Run 所属用户不存在")
    result = await build_run_manifest_result(run=run, user=user, db=db)
    fingerprint = compute_manifest_fingerprint(result.manifest)
    persisted, recorded = await run_repo.record_run_manifest(
        run.id,
        manifest=result.manifest,
        fingerprint=fingerprint,
        worker_id=worker_id,
    )
    if persisted is None:
        raise RuntimeError("运行清单对应的 Run 不存在")
    persisted_fingerprint = getattr(persisted, "manifest_fingerprint", fingerprint)
    if persisted_fingerprint != fingerprint:
        raise RuntimeError("运行清单已由其他配置固化，拒绝覆盖")
    if not recorded:
        # write-once 幂等重投只能复用同一持久化指纹，不能以当前配置覆盖内存。
        run.manifest_fingerprint = persisted_fingerprint
        return
    run.manifest_fingerprint = persisted_fingerprint


async def _verify_run_manifest(db, run) -> None:
    """在模型或工具执行前拒绝已偏离固化清单的运行时配置。"""

    user = await db.scalar(select(User).where(User.uid == run.uid))
    if user is None:
        raise RuntimeError("Run 所属用户不存在")
    current = await build_run_manifest_result(run=run, user=user, db=db)
    if compute_manifest_fingerprint(current.manifest) != run.manifest_fingerprint:
        raise RuntimeError("运行时配置已在 manifest 固化后变化，请重新提交请求")


async def _notify_agent_task(run_id: str, run_status: str) -> None:
    """任务执行 Run 终态推进任务队列；普通 Run 直接返回。"""
    from yuxi.services.agent_task_dispatcher import notify_agent_task_finished

    try:
        await notify_agent_task_finished(run_id, run_status)
    except Exception as exc:  # noqa: BLE001 - 任务推进失败不改变 Run 终态
        logger.warning(f"任务执行推进失败 run={run_id}: {exc}")


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


async def _sync_input_delivery_status(db, *, input_message_id: int | None, run_status: str) -> None:
    """Run 终态回写输入消息投递状态（interrupted 保持原状态以便 UI 区分）。"""
    from yuxi.services.agent_request_queue_service import RUN_STATUS_TO_DELIVERY_STATUS

    delivery = RUN_STATUS_TO_DELIVERY_STATUS.get(run_status)
    if delivery and input_message_id:
        await ConversationRepository(db).set_message_delivery_status(input_message_id, delivery)


async def _fail_run(
    db,
    run_repo,
    *,
    run_id: str,
    uid: str,
    agent_slug: str,
    thread_id: str,
    input_message_id: int | None,
    worker_id: str | None,
    message: str,
) -> bool:
    """使用 rollback 前快照收束失败 Run，并续派 FIFO 队头。"""
    from yuxi.services.run_queue_service import clear_cancel_signal, has_cancel_signal

    cancelled = await has_cancel_signal(run_id)
    terminal_status = "cancelled" if cancelled else "failed"
    error_message = None if cancelled else message
    end_payload = {"status": "cancelled"} if cancelled else {"status": "error", "error_message": message}

    terminal_kwargs = {
        "status": terminal_status,
        "error_message": error_message,
    }
    if worker_id:
        terminal_kwargs["worker_id"] = worker_id
    persisted, changed = await run_repo.set_terminal_status(run_id, **terminal_kwargs)
    if persisted is None or not changed:
        return False
    terminal_status = persisted.status
    await _sync_input_delivery_status(
        db,
        input_message_id=input_message_id,
        run_status=terminal_status,
    )
    await db.commit()
    await _emit_end_event(run_id, thread_id, end_payload)
    if cancelled:
        await clear_cancel_signal(run_id)
    await dispatch_next_request(uid=uid, agent_slug=agent_slug, thread_id=thread_id)
    await _notify_agent_task(run_id, terminal_status)
    return True


async def fail_agent_run_by_id(run_id: str, message: str) -> None:
    """中断并确认远端执行停止后，使用独立事务收束超时 Run。"""
    from yuxi.agentscope.client import AgentScopeServiceError
    from yuxi.repositories.agentscope_team_workers import AgentScopeTeamWorkerRepository
    from yuxi.repositories.agentscope_thread_sessions import get_thread_session

    async with pg_manager.get_async_session_context() as db:
        run_repo = AgentRunRepository(db)
        run = await run_repo.get_run(run_id)
        if run is None or run.status in TERMINAL_RUN_STATUSES:
            return
        mapping = await get_thread_session(
            db,
            uid=run.uid,
            thread_id=run.conversation_thread_id,
        )
        child_runs = await run_repo.list_active_child_runs_for_user(run.id, run.uid)
        child_bindings = await AgentScopeTeamWorkerRepository(db).list_for_active_runs(
            uid=run.uid,
            run_ids=[child.id for child in child_runs],
        )
        uid = run.uid

    client = AgentScopeServiceClient(
        os.getenv("AGENTSCOPE_BASE_URL", "http://agentscope:8100"),
        timeout=5,
    )
    sessions = []
    if mapping is not None:
        sessions.append((mapping.agentscope_agent_id, mapping.agentscope_session_id))
    sessions.extend((binding.worker_agent_id, binding.worker_session_id) for binding in child_bindings)

    async with asyncio.timeout(25):

        async def interrupt(agent_id: str, session_id: str) -> None:
            try:
                await client.interrupt_session(uid, agent_id, session_id)
            except AgentScopeServiceError as exc:
                if exc.status_code != 404:
                    raise

        await asyncio.gather(*(interrupt(agent_id, session_id) for agent_id, session_id in sessions))

        pending_sessions = list(sessions)
        for _ in range(40):
            statuses = await asyncio.gather(
                *(_session_is_stopped(client, uid, agent_id, session_id) for agent_id, session_id in pending_sessions)
            )
            pending_sessions = [
                session for session, stopped in zip(pending_sessions, statuses, strict=True) if not stopped
            ]
            if not pending_sessions:
                break
            await asyncio.sleep(0.25)
        if pending_sessions:
            raise RuntimeError("AgentScope Session 未在超时后停止")

        from yuxi.agentscope.recovery import reconcile_active_team_child_runs

        await reconcile_active_team_child_runs(
            client,
            parent_run_id=run_id,
            uid=uid,
        )
        await _fail_remaining_timeout_children(
            parent_run_id=run_id,
            uid=uid,
        )
        async with pg_manager.get_async_session_context() as db:
            run_repo = AgentRunRepository(db)
            run = await run_repo.get_run(run_id)
            if run is None or run.status in TERMINAL_RUN_STATUSES:
                return
            await _fail_run(
                db,
                run_repo,
                run_id=run.id,
                uid=run.uid,
                agent_slug=run.agent_slug,
                thread_id=run.conversation_thread_id,
                input_message_id=run.input_message_id,
                worker_id=run.worker_id,
                message=message,
            )


async def _session_is_stopped(
    client: AgentScopeServiceClient,
    uid: str,
    agent_id: str,
    session_id: str,
) -> bool:
    """确认 AgentScope Session 已空闲或不存在。"""
    from yuxi.agentscope.client import AgentScopeServiceError

    try:
        return await client.get_session_status(uid, agent_id, session_id) == "idle"
    except AgentScopeServiceError as exc:
        if exc.status_code == 404:
            return True
        raise


async def _fail_remaining_timeout_children(*, parent_run_id: str, uid: str) -> None:
    """收束没有可恢复 reply 的超时 child Run，并确认全部进入终态。"""
    async with pg_manager.get_async_session_context() as db:
        runs = AgentRunRepository(db)
        children = await runs.list_active_child_runs_for_user(parent_run_id, uid)
        for child in children:
            await _fail_run(
                db,
                runs,
                run_id=child.id,
                uid=child.uid,
                agent_slug=child.agent_slug,
                thread_id=child.conversation_thread_id,
                input_message_id=child.input_message_id,
                worker_id=child.worker_id,
                message="执行因父运行超时而终止",
            )

    async with pg_manager.get_async_session_context() as db:
        remaining = await AgentRunRepository(db).list_active_child_runs_for_user(
            parent_run_id,
            uid,
        )
        if remaining:
            raise RuntimeError("父运行超时后仍存在未结束的 child Run")


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

    兼容已持久化的审批决定与主动提问回答，并由外层统一持久化输出和终态。
    """
    mapping = await ensure_thread_session(
        db,
        client,
        uid=run.uid,
        thread_id=run.conversation_thread_id,
        agent_slug=run.agent_slug,
        model_spec=(run.input_payload or {}).get("model_spec"),
    )
    await _verify_run_manifest(db, run)
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
        await _replace_pending_confirm(run.conversation_thread_id, result)
        return result

    answer = resume_input.get("answer") if isinstance(resume_input, dict) else None
    if confirm_event and event_type == "REQUIRE_EXTERNAL_EXECUTION":
        if answer is None:
            raise ValueError("外部问答恢复缺少 answer")
        result = await _resume_external_and_collect(client, run, mapping, confirm_event, answer)
        await _replace_pending_confirm(run.conversation_thread_id, result)
        return result

    if confirm_event:
        raise ValueError(f"不支持的挂起事件类型: {event_type or 'unknown'}")

    # 没有挂起事件时，文本 resume 仍按普通新一轮输入处理。
    text = answer if isinstance(answer, str) and answer.strip() else input_message.content
    return await execute_run(
        db,
        client,
        run=run,
        text=text,
        model_spec=(run.input_payload or {}).get("model_spec"),
        persist_result=False,
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
    """订阅在先、恢复在后，并保留审批停滞自愈。"""
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
        tool_converter = ToolEventConverter(run.request_id)
        tool_converter.seed_tool_calls(
            tool_calls,
            reply_id=str(confirm_event.get("reply_id") or ""),
        )

        async def _retry_stalled_confirmations() -> None:
            """补发本轮审批决定，解除并行工具调用的 asking 停滞。"""
            asking_calls = await _collect_asking_tool_calls(
                client,
                uid=run.uid,
                agent_id=mapping.agentscope_agent_id,
                session_id=mapping.agentscope_session_id,
                reply_id=confirm_event.get("reply_id", ""),
            )
            if not asking_calls:
                return
            unknown_ids = {call.get("id") for call in asking_calls} - set(decisions_by_id)
            if unknown_ids:
                raise ValueError("会话出现未向用户展示的待审批工具调用")
            await client.resume_confirm(
                run.uid,
                mapping.agentscope_agent_id,
                mapping.agentscope_session_id,
                reply_id=confirm_event.get("reply_id", ""),
                tool_calls=asking_calls,
                confirmed=[decisions_by_id[call.get("id")] for call in asking_calls],
            )

        return await collect_run_events(
            queue,
            client,
            uid=run.uid,
            agent_id=mapping.agentscope_agent_id,
            session_id=mapping.agentscope_session_id,
            run_id=run.id,
            request_id=run.request_id,
            thread_id=run.conversation_thread_id,
            read_timeout=READ_TIMEOUT_SECONDS,
            configured_model_spec=mapping.model_spec,
            tool_converter=tool_converter,
            team_tool_seen=any(
                decision and str(call.get("name") or "") in TEAM_TOOL_NAMES
                for call, decision in zip(tool_calls, approved, strict=True)
            ),
            idle_callback=_retry_stalled_confirmations,
            idle_poll_seconds=stall_poll_seconds,
            total_timeout=READ_TIMEOUT_SECONDS,
            emit_parked_end=False,
            has_active_child_runs=lambda: has_active_child_runs(client, run.id, run.uid),
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
        tool_converter = ToolEventConverter(run.request_id)
        pending_calls = pending_event.get("tool_calls") or []
        tool_converter.seed_tool_calls(pending_calls, reply_id=str(pending_event.get("reply_id") or ""))
        return await collect_run_events(
            queue,
            client,
            uid=run.uid,
            agent_id=mapping.agentscope_agent_id,
            session_id=mapping.agentscope_session_id,
            run_id=run.id,
            request_id=run.request_id,
            thread_id=run.conversation_thread_id,
            read_timeout=READ_TIMEOUT_SECONDS,
            configured_model_spec=mapping.model_spec,
            tool_converter=tool_converter,
            team_tool_seen=any(str(call.get("name") or "") in TEAM_TOOL_NAMES for call in pending_calls),
            emit_parked_end=False,
            has_active_child_runs=lambda: has_active_child_runs(client, run.id, run.uid),
        )
    finally:
        await cancel_tasks(pump, cancel_task)


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
