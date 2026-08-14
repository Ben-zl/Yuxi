"""agentscope 事件流 → yuxi 前端 chunk 协议的转换（迁移工单 04）。

纯函数集合：输入 agentscope 事件（dict，type 为大写枚举名），输出前端
`useAgentStreamHandler` 消费的 chunk dict。契约要点：
- 增量经 status=loading + msg(AIMessageChunk) 携带；思考块用
  reasoning_content 字段区分于 content 文本；
- 终态映射：REPLY_END.finished_reason → run 状态 + finished/error/interrupted chunk。
"""

from dataclasses import dataclass

ASSISTANT_MSG_TYPE = "AIMessageChunk"


def make_chunk(request_id: str, **kwargs) -> dict:
    """构造与现有 chat_service.make_chunk 等价的 chunk dict。"""
    return {"request_id": request_id, "response": None, "thread_id": None, **kwargs}


def init_chunk(request_id: str, *, text: str, message_type: str = "text") -> dict:
    """用户消息的 init chunk（请求受理时首先发出）。"""
    return make_chunk(
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
            make_chunk(
                request_id,
                status="loading",
                msg={"id": reply_id, "type": ASSISTANT_MSG_TYPE, "content": ""},
            )
        ]
    if event_type == "REQUIRE_USER_CONFIRM":
        # 工具审批挂起：前端按 action_requests 渲染审批卡片（与旧栈一致）
        action_requests = [
            {
                "action": call.get("name", ""),
                "args": call.get("inputs") or {},
            }
            for call in event.get("tool_calls") or []
        ]
        return [
            make_chunk(
                request_id,
                status="human_approval_required",
                action_requests=action_requests,
            )
        ]
    if event_type in {"TEXT_BLOCK_DELTA", "THINKING_BLOCK_DELTA"}:
        # 文本与思考增量同构：思考块走 reasoning_content 字段区分
        msg = {"id": reply_id, "type": ASSISTANT_MSG_TYPE, "content": ""}
        if event_type == "TEXT_BLOCK_DELTA":
            msg["content"] = event.get("delta", "")
        else:
            msg["reasoning_content"] = event.get("delta", "")
        return [make_chunk(request_id, status="loading", msg=msg)]
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
            chunk=make_chunk(
                request_id,
                status="error",
                error_type=reason,
                error_message=error_message or f"运行失败: {reason}",
            ),
        )
    if reason == "interrupted":
        return TerminalConversion(
            run_status="interrupted",
            chunk=make_chunk(request_id, status="interrupted", message="对话已中断"),
        )
    return TerminalConversion(
        run_status="completed",
        chunk=make_chunk(request_id, status="finished"),
    )


class ToolEventConverter:
    """工具事件 → 前端工具 chunk 的有状态转换器（按 tool_call_id 累积）。

    覆盖工具调用增量（tool_call_chunks）与工具结果完成
    （stream_event.tool-finished，与既有 run_worker 事件形状一致）。
    每轮对话使用一个实例；feed 返回 0..n 个 chunk。
    """

    def __init__(self, request_id: str):
        self._request_id = request_id
        self._args_fragments: dict[str, list[str]] = {}
        self._result_fragments: dict[str, list[str]] = {}
        self._tool_names: dict[str, str] = {}

    def feed(self, event: dict) -> list[dict]:
        event_type = str(event.get("type", "")).upper()
        tool_call_id = event.get("tool_call_id")

        if event_type == "TOOL_CALL_START":
            name = event.get("tool_call_name", "")
            self._tool_names[tool_call_id] = name
            self._args_fragments[tool_call_id] = []
            return [self._tool_callmake_chunk(tool_call_id, args="")]
        if event_type == "TOOL_CALL_DELTA":
            fragment = event.get("delta", "")
            self._args_fragments.setdefault(tool_call_id, []).append(fragment)
            return [self._tool_callmake_chunk(tool_call_id, args=fragment)]
        if event_type == "TOOL_CALL_END":
            # 完整参数 chunk：前端按 tool_call 类型消费完整 args 字符串
            complete_args = "".join(self._args_fragments.get(tool_call_id, []))
            return [self._tool_callmake_chunk(tool_call_id, args=complete_args, complete=True)]
        if event_type == "TOOL_RESULT_TEXT_DELTA":
            self._result_fragments.setdefault(tool_call_id, []).append(event.get("delta", ""))
            return []
        if event_type == "TOOL_RESULT_END":
            output_text = "".join(self._result_fragments.get(tool_call_id, []))
            return [self._tool_finishedmake_chunk(tool_call_id, output_text)]
        return []

    def _tool_callmake_chunk(self, tool_call_id: str, *, args: str, complete: bool = False) -> dict:
        name = self._tool_names.get(tool_call_id, "")
        fragment = {
            "index": 0,
            "id": tool_call_id,
            "name": name,
            "args": args,
        }
        msg = {
            "id": None,
            "type": ASSISTANT_MSG_TYPE,
            "content": "",
            "tool_call_chunks": [fragment],
        }
        if complete:
            # 完整调用：args 为完整 JSON 字符串（前端 tool_call 类型消费）
            msg["tool_calls"] = [fragment]
        return make_chunk(self._request_id, status="loading", msg=msg)

    def _tool_finishedmake_chunk(self, tool_call_id: str, output_text: str) -> dict:
        return make_chunk(
            self._request_id,
            status="stream_event",
            event={
                "method": "tools",
                "data": {
                    "event": "tool-finished",
                    "output": {
                        "tool_call_id": tool_call_id,
                        "content": output_text,
                    },
                    "tool_call_id": tool_call_id,
                },
            },
        )
