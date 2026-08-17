"""agentscope 执行器：按运行事实执行一轮对话并回写终态（迁移工单 05）。

执行链：映射保障（Thread↔Session）→ 网关协议转换写入 run 事件流 →
AgentRun 终态回写。请求提交与排队互斥由既有 intake/队列服务完成
（运行事实先于派发落库），本模块只负责派发后的执行与终态。
"""

from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.agentscope.client import AgentScopeServiceClient
from yuxi.agentscope.event_stream import READ_TIMEOUT_SECONDS
from yuxi.agentscope.gateway import GatewayRoundResult, stream_round_to_run_events
from yuxi.agentscope.runner import ensure_thread_session
from yuxi.repositories.agent_run_repository import AgentRunRepository
from yuxi.storage.postgres.models_business import AgentRun


async def execute_run(
    db: AsyncSession,
    client: AgentScopeServiceClient,
    *,
    run: AgentRun,
    text: str,
    read_timeout: float = READ_TIMEOUT_SECONDS,
    model_spec: str | None = None,
    image_content: str | None = None,
) -> GatewayRoundResult:
    """执行一个已派发的 Run：事件写入 Redis Stream，终态回写 AgentRun。"""
    mapping = await ensure_thread_session(
        db,
        client,
        uid=run.uid,
        thread_id=run.conversation_thread_id,
        agent_slug=run.agent_slug,
        model_spec=model_spec,
    )
    result = await stream_round_to_run_events(
        client,
        uid=run.uid,
        agent_id=mapping.agentscope_agent_id,
        session_id=mapping.agentscope_session_id,
        text=text,
        run_id=run.id,
        request_id=run.request_id,
        thread_id=run.conversation_thread_id,
        read_timeout=read_timeout,
        image_content=image_content,
    )
    await finalize_run(db, run, result)
    return result


def _terminal_error_message(result: GatewayRoundResult) -> str | None:
    """终态错误文案：审批挂起/中断/失败分别给出可区分的语义。"""
    if result.parked == "permission":
        return "等待工具审批"
    if result.run_status in {"completed", "cancelled"}:
        return None
    if result.run_status == "interrupted":
        return "会话已中断"
    return result.error_message or "运行失败"


async def finalize_run(db: AsyncSession, run: AgentRun, result: GatewayRoundResult) -> None:
    """按运行结果写 AgentRun 终态（执行路径与 resume 路径共用）。

    存在取消信号时终态归一为 cancelled（清除信号避免残留）——取消由
    网关的取消监听器中断会话触发，REPLY_END 呈现为 interrupted。
    """
    from yuxi.services.run_queue_service import clear_cancel_signal, has_cancel_signal

    if result.run_status != "completed" and await has_cancel_signal(run.id):
        await clear_cancel_signal(run.id)
        result.run_status = "cancelled"
    await AgentRunRepository(db).set_terminal_status(
        run.id,
        status=result.run_status,
        error_message=_terminal_error_message(result),
        token_usage=result.usage or {},
    )
