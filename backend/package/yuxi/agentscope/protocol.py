"""agentscope 事件流 → yuxi 前端 chunk 协议的转换（迁移工单 04）。

纯函数集合：输入 agentscope 事件（dict，type 为大写枚举名），输出前端
`useAgentStreamHandler` 消费的 chunk dict。契约要点：
- 增量经 status=loading + msg(AIMessageChunk) 携带；思考块用
  reasoning_content 字段区分于 content 文本；
- 终态映射：REPLY_END.finished_reason → run 状态 + finished/error/interrupted chunk。
"""

from dataclasses import dataclass

ASSISTANT_MSG_TYPE = "AIMessageChunk"


def _chunk(request_id: str, **kwargs) -> dict:
    """构造与现有 chat_service.make_chunk 等价的 chunk dict。"""
    return {"request_id": request_id, "response": None, "thread_id": None, **kwargs}


def init_chunk(request_id: str, *, text: str, message_type: str = "text") -> dict:
    """用户消息的 init chunk（请求受理时首先发出）。"""
    return _chunk(
        request_id,
        status="init",
        msg={
            "role": "user",
            "content": text,
            "type": "human",
            "message_type": message_type,
            "extra_metadata": {"request_id": request_id},
        },
    )


def event_to_chunks(event: dict, *, request_id: str) -> list[dict]:
    """单个 agentscope 事件 → 0..n 个 chunk；无法映射的事件返回空列表。"""
    event_type = str(event.get("type", "")).upper()
    reply_id = event.get("reply_id")

    if event_type == "REPLY_START":
        return [
            _chunk(
                request_id,
                status="loading",
                msg={"id": reply_id, "type": ASSISTANT_MSG_TYPE, "content": ""},
            )
        ]
    if event_type == "TEXT_BLOCK_DELTA":
        return [
            _chunk(
                request_id,
                status="loading",
                msg={
                    "id": reply_id,
                    "type": ASSISTANT_MSG_TYPE,
                    "content": event.get("delta", ""),
                },
            )
        ]
    if event_type == "THINKING_BLOCK_DELTA":
        return [
            _chunk(
                request_id,
                status="loading",
                msg={
                    "id": reply_id,
                    "type": ASSISTANT_MSG_TYPE,
                    "content": "",
                    "reasoning_content": event.get("delta", ""),
                },
            )
        ]
    return []


@dataclass
class TerminalConversion:
    """REPLY_END 的终态映射结果。"""

    run_status: str  # completed / failed / interrupted（Run 终态，end 事件载荷）
    chunk: dict  # 终态 chunk（finished / error / interrupted）


def reply_end_to_terminal(event: dict, *, request_id: str) -> TerminalConversion:
    """把 REPLY_END 映射为 Run 终态与终态 chunk。"""
    reason = str(event.get("finished_reason", "completed")).lower()
    error = event.get("error") or {}
    error_message = error.get("message") if isinstance(error, dict) else None

    if reason in {"error", "exceed_max_iters"}:
        return TerminalConversion(
            run_status="failed",
            chunk=_chunk(
                request_id,
                status="error",
                error_type=reason,
                error_message=error_message or f"运行失败: {reason}",
            ),
        )
    if reason == "interrupted":
        return TerminalConversion(
            run_status="interrupted",
            chunk=_chunk(request_id, status="interrupted", message="对话已中断"),
        )
    return TerminalConversion(
        run_status="completed",
        chunk=_chunk(request_id, status="finished"),
    )
