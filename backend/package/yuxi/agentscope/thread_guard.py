"""线程接入守卫与挂起事件存储（迁移工单 14）。

存量线程判据（cutover 时间戳）：无 agentscope 映射且存在**切换时刻之前**
的消息 → 视为旧栈线程，只读、拒绝发送并提示开新线程，不建立空 Session。
新栈线程的消息都在切换之后，intake 先落用户消息不影响判据。
"""

import json
import os
from datetime import datetime, UTC

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.repositories.agentscope_thread_sessions import get_thread_session
from yuxi.storage.postgres.models_business import Conversation, Message
from yuxi.storage.redis.manager import get_async_redis_client

LEGACY_THREAD_MESSAGE = "该线程创建于旧版本，已归档为只读。请新建线程继续对话（历史记录仍可查看）。"

# 审批挂起事件缓存（thread 维度，resume 时取回 reply_id/tool_calls）
PENDING_CONFIRM_KEY = "agentscope:pending_confirm:{thread_id}"
PENDING_CONFIRM_TTL_SECONDS = 86400


async def store_pending_confirm(thread_id: str, confirm_event: dict, *, run_id: str | None = None) -> None:
    """缓存审批挂起事件，供 resume 请求构造 UserConfirmResultEvent。"""
    redis = await get_async_redis_client()
    payload = dict(confirm_event)
    if run_id is not None:
        payload["_owner_run_id"] = run_id
    await redis.set(
        PENDING_CONFIRM_KEY.format(thread_id=thread_id),
        json.dumps(payload, ensure_ascii=False),
        ex=PENDING_CONFIRM_TTL_SECONDS,
    )


async def load_pending_confirm(thread_id: str) -> dict | None:
    """读取线程的挂起事件；恢复成功前保留，避免异常导致状态丢失。"""
    redis = await get_async_redis_client()
    key = PENDING_CONFIRM_KEY.format(thread_id=thread_id)
    raw = await redis.get(key)
    if not raw:
        return None
    return json.loads(raw)


async def clear_pending_confirm(
    thread_id: str,
    *,
    expected_run_id: str | None = None,
    include_legacy: bool = False,
) -> None:
    """恢复成功时清理；取消时仅原子删除属于目标 Run 的事件。"""
    redis = await get_async_redis_client()
    key = PENDING_CONFIRM_KEY.format(thread_id=thread_id)
    if expected_run_id is None:
        await redis.delete(key)
        return
    await redis.eval(
        """
        local raw = redis.call('GET', KEYS[1])
        if not raw then return 0 end
        local ok, event = pcall(cjson.decode, raw)
        if ok and type(event) == 'table' and
            (event['_owner_run_id'] == ARGV[1] or
             (ARGV[2] == '1' and event['_owner_run_id'] == nil)) then
            return redis.call('DEL', KEYS[1])
        end
        return 0
        """,
        1,
        key,
        expected_run_id,
        "1" if include_legacy else "0",
    )


async def has_pending_confirm(thread_id: str) -> bool:
    """线程是否处于审批挂起。

    interrupted 终态有两种语义：审批挂起（有缓存键，队列/intake 保持等待）
    与 steer 中断（无缓存键，队头应立即可派发）。
    """
    redis = await get_async_redis_client()
    return await redis.exists(PENDING_CONFIRM_KEY.format(thread_id=thread_id)) > 0


def _legacy_cutoff() -> datetime:
    """读取强制切换时刻（AGENTSCOPE_LEGACY_CUTOFF，ISO 格式）。

    Message.created_at 为 naive UTC（utc_now_naive），cutoff 统一转 naive 比较。
    """
    raw = os.getenv("AGENTSCOPE_LEGACY_CUTOFF")
    if not raw:
        raise RuntimeError("AGENTSCOPE_LEGACY_CUTOFF 未配置，拒绝判定线程新旧状态")
    try:
        cutoff = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise RuntimeError("AGENTSCOPE_LEGACY_CUTOFF 必须是 ISO 时间") from exc
    if cutoff.tzinfo is not None:
        cutoff = cutoff.astimezone(UTC).replace(tzinfo=None)
    return cutoff


async def is_legacy_thread(db: AsyncSession, *, thread_id: str) -> bool:
    """无映射且存在切换时刻之前的消息 → 存量线程。"""
    cutoff = _legacy_cutoff()
    result = await db.execute(
        select(Message.id)
        .join(Conversation, Message.conversation_id == Conversation.id)
        .where(Conversation.thread_id == thread_id, Message.created_at < cutoff)
        .limit(1)
    )
    return result.first() is not None


async def ensure_thread_eligible(db: AsyncSession, *, uid: str, thread_id: str) -> None:
    """存量线程守卫：有旧栈历史且无映射时拒绝接入（显式失败）。"""
    mapping = await get_thread_session(db, uid=uid, thread_id=thread_id)
    if mapping is not None:
        return  # 已有映射（新栈线程），放行
    if await is_legacy_thread(db, thread_id=thread_id):
        raise ValueError(LEGACY_THREAD_MESSAGE)
