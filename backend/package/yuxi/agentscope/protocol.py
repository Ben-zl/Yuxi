"""agentscope 事件流 → yuxi 前端 chunk 协议的转换（迁移工单 04）。

纯函数集合：输入 agentscope 事件（dict，type 为大写枚举名），输出前端
`useAgentStreamHandler` 消费的 chunk dict。契约要点：
- 增量经 status=loading + msg(AIMessageChunk) 携带；思考块用
  reasoning_content 字段区分于 content 文本；
- 终态映射：REPLY_END.finished_reason → run 状态 + finished/error/interrupted chunk。
"""

import json
from dataclasses import dataclass

ASSISTANT_MSG_TYPE = "AIMessageChunk"


def sanitize_persisted_value(value):
    """递归替换 PostgreSQL text/jsonb 不接受的 NUL 字符。"""
    if isinstance(value, str):
        return value.replace("\x00", "\N{REPLACEMENT CHARACTER}")
    if isinstance(value, dict):
        return {sanitize_persisted_value(key): sanitize_persisted_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize_persisted_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(sanitize_persisted_value(item) for item in value)
    return value


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
        # 工具审批挂起：前端 useApproval 从 chunk.approval 下读取
        # action_requests，并要求 review_configs 与其等长（旧栈契约）。
        # fork 的 ToolCallBlock.input 是原始 JSON 字符串，需解析为 dict。
        action_requests = [
            {
                "action": call.get("name", ""),
                "args": json.loads(call.get("input") or "{}"),
            }
            for call in event.get("tool_calls") or []
        ]
        return [
            make_chunk(
                request_id,
                status="human_approval_required",
                approval={
                    "action_requests": action_requests,
                    "review_configs": [{} for _ in action_requests],
                },
            )
        ]
    if event_type == "REQUIRE_EXTERNAL_EXECUTION":
        tool_calls = event.get("tool_calls") or []
        questions = []
        for call in tool_calls:
            if not isinstance(call, dict):
                continue
            try:
                payload = json.loads(call.get("input") or "{}")
            except (TypeError, ValueError):
                payload = {}
            arguments = payload.get("arguments") if isinstance(payload, dict) else None
            question_payload = arguments if isinstance(arguments, dict) else payload
            if isinstance(question_payload.get("questions"), list):
                questions.extend(question_payload["questions"])
        if not questions:
            raise ValueError("外部问答事件缺少有效 questions")
        return [
            make_chunk(
                request_id,
                status="ask_user_question_required",
                questions=questions,
                source=(
                    _external_source(tool_calls[0])
                    if tool_calls and isinstance(tool_calls[0], dict)
                    else "external_execution"
                ),
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
    if event_type == "CUSTOM" and event.get("name") == "context_compression":
        value = event.get("value") if isinstance(event.get("value"), dict) else {}
        return [
            make_chunk(
                request_id,
                status="context_compression",
                compression={"type": "yuxi.context_compression", **value},
            )
        ]
    return []


def _external_source(tool_call: dict) -> str:
    """Gateway 外部调用展示真实依赖名，普通外部工具保持原名。"""
    try:
        payload = json.loads(tool_call.get("input") or "{}")
    except (TypeError, ValueError):
        payload = {}
    return str(payload.get("tool_name") or tool_call.get("name") or "external_execution")


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
        self._reply_ids: dict[str, str] = {}
        self._tool_indexes: dict[str, int] = {}
        self._result_states: dict[str, str] = {}
        self._result_metadata: dict[str, dict] = {}

    def feed(self, event: dict) -> list[dict]:
        event_type = str(event.get("type", "")).upper()
        tool_call_id = event.get("tool_call_id")

        if event_type == "TOOL_CALL_START":
            name = event.get("tool_call_name", "")
            self._tool_names[tool_call_id] = name
            self._reply_ids[tool_call_id] = str(event.get("reply_id") or "")
            self._tool_indexes.setdefault(tool_call_id, len(self._tool_indexes))
            self._args_fragments[tool_call_id] = []
            return [self._tool_call_make_chunk(tool_call_id, args="")]
        if event_type == "TOOL_RESULT_START":
            self._tool_names.setdefault(tool_call_id, event.get("tool_call_name", ""))
            self._reply_ids.setdefault(tool_call_id, str(event.get("reply_id") or ""))
            self._tool_indexes.setdefault(tool_call_id, len(self._tool_indexes))
            return []
        if event_type == "TOOL_CALL_DELTA":
            fragment = event.get("delta", "")
            self._args_fragments.setdefault(tool_call_id, []).append(fragment)
            return [self._tool_call_make_chunk(tool_call_id, args=fragment)]
        if event_type == "TOOL_CALL_END":
            # 完整参数 chunk：前端按 tool_call 类型消费完整 args 字符串
            complete_args = "".join(self._args_fragments.get(tool_call_id, []))
            return [self._tool_call_make_chunk(tool_call_id, args=complete_args, complete=True)]
        if event_type == "TOOL_RESULT_TEXT_DELTA":
            self._result_fragments.setdefault(tool_call_id, []).append(event.get("delta", ""))
            return []
        if event_type == "TOOL_RESULT_END":
            self._result_states[tool_call_id] = str(event.get("state") or "success").lower()
            self._result_metadata[tool_call_id] = dict(event.get("metadata") or {})
            output_text = "".join(self._result_fragments.get(tool_call_id, []))
            return [self._tool_finished_make_chunk(tool_call_id, output_text)]
        return []

    def seed_tool_calls(self, tool_calls: list[dict], *, reply_id: str) -> None:
        """用审批事件中的完整工具参数初始化恢复轮次。"""
        for call in tool_calls:
            if not isinstance(call, dict) or not call.get("id"):
                continue
            tool_call_id = str(call["id"])
            self._tool_names.setdefault(tool_call_id, str(call.get("name") or ""))
            self._reply_ids.setdefault(tool_call_id, reply_id)
            self._tool_indexes.setdefault(tool_call_id, len(self._tool_indexes))
            raw_args = call.get("input")
            if not isinstance(raw_args, str):
                raw_args = json.dumps(raw_args or {}, ensure_ascii=False)
            self._args_fragments.setdefault(tool_call_id, [raw_args])

    def history_tool_calls(self) -> list[dict]:
        """返回当前轮次可直接写入 Yuxi 历史表的工具调用。"""
        calls = []
        for tool_call_id in self._tool_indexes:
            raw_args = "".join(self._args_fragments.get(tool_call_id, []))
            try:
                parsed_args = json.loads(raw_args) if raw_args else {}
            except (TypeError, ValueError):
                parsed_args = {"raw": raw_args}
            if not isinstance(parsed_args, dict):
                parsed_args = {"value": parsed_args}

            state = self._result_states.get(tool_call_id, "pending")
            metadata = self._result_metadata.get(tool_call_id, {})
            error_message = None
            if state != "success":
                error_message = metadata.get("error_message") or metadata.get("message")
            calls.append(
                sanitize_persisted_value(
                    {
                        "id": tool_call_id,
                        "name": self._tool_names.get(tool_call_id, "") or "unknown",
                        "args": parsed_args,
                        "output": "".join(self._result_fragments.get(tool_call_id, [])),
                        "status": state,
                        "error_message": error_message,
                        "metadata": metadata,
                    }
                )
            )
        return calls

    def _tool_call_make_chunk(self, tool_call_id: str, *, args: str, complete: bool = False) -> dict:
        name = self._tool_names.get(tool_call_id, "")
        fragment = {
            "index": self._tool_indexes.get(tool_call_id, 0),
            "id": tool_call_id,
            "name": name,
            "args": args,
        }
        msg = {
            "id": self._reply_ids.get(tool_call_id) or None,
            "type": ASSISTANT_MSG_TYPE,
            "content": "",
            "tool_call_chunks": [fragment],
        }
        if complete:
            # 完整调用：args 为完整 JSON 字符串（前端 tool_call 类型消费）
            msg["tool_calls"] = [fragment]
        return make_chunk(self._request_id, status="loading", msg=msg)

    def _tool_finished_make_chunk(self, tool_call_id: str, output_text: str) -> dict:
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
