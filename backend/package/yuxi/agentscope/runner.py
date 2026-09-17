"""线程会话保障与一轮对话的触发/收集（工单 02）。

ensure_thread_session 维护 Thread ↔ Session 的一一映射（首次使用时投影
模型供应商并创建 agent/credential/session，事实落 yuxi 库）；
collect_chat_round 订阅会话事件流后触发对话，收集本次 reply 的完整事件。

事件消费必须在运行期在线进行：run 结束并持久化后，service 会清空回放
日志（事件的事实归宿是消息表）。订阅与触发之间的短暂空档由回放日志
覆盖。事件 type 为大写枚举名（如 REPLY_END），匹配一律忽略大小写。
"""

import asyncio
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.agentscope.client import AgentScopeServiceClient
from yuxi.agentscope.event_stream import READ_TIMEOUT_SECONDS
from yuxi.agentscope.config_projection import RuntimeProjection, project_runtime
from yuxi.agentscope.event_stream import cancel_tasks, start_event_pump
from yuxi.agentscope.thread_guard import ensure_thread_eligible
from yuxi.repositories import agentscope_thread_sessions as thread_session_repo
from yuxi.repositories.agentscope_team_workers import AgentScopeTeamWorkerRepository
from yuxi.repositories.agentscope_thread_sessions import AgentScopeThreadSession

# 订阅建立后的等待窗口：覆盖 HTTP 连接与回放建立，早于 chat 触发即可
SUBSCRIBE_SETTLE_SECONDS = 0.5
STALE_PENDING_RECOVERY_ATTEMPTS = 100
STALE_PENDING_RECOVERY_INTERVAL_SECONDS = 0.1
_PENDING_SESSION_STATUSES = {"awaiting_permission", "awaiting_external_result"}


@dataclass
class ChatRoundResult:
    """一轮对话的收集结果：事件原文与拼装文本。"""

    events: list[dict]
    text: str
    parked: str | None = None  # 挂起原因（permission=等待工具审批）


async def recover_untracked_pending_session(
    client: AgentScopeServiceClient,
    *,
    uid: str,
    agent_id: str,
    session_id: str,
) -> bool:
    """清理由后台续写遗留、但 Yuxi 已无 pending 事实的挂起状态。"""
    session_status = await client.get_session_status(uid, agent_id, session_id)
    if session_status not in _PENDING_SESSION_STATUSES:
        return False

    await client.interrupt_session(uid, agent_id, session_id)
    for _ in range(STALE_PENDING_RECOVERY_ATTEMPTS):
        await asyncio.sleep(STALE_PENDING_RECOVERY_INTERVAL_SECONDS)
        session_status = await client.get_session_status(uid, agent_id, session_id)
        if session_status == "idle":
            return True
        if session_status not in {*_PENDING_SESSION_STATUSES, "running"}:
            raise RuntimeError(f"AgentScope 挂起恢复进入未知状态: {session_status}")
    raise TimeoutError("AgentScope 挂起状态恢复超时")


async def ensure_thread_session(
    db: AsyncSession,
    client: AgentScopeServiceClient,
    *,
    uid: str,
    thread_id: str,
    agent_slug: str,
    model_spec: str | None = None,
    projection: RuntimeProjection | None = None,
    department_id: int | None = None,
) -> AgentScopeThreadSession:
    """保障线程映射的 session 存在；命中映射直接返回，缺失则投影并创建。

    存量线程守卫：旧栈历史且无映射的线程拒绝接入（只读+提示开新线程）。
    """
    existing = await thread_session_repo.get_thread_session(db, uid=uid, thread_id=thread_id)
    if existing is not None:
        if not getattr(existing, "agentscope_workspace_id", None):
            workspace_id = await client.get_session_workspace_id(
                uid,
                existing.agentscope_agent_id,
                existing.agentscope_session_id,
            )
            await thread_session_repo.set_thread_session_workspace_id(
                db,
                existing,
                agentscope_workspace_id=workspace_id,
            )
        projection = projection or await project_runtime(
            db,
            uid=uid,
            agent_slug=agent_slug,
            model_spec=model_spec,
            thread_id=thread_id,
            department_id=department_id,
        )
        credential_id = await client.create_credential(uid, projection.credential_data)
        await client.update_agent(uid, existing.agentscope_agent_id, projection.agent_request)
        await client.update_session_model(
            uid,
            existing.agentscope_agent_id,
            existing.agentscope_session_id,
            {**projection.chat_model_config, "credential_id": credential_id},
        )
        workers = await AgentScopeTeamWorkerRepository(db).list_active_for_parent_thread(
            uid=uid,
            parent_thread_id=thread_id,
        )
        for worker in workers:
            await client.update_session_model(
                uid,
                worker.worker_agent_id,
                worker.worker_session_id,
                {**projection.chat_model_config, "credential_id": credential_id},
            )
        current_skills = set(
            await client.list_workspace_skills(
                uid,
                existing.agentscope_agent_id,
                existing.agentscope_session_id,
            )
        )
        desired_skills = {item["slug"]: item for item in projection.skills}
        for slug in sorted(current_skills - desired_skills.keys()):
            await client.remove_workspace_skill(
                uid,
                existing.agentscope_agent_id,
                existing.agentscope_session_id,
                slug,
            )
        for slug in desired_skills.keys() - current_skills:
            skill = desired_skills[slug]
            if "snapshot_files" in skill:
                continue
            await client.add_workspace_skill(
                uid,
                existing.agentscope_agent_id,
                existing.agentscope_session_id,
                skill["source_dir"],
            )
        old_credential_id = existing.agentscope_credential_id
        await thread_session_repo.update_thread_session_model(
            db,
            existing,
            model_spec=projection.model_spec,
            agentscope_credential_id=credential_id,
        )
        await db.commit()
        if old_credential_id != credential_id:
            await client.delete_credential(uid, old_credential_id)
        return existing
    await ensure_thread_eligible(db, uid=uid, thread_id=thread_id)

    projection = projection or await project_runtime(
        db,
        uid=uid,
        agent_slug=agent_slug,
        model_spec=model_spec,
        thread_id=thread_id,
        department_id=department_id,
    )
    credential_id = await client.create_credential(uid, projection.credential_data)
    agent_id = await client.create_agent(uid, projection.agent_request)
    chat_model_config = {**projection.chat_model_config, "credential_id": credential_id}
    session_id = await client.create_session(uid, agent_id, chat_model_config)
    workspace_id = await client.get_session_workspace_id(uid, agent_id, session_id)
    for skill in projection.skills:
        if "snapshot_files" not in skill:
            await client.add_workspace_skill(uid, agent_id, session_id, skill["source_dir"])
    record = await thread_session_repo.create_thread_session(
        db,
        uid=uid,
        thread_id=thread_id,
        agent_slug=agent_slug,
        model_spec=projection.model_spec,
        agentscope_agent_id=agent_id,
        agentscope_credential_id=credential_id,
        agentscope_session_id=session_id,
        agentscope_workspace_id=workspace_id,
    )
    await db.commit()
    return record


async def collect_chat_round(
    client: AgentScopeServiceClient,
    *,
    uid: str,
    agent_id: str,
    session_id: str,
    text: str,
    read_timeout: float = READ_TIMEOUT_SECONDS,
) -> ChatRoundResult:
    """订阅在先、触发在后，收集本次 reply 的完整事件流并拼装文本。

    以「最近一次 REPLY_START 到其 REPLY_END」的完整区段识别本轮；
    会话锁保证同 session 串行，本轮必然是最后完成的区段。
    """
    queue, pump_task = start_event_pump(
        client,
        uid=uid,
        agent_id=agent_id,
        session_id=session_id,
        read_timeout=read_timeout,
    )
    try:
        await asyncio.sleep(SUBSCRIBE_SETTLE_SECONDS)
        await client.trigger_chat(uid, agent_id, session_id, text)

        events: list[dict] = []
        span_start = -1
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        while True:
            event = await asyncio.wait_for(queue.get(), timeout=read_timeout)
            if isinstance(event, Exception):
                raise event
            event_type = str(event.get("type", "")).upper()
            if event_type == "TEXT_BLOCK_DELTA":
                text_parts.append(event.get("delta", ""))
            elif event_type == "THINKING_BLOCK_DELTA":
                reasoning_parts.append(event.get("delta", ""))
            if event_type == "REPLY_START":
                span_start = len(events)
                events.append(event)
                continue
            if span_start < 0:
                continue  # 订阅前残留的无关事件
            events.append(event)
            if event_type == "REQUIRE_USER_CONFIRM":
                return ChatRoundResult(
                    events=events[span_start:],
                    text="".join(text_parts),
                    parked="permission",
                )
            if event_type == "REPLY_END":
                round_text = "".join(text_parts)
                return ChatRoundResult(events=events[span_start:], text=round_text)
    finally:
        await cancel_tasks(pump_task)
