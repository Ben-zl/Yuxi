"""agentscope 执行器：按运行事实执行一轮对话并回写终态（迁移工单 05）。

执行链：映射保障（Thread↔Session）→ 网关协议转换写入 run 事件流 →
AgentRun 终态回写。请求提交与排队互斥由既有 intake/队列服务完成
（运行事实先于派发落库），本模块只负责派发后的执行与终态。
"""

from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.agentscope.client import AgentScopeServiceClient
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
    read_timeout: float = 180.0,
) -> GatewayRoundResult:
    """执行一个已派发的 Run：事件写入 Redis Stream，终态回写 AgentRun。"""
    mapping = await ensure_thread_session(
        db,
        client,
        uid=run.uid,
        thread_id=run.conversation_thread_id,
        agent_slug=run.agent_slug,
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
    )
    await AgentRunRepository(db).set_terminal_status(
        run.id,
        status=result.run_status,
        error_message=(
            "等待工具审批" if result.parked == "permission"
            else None if result.run_status == "completed"
            else "运行失败"
        ),
        token_usage=result.usage or {},
    )
    return result
