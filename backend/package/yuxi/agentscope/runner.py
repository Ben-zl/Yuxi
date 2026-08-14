"""线程会话保障与一轮对话的触发/收集（工单 02）。

ensure_thread_session 维护 Thread ↔ Session 的一一映射（首次使用时投影
模型供应商并创建 agent/credential/session，事实落 yuxi 库）；
collect_chat_round 订阅会话事件流后触发对话，收集本次 reply 的完整事件。

事件消费必须在运行期在线进行：run 结束并持久化后，service 会清空回放
日志（事件的事实归宿是消息表）。订阅与触发之间的短暂空档由回放日志
覆盖。事件 type 为大写枚举名（如 REPLY_END），匹配一律忽略大小写。
"""

import asyncio
import contextlib
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.agentscope.client import AgentScopeServiceClient
from yuxi.agentscope.config_projection import project_runtime
from yuxi.agentscope.tools import bind_thread_mcps
from yuxi.repositories import agentscope_thread_sessions as thread_session_repo
from yuxi.repositories.agentscope_thread_sessions import AgentScopeThreadSession

# 订阅建立后的等待窗口：覆盖 HTTP 连接与回放建立，早于 chat 触发即可
SUBSCRIBE_SETTLE_SECONDS = 0.5


@dataclass
class ChatRoundResult:
    """一轮对话的收集结果：事件原文与拼装文本。"""

    events: list[dict]
    text: str
    parked: str | None = None  # 挂起原因（permission=等待工具审批）


async def ensure_thread_session(
    db: AsyncSession,
    client: AgentScopeServiceClient,
    *,
    uid: str,
    thread_id: str,
    agent_slug: str,
    model_spec: str | None = None,
) -> AgentScopeThreadSession:
    """保障线程映射的 session 存在；命中映射直接返回，缺失则投影并创建。"""
    existing = await thread_session_repo.get_thread_session(db, uid=uid, thread_id=thread_id)
    if existing is not None:
        return existing

    projection = await project_runtime(
        db, uid=uid, agent_slug=agent_slug, model_spec=model_spec
    )
    credential_id = await client.create_credential(uid, projection.credential_data)
    agent_id = await client.create_agent(uid, projection.agent_request)
    chat_model_config = {**projection.chat_model_config, "credential_id": credential_id}
    session_id = await client.create_session(uid, agent_id, chat_model_config)
    await bind_thread_mcps(
        db,
        client,
        uid=uid,
        mcp_server_names=projection.mcp_server_names,
        agent_id=agent_id,
        session_id=session_id,
    )

    record = await thread_session_repo.create_thread_session(
        db,
        uid=uid,
        thread_id=thread_id,
        agent_slug=agent_slug,
        model_spec=projection.model_spec,
        agentscope_agent_id=agent_id,
        agentscope_credential_id=credential_id,
        agentscope_session_id=session_id,
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
    read_timeout: float = 180.0,
) -> ChatRoundResult:
    """订阅在先、触发在后，收集本次 reply 的完整事件流并拼装文本。

    以「最近一次 REPLY_START 到其 REPLY_END」的完整区段识别本轮；
    会话锁保证同 session 串行，本轮必然是最后完成的区段。
    """
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

        events: list[dict] = []
        span_start = -1
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        while True:
            event = await asyncio.wait_for(queue.get(), timeout=read_timeout)
            if isinstance(event, Exception):
                raise event
            event_type = str(event.get("type", "")).lower()
            if event_type == "text_block_delta":
                text_parts.append(event.get("delta", ""))
            elif event_type == "thinking_block_delta":
                reasoning_parts.append(event.get("delta", ""))
            if event_type == "reply_start":
                span_start = len(events)
                events.append(event)
                continue
            if span_start < 0:
                continue  # 订阅前残留的无关事件
            events.append(event)
            if event_type == "require_user_confirm":
                return ChatRoundResult(
                    events=events[span_start:],
                    text="".join(text_parts),
                    parked="permission",
                )
            if event_type == "reply_end":
                round_text = "".join(text_parts)
                return ChatRoundResult(events=events[span_start:], text=round_text)
    finally:
        pump_task.cancel()
        # 等待取消传播完成，让 httpx 流上下文在协程栈展开中正常关闭，
        # 避免连接清理协程被 GC 兜底（会以 unraisable 警告污染后续测试）
        with contextlib.suppress(asyncio.CancelledError):
            await pump_task
