"""agentscope 执行器：按运行事实执行一轮对话并回写终态（迁移工单 05）。

执行链：映射保障（Thread↔Session）→ 网关协议转换写入 run 事件流 →
AgentRun 终态回写。请求提交与排队互斥由既有 intake/队列服务完成
（运行事实先于派发落库），本模块只负责派发后的执行与终态。
"""

from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.agentscope.client import AgentScopeServiceClient
from yuxi.agentscope.event_stream import READ_TIMEOUT_SECONDS
from yuxi.agentscope.gateway import GatewayRoundResult, stream_round_to_run_events
from yuxi.agentscope.protocol import split_embedded_reasoning
from yuxi.agentscope.runner import ensure_thread_session, recover_untracked_pending_session
from yuxi.repositories.agentscope_thread_sessions import AgentScopeThreadSession
from yuxi.repositories.agent_run_repository import AgentRunRepository
from yuxi.repositories.conversation_repository import ConversationRepository
from yuxi.storage.postgres.models_business import AgentRun


async def has_active_child_runs(client: AgentScopeServiceClient, run_id: str, uid: str) -> bool:
    """查询活动 child，并收束已 idle 但本地终态丢失的 Team worker。"""
    from yuxi.agentscope.recovery import reconcile_active_team_child_runs

    return await reconcile_active_team_child_runs(client, parent_run_id=run_id, uid=uid)


async def execute_run(
    db: AsyncSession,
    client: AgentScopeServiceClient,
    *,
    run: AgentRun,
    text: str,
    read_timeout: float = READ_TIMEOUT_SECONDS,
    model_spec: str | None = None,
    image_content: str | None = None,
    mapping: AgentScopeThreadSession | None = None,
    persist_result: bool = True,
) -> GatewayRoundResult:
    """执行一个已派发的 Run，并按调用方需要持久化结果与终态。"""
    if mapping is None:
        mapping = await ensure_thread_session(
            db,
            client,
            uid=run.uid,
            thread_id=run.conversation_thread_id,
            agent_slug=run.agent_slug,
            model_spec=model_spec,
        )
    await recover_untracked_pending_session(
        client,
        uid=run.uid,
        agent_id=mapping.agentscope_agent_id,
        session_id=mapping.agentscope_session_id,
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
        configured_model_spec=mapping.model_spec,
        has_active_child_runs=lambda: has_active_child_runs(client, run.id, run.uid),
    )
    if persist_result:
        output_message = await persist_run_output(db, run, result)
        if output_message is not None:
            await AgentRunRepository(db).set_output_message(
                run.id,
                output_message.id,
                worker_id=run.worker_id,
            )
        await finalize_run(db, run, result, worker_id=run.worker_id)
    return result


async def persist_run_output(db: AsyncSession, run: AgentRun, result: GatewayRoundResult):
    """将一个 AgentScope 回合的助手输出绑定到同一 Run。"""

    output_text, output_reasoning = split_embedded_reasoning(result.text, result.reasoning)
    if not (output_text or output_reasoning or result.tool_calls):
        return None

    output_message = await ConversationRepository(db).add_message_by_thread_id(
        thread_id=run.conversation_thread_id,
        role="assistant",
        content=output_text,
        message_type="text",
        extra_metadata={
            "request_id": run.request_id,
            "run_id": run.id,
            "token_usage": result.usage or {},
            "additional_kwargs": {"reasoning_content": output_reasoning} if output_reasoning else {},
        },
        run_id=run.id,
        request_id=run.request_id,
    )
    if output_message is None:
        raise RuntimeError("回复消息落库失败：线程不存在")

    conversation_repo = ConversationRepository(db)
    for tool_call in result.tool_calls or []:
        await conversation_repo.add_tool_call(
            message_id=output_message.id,
            tool_name=tool_call.get("name") or "unknown",
            tool_input=tool_call.get("args") or {},
            tool_output=tool_call.get("output") or "",
            status=tool_call.get("status") or "pending",
            error_message=tool_call.get("error_message"),
            langgraph_tool_call_id=tool_call.get("id"),
        )
    return output_message


def _terminal_error_message(result: GatewayRoundResult) -> str | None:
    """终态错误文案：审批挂起/中断/失败分别给出可区分的语义。"""
    if result.parked == "permission":
        return "等待工具审批"
    if result.run_status in {"completed", "cancelled"}:
        return None
    if result.run_status == "interrupted":
        return "会话已中断"
    return result.error_message or "运行失败"


async def finalize_run(
    db: AsyncSession,
    run: AgentRun,
    result: GatewayRoundResult,
    *,
    worker_id: str | None = None,
) -> None:
    """按运行结果写 AgentRun 终态（执行路径与 resume 路径共用）。

    存在取消信号时终态归一为 cancelled（清除信号避免残留）——取消由
    网关的取消监听器中断会话触发，REPLY_END 呈现为 interrupted。
    """
    from yuxi.services.run_queue_service import clear_cancel_signal, has_cancel_signal

    if result.run_status != "completed" and await has_cancel_signal(run.id):
        await clear_cancel_signal(run.id)
        result.run_status = "cancelled"
    error_type = result.error_type
    if result.parked == "permission":
        error_type = "human_approval_required"
    elif result.parked == "external":
        error_type = "external_execution_required"
    persisted_run, transitioned = await AgentRunRepository(db).set_terminal_status(
        run.id,
        status=result.run_status,
        error_type=error_type,
        error_message=_terminal_error_message(result),
        token_usage=result.usage or {},
        worker_id=worker_id,
        cancel_requested_as_cancelled=True,
    )
    if persisted_run is None or (
        not transitioned and persisted_run.status not in {"completed", "failed", "cancelled", "interrupted"}
    ):
        raise RuntimeError("Run 终态未能由当前 Worker 写入")
    result.run_status = persisted_run.status
