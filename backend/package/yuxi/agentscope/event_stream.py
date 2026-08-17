"""AgentScope 事件订阅任务的统一创建与清理。"""

import asyncio

from yuxi.agentscope.client import AgentScopeServiceClient


# 事件流读取超时（秒）：无新事件超过该时长视为会话停滞，显式失败。
READ_TIMEOUT_SECONDS = 180.0


def start_event_pump(
    client: AgentScopeServiceClient,
    *,
    uid: str,
    agent_id: str,
    session_id: str,
    read_timeout: float,
) -> tuple[asyncio.Queue, asyncio.Task]:
    """启动事件订阅并把流异常送入同一队列，交由消费方显式处理。"""
    queue: asyncio.Queue = asyncio.Queue()

    async def pump() -> None:
        try:
            async for event in client.stream_events(uid, agent_id, session_id, read_timeout=read_timeout):
                await queue.put(event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 流异常必须送回消费方
            await queue.put(exc)

    return queue, asyncio.create_task(pump())


async def cancel_tasks(*tasks: asyncio.Task) -> None:
    """取消并等待后台任务退出，确保 HTTP 流上下文正常释放。"""
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
