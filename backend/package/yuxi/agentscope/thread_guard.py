"""线程接入守卫与 steer 中断钩子（迁移工单 14 · ①⑥）。

存量线程判据（cutover 时间戳）：无 agentscope 映射且存在**切换时刻之前**
的消息 → 视为旧栈线程，只读、拒绝发送并提示开新线程，不建立空 Session。
新栈线程的消息都在切换之后，intake 先落用户消息不影响判据。
steer 钩子：排队 steer 请求后中断该线程的活跃 agentscope 会话，使运行中
的 Run 提前结束（interrupted 终态），队列随即派发 steer 消息。
"""

import json
import os
from datetime import datetime, UTC

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.agentscope.client import AgentScopeServiceClient
from yuxi.repositories.agentscope_thread_sessions import get_thread_session
from yuxi.storage.postgres.models_business import Conversation, Message
from yuxi.storage.redis.manager import get_async_redis_client
from yuxi.utils import logger

LEGACY_THREAD_MESSAGE = (
    "该线程创建于旧版本，已归档为只读。请新建线程继续对话（历史记录仍可查看）。"
)

# 审批挂起事件缓存（thread 维度，resume 时取回 reply_id/tool_calls）
PENDING_CONFIRM_KEY = "agentscope:pending_confirm:{thread_id}"
PENDING_CONFIRM_TTL_SECONDS = 86400


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


async def has_pending_confirm(thread_id: str) -> bool:
    """线程是否处于审批挂起。

    interrupted 终态有两种语义：审批挂起（有缓存键，队列/intake 保持等待）
    与 steer 中断（无缓存键，队头应立即可派发）。
    """
    redis = await get_async_redis_client()
    return await redis.exists(PENDING_CONFIRM_KEY.format(thread_id=thread_id)) > 0


def _legacy_cutoff() -> datetime | None:
    """读取切换时刻（AGENTSCOPE_LEGACY_CUTOFF，ISO 格式）；未设置视为无存量。

    Message.created_at 为 naive UTC（utc_now_naive），cutoff 统一转 naive 比较。
    """
    raw = os.getenv("AGENTSCOPE_LEGACY_CUTOFF")
    if not raw:
        return None
    cutoff = datetime.fromisoformat(raw)
    if cutoff.tzinfo is not None:
        cutoff = cutoff.astimezone(UTC).replace(tzinfo=None)
    return cutoff


async def is_legacy_thread(db: AsyncSession, *, thread_id: str) -> bool:
    """无映射且存在切换时刻之前的消息 → 存量线程。"""
    cutoff = _legacy_cutoff()
    if cutoff is None:
        return False
    result = await db.execute(
        select(Message.id)
        .join(Conversation, Message.conversation_id == Conversation.id)
        .where(Conversation.thread_id == thread_id, Message.created_at < cutoff)
        .limit(1)
    )
    return result.first() is not None


async def ensure_thread_eligible(
    db: AsyncSession, *, uid: str, thread_id: str
) -> None:
    """存量线程守卫：有旧栈历史且无映射时拒绝接入（显式失败）。"""
    mapping = await get_thread_session(db, uid=uid, thread_id=thread_id)
    if mapping is not None:
        return  # 已有映射（新栈线程），放行
    if await is_legacy_thread(db, thread_id=thread_id):
        raise ValueError(LEGACY_THREAD_MESSAGE)


async def interrupt_thread_session(
    db: AsyncSession, *, uid: str, agent_slug: str, thread_id: str
) -> bool:
    """中断线程的活跃 agentscope 会话（steer 提前结束语义）。"""
    mapping = await get_thread_session(db, uid=uid, thread_id=thread_id)
    if mapping is None:
        return False
    client = AgentScopeServiceClient(
        os.getenv("AGENTSCOPE_BASE_URL", "http://agentscope:8100")
    )
    try:
        await client.interrupt_session(
            uid, mapping.agentscope_agent_id, mapping.agentscope_session_id
        )
        return True
    except Exception as exc:  # noqa: BLE001 - steer 中断失败不阻断入队，仅记录
        logger.warning(f"steer 中断会话失败 thread={thread_id}: {exc}")
        return False
