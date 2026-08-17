"""OpenAI 兼容的流式 mock 模型服务（e2e 专用）。

以独立容器运行在 compose 网络内，为 agentscope 链路提供真实 HTTP 模型端点：
POST /v1/chat/completions 支持 stream 与非 stream 两种响应。
行为按最近一条用户消息驱动，回复确定性生成，便于断言：
- 消息包含「写文件」→ 先流式返回 Write 工具调用，工具结果回来后返回总结文本；
- 其余消息 → 直接返回固定文本。
"""

import asyncio
import json
import time

from fastapi import FastAPI
from fastapi.responses import StreamingResponse

app = FastAPI(title="openai-mock")

REPLY_TEXT = "你好，我是 e2e mock 模型。这条回复经由 agentscope 全链路返回。"
TOOL_REPLY_TEXT = "文件已写入沙盒。"
WRITE_FILE_PATH = "/workspace/outputs/hello.txt"
WRITE_FILE_CONTENT = "agentscope workspace 写入测试\n"


def _message_text(message: dict) -> str:
    """消息文本（OpenAI content 为字符串或内容块列表两种形态）。"""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block.get("text", "") for block in content if isinstance(block, dict)
        )
    return ""


def _last_user_text(body: dict) -> str:
    """最近一条 user 消息的文本。"""
    messages = body.get("messages") or []
    user_texts = [_message_text(m) for m in messages if m.get("role") == "user"]
    return user_texts[-1] if user_texts else ""


# 关键词 → (工具名, 参数 dict)；e2e 按用户消息文本驱动确定性工具调用
TOOL_TRIGGERS = {
    "写文件": ("Write", {"file_path": WRITE_FILE_PATH, "content": WRITE_FILE_CONTENT}),
    "列出知识库": ("list_kbs", {}),
    "调用回声": ("mcp__e2e-echo-mcp__echo", {"text": "hi-mcp"}),
    "查看技能": ("Skill", {"skill": "e2e-skill-demo"}),
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


def _team_next_call(body: dict):
    """「组建团队」编排：TeamCreate → AgentCreate(自定义模板) → 总结。"""
    called = _assistant_tool_names(body)
    if "AgentCreate" in called:
        return None  # worker 已创建，进入总结轮
    if "TeamCreate" in called:
        return (
            "AgentCreate",
            {
                "name": "worker-1",
                "description": "e2e 子智能体",
                "prompt": "完成示例任务",
                "subagent_type": "e2e-team-sub",
            },
        )
    return ("TeamCreate", {"name": "e2e-team", "description": "e2e 验证团队"})


def _matched_tool_trigger(body: dict):
    """按最后一条真实用户消息匹配工具触发；无工具结果时才触发。

    只看末条 user 消息：全量技能种子会以 hint 块注入会话（含“写文件”
    等触发词字样），扫全部历史会误触发错误工具。
    """
    messages = body.get("messages") or []
    user_texts = [
        t for t in (_message_text(m) for m in messages if m.get("role") == "user")
        if not t.startswith("<system-reminder>")
    ]
    joined = user_texts[-1] if user_texts else ""
    # 团队编排是状态机（多轮工具调用），先于一次性触发的工具结果短路
    if "组建团队" in joined:
        return _team_next_call(body)
    if any(m.get("role") == "tool" for m in messages):
        return None
    for keyword, (name, args) in TOOL_TRIGGERS.items():
        if keyword in joined:
            return name, args
    return None


def _has_tool_result(body: dict) -> bool:
    """请求中是否已带工具结果（决定回复正文还是工具总结）。"""
    return any(m.get("role") == "tool" for m in body.get("messages") or [])


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
async def chat_completions(body: dict):
    model = body.get("model", "mock-chat-model")
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
                yield _sse_chunk(model, {"role": "assistant", "content": None})
                yield _sse_chunk(
                    model,
                    {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": f"call_mock_{tool_name}",
                                "type": "function",
                                "function": {"name": tool_name, "arguments": args},
                            }
                        ]
                    },
                )
                yield _sse_chunk(model, {}, finish_reason="tool_calls")
            else:
                if "worker-1" in json.dumps(body.get("messages") or [], ensure_ascii=False) and _has_tool_result(body):
                    text = TEAM_DONE_TEXT
                else:
                    text = TOOL_REPLY_TEXT if _has_tool_result(body) else REPLY_TEXT
                chunks = [text[i : i + 6] for i in range(0, len(text), 6)]
                for piece in chunks:
                    yield _sse_chunk(model, {"content": piece})
                    await asyncio.sleep(0.01)
                yield _sse_chunk(model, {}, finish_reason="stop")
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
                "message": {"role": "assistant", "content": REPLY_TEXT},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
    }
