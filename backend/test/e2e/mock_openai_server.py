"""OpenAI 兼容的流式 mock 模型服务（e2e 专用）。

以独立容器运行在 compose 网络内，为 agentscope 链路提供真实 HTTP 模型端点：
POST /v1/chat/completions 支持 stream 与非 stream 两种响应。
行为按最近一条用户消息驱动，回复确定性生成，便于断言：
- 消息包含「写文件」→ 先流式返回 Write 工具调用，工具结果回来后返回总结文本；
- 消息包含「编辑已有文件」→ 依次返回 Read、Edit 工具调用，再返回总结文本；
- 其余消息 → 直接返回固定文本。
"""

import asyncio
import json
import time

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse

app = FastAPI(title="openai-mock")

REPLY_TEXT = "你好，我是 e2e mock 模型。这条回复经由 agentscope 全链路返回。"
TOOL_REPLY_TEXT = "文件已写入沙盒。"
EDIT_REPLY_TEXT = "文件已完成编辑。"
WRITE_FILE_PATH = "/workspace/outputs/hello.txt"
WRITE_FILE_CONTENT = "agentscope workspace 写入测试\n"
EDIT_FILE_CONTENT = "agentscope workspace 编辑成功\n"


def _message_text(message: dict) -> str:
    """消息文本（OpenAI content 为字符串或内容块列表两种形态）。"""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(block.get("text", "") for block in content if isinstance(block, dict))
    return ""


def _last_user_text(body: dict) -> str:
    """最近一条 user 消息的文本。"""
    messages = body.get("messages") or []
    user_texts = [_message_text(m) for m in messages if m.get("role") == "user"]
    return user_texts[-1] if user_texts else ""


# 关键词 → (工具名, 参数 dict)；e2e 按用户消息文本驱动确定性工具调用
TOOL_TRIGGERS = {
    "SLOW_TOOL": ("Bash", {"command": "sleep 12 && echo TOOL_FINISHED"}),
    "写文件": ("Write", {"file_path": WRITE_FILE_PATH, "content": WRITE_FILE_CONTENT}),
    "列出知识库": ("list_kbs", {}),
    "调用回声": ("mcp__e2e-echo-mcp__echo", {"text": "hi-mcp"}),
    "查看技能": ("Skill", {"skill": "e2e-skill-demo"}),
    "需要确认方案": (
        "ask_user_question",
        {
            "questions": [
                {
                    "header": "交付方式",
                    "question": "希望采用哪种交付方式？",
                    "options": [
                        {"label": "一次交付", "description": "完成后一次性交付全部结果"},
                        {"label": "分步交付", "description": "按阶段交付并逐步确认"},
                    ],
                }
            ]
        },
    ),
}

TEAM_DONE_TEXT = "团队任务完成"


def _assistant_tool_names(body: dict) -> set[str]:
    """历史消息中助手已发起过的工具调用名。"""
    names = set()
    for m in body.get("messages") or []:
        if m.get("role") != "assistant":
            continue
        calls = m.get("tool_calls") or []
        if isinstance(calls, list):
            for call in calls:
                function = call.get("function") or {}
                if function.get("name"):
                    names.add(function["name"])
    return names


def _assistant_tool_inputs(body: dict, tool_name: str) -> list[dict]:
    """读取历史中指定工具的完整参数。"""
    inputs = []
    for message in body.get("messages") or []:
        if message.get("role") != "assistant":
            continue
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            if function.get("name") != tool_name:
                continue
            try:
                value = json.loads(function.get("arguments") or "{}")
            except (TypeError, ValueError):
                value = {}
            inputs.append(value if isinstance(value, dict) else {})
    return inputs


def _subagent_types(body: dict) -> list[str]:
    """读取 AgentCreate schema 中当前允许的子智能体类型。"""
    for tool in body.get("tools") or []:
        function = tool.get("function") or {}
        if function.get("name") != "AgentCreate":
            continue
        values = (
            ((function.get("parameters") or {}).get("properties") or {})
            .get(
                "subagent_type",
                {},
            )
            .get("enum")
        )
        if isinstance(values, list):
            return [str(value) for value in values]
    return []


def _team_next_call(body: dict):
    """团队编排：TeamCreate → AgentCreate → TeamDelete → 总结。"""
    called = _assistant_tool_names(body)
    context = json.dumps(body.get("messages") or [], ensure_ascii=False)
    if "worker 缺参回报验证" in context:
        subagent_types = _subagent_types(body)
        if "TeamCreate" not in called:
            return "TeamCreate", {"name": "e2e-missing-input-team", "description": "worker 缺参回报验证"}
        if "AgentCreate" not in called:
            return "AgentCreate", {
                "name": "worker-1",
                "description": "e2e 缺参子智能体",
                "prompt": "执行 worker 缺参回报验证；缺少 AUTOMATION_PROJECT_ID，必须向 leader 回报。",
                "subagent_type": subagent_types[0],
            }
        worker_reported = any(
            '<team-message from="worker-1">' in _message_text(message)
            for message in body.get("messages") or []
            if message.get("role") == "user"
        )
        if worker_reported and "TeamDelete" not in called:
            return "TeamDelete", {}
        return None
    if "双 worker 回环验证" in context:
        created = {str(item.get("name") or "") for item in _assistant_tool_inputs(body, "AgentCreate")}
        subagent_types = _subagent_types(body)
        if "TeamCreate" not in called:
            return "TeamCreate", {"name": "e2e-dual-team", "description": "双 worker 回环验证"}
        if "worker-a" not in created:
            return "AgentCreate", {
                "name": "worker-a",
                "description": "e2e worker A",
                "prompt": "完成 A 模块分析并回报 leader",
                "subagent_type": subagent_types[0],
            }
        if "worker-b" not in created:
            return "AgentCreate", {
                "name": "worker-b",
                "description": "e2e worker B",
                "prompt": "完成 B 模块分析并回报 leader",
                "subagent_type": subagent_types[1],
            }
        both_reported = all(f'<team-message from="{name}">' in context for name in ("worker-a", "worker-b"))
        if both_reported and "TeamDelete" not in called:
            return "TeamDelete", {}
        return None

    if "AgentCreate" in called:
        worker_reported = any(
            '<team-message from="worker-1">' in _message_text(message)
            for message in body.get("messages") or []
            if message.get("role") == "user"
        )
        if "TeamDelete" not in called and worker_reported:
            return "TeamDelete", {}
        return None
    if "TeamCreate" in called:
        subagent_types = _subagent_types(body)
        subagent_type = subagent_types[0] if subagent_types else "e2e-team-sub"
        return (
            "AgentCreate",
            {
                "name": "worker-1",
                "description": "e2e 子智能体",
                "prompt": "完成示例任务",
                "subagent_type": subagent_type,
            },
        )
    return ("TeamCreate", {"name": "e2e-team", "description": "e2e 验证团队"})


def _skill_gateway_next_call(body: dict):
    """Skill 依赖编排：读取 deep-research 后通过 Gateway 调用搜索。"""
    called = _assistant_tool_names(body)
    if "skill_dependency_gateway" in called:
        return None
    if "Skill" in called:
        return (
            "skill_dependency_gateway",
            {
                "skill": "deep-research",
                "tool_name": "web_search",
                "arguments": {"query": "AgentScope"},
            },
        )
    return ("Skill", {"skill": "deep-research"})


def _edit_existing_file_next_call(body: dict):
    """已有文件编辑：Read → Edit → 总结。"""
    called = _assistant_tool_names(body)
    if "Edit" in called:
        return None
    if "Read" in called:
        return (
            "Edit",
            {
                "file_path": WRITE_FILE_PATH,
                "old_string": WRITE_FILE_CONTENT,
                "new_string": EDIT_FILE_CONTENT,
            },
        )
    return "Read", {"file_path": WRITE_FILE_PATH}


def _matched_tool_trigger(body: dict):
    """按最后一条真实用户消息匹配工具触发；无工具结果时才触发。

    只看末条 user 消息：全量技能种子会以 hint 块注入会话（含“写文件”
    等触发词字样），扫全部历史会误触发错误工具。
    """
    messages = body.get("messages") or []
    user_texts = [
        t
        for t in (_message_text(m) for m in messages if m.get("role") == "user")
        if not t.startswith("<system-reminder>")
    ]
    joined = user_texts[-1] if user_texts else ""
    tool_names = {str((tool.get("function") or {}).get("name") or "") for tool in body.get("tools") or []}
    if "generate_structured_output" in tool_names:
        return (
            "generate_structured_output",
            {
                "task_overview": "用户要求处理长上下文并继续完成任务。",
                "current_state": "已读取用户提供的背景资料，尚未输出最终答复。",
                "important_discoveries": "上下文达到压缩阈值，需要保留任务目标。",
                "next_steps": "基于压缩摘要给出简短确认。",
                "context_to_preserve": "使用中文回答。",
            },
        )
    if "<team-message" in joined and "TeamSay" in tool_names and "AgentCreate" not in tool_names:
        if "TeamSay" not in _assistant_tool_names(body):
            if "worker 缺参回报验证" in joined:
                if "ask_user_question" in tool_names:
                    return TOOL_TRIGGERS["需要确认方案"]
                return "TeamSay", {
                    "content": "缺少 AUTOMATION_PROJECT_ID，无法继续执行。",
                    "to": None,
                }
            return "TeamSay", {"content": "worker 已完成调研并给出三点结论", "to": None}
        return None
    # 团队编排是状态机（多轮工具调用），先于一次性触发的工具结果短路
    if (
        "组建团队" in joined
        or "简短调研" in joined
        or "双 worker 回环验证" in joined
        or "worker 缺参回报验证" in joined
    ):
        return _team_next_call(body)
    if "调用技能依赖" in joined:
        return _skill_gateway_next_call(body)
    if "编辑已有文件" in joined:
        return _edit_existing_file_next_call(body)
    if any(m.get("role") == "tool" for m in messages):
        return None
    for keyword, (name, args) in TOOL_TRIGGERS.items():
        if keyword in joined:
            return name, args
    return None


def _has_tool_result(body: dict) -> bool:
    """请求中是否已带工具结果（决定回复正文还是工具总结）。"""
    return any(m.get("role") == "tool" for m in body.get("messages") or [])


def _reply_text(body: dict) -> str:
    """按 E2E 场景返回可断言的最终正文。"""
    if "编辑已有文件" in _last_user_text(body) and _has_tool_result(body):
        return EDIT_REPLY_TEXT
    if "STEER" in _last_user_text(body):
        context = json.dumps(body.get("messages") or [], ensure_ascii=False)
        suffix = "TOOL_CONTEXT_OK" if "TOOL_FINISHED" in context else "TOOL_CONTEXT_MISSING"
        return f"STEER_COMPLETE {suffix}"
    if "worker-1" in json.dumps(body.get("messages") or [], ensure_ascii=False) and _has_tool_result(body):
        return TEAM_DONE_TEXT
    return TOOL_REPLY_TEXT if _has_tool_result(body) else REPLY_TEXT


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _sse_chunk(model: str, delta: dict, finish_reason: str | None = None) -> str:
    """流式输出一个 chat.completion.chunk 帧。"""
    return _sse(_chunk(model, delta, finish_reason))


@app.get("/health")
async def health():
    return {"status": "ok"}


def _chunk(model: str, delta: dict, finish_reason: str | None = None) -> dict:
    return {
        "id": "chatcmpl-mock",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }


@app.post("/v1/chat/completions")
async def chat_completions(body: dict, authorization: str | None = Header(None)):
    model = body.get("model", "mock-chat-model")
    if model == "snapshot-proof" and authorization != "Bearer snapshot-fixture-key":
        raise HTTPException(status_code=401, detail="snapshot credential mismatch")
    import logging

    logging.getLogger("mock").warning(
        "REQ roles=%s last_user=%r tools=%s",
        [m.get("role") for m in body.get("messages") or []],
        _last_user_text(body)[:60],
        [t.get("function", {}).get("name") for t in body.get("tools") or []],
    )
    logging.getLogger("mock").warning(
        "DECISION trigger=%r tool_names=%r",
        _matched_tool_trigger(body),
        sorted(_assistant_tool_names(body)),
    )

    if body.get("stream"):
        trigger = _matched_tool_trigger(body)

        async def stream_chunks():
            if trigger:
                tool_name, tool_args = trigger
                args = json.dumps(tool_args, ensure_ascii=False)
                call_suffix = ""
                if tool_name == "AgentCreate":
                    call_suffix = f"_{tool_args.get('name', 'worker')}"
                yield _sse_chunk(model, {"role": "assistant", "content": None})
                yield _sse_chunk(
                    model,
                    {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": f"call_mock_{tool_name}{call_suffix}",
                                "type": "function",
                                "function": {"name": tool_name, "arguments": args},
                            }
                        ]
                    },
                )
                final_chunk = _chunk(model, {}, finish_reason="tool_calls")
                final_chunk["usage"] = {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}
                yield _sse(final_chunk)
            else:
                text = _reply_text(body)
                chunks = [text[i : i + 6] for i in range(0, len(text), 6)]
                for piece in chunks:
                    yield _sse_chunk(model, {"content": piece})
                    await asyncio.sleep(0.01)
                final_chunk = _chunk(model, {}, finish_reason="stop")
                final_chunk["usage"] = {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}
                yield _sse(final_chunk)
            yield "data: [DONE]\n\n"

        return StreamingResponse(stream_chunks(), media_type="text/event-stream")

    trigger = _matched_tool_trigger(body)
    if trigger:
        return {
            "id": "chatcmpl-mock",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": f"call_mock_{trigger[0]}",
                                "type": "function",
                                "function": {
                                    "name": trigger[0],
                                    "arguments": json.dumps(trigger[1], ensure_ascii=False),
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        }

    return {
        "id": "chatcmpl-mock",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": _reply_text(body)},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
    }
