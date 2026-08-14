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


def _wants_tool_call(body: dict) -> bool:
    """任一用户消息要求写文件，且尚无工具结果（否则是第二轮总结）。

    注意 agentscope 会把 workspace 提示以 <system-reminder> 追加为
    额外的 user 消息，不能只看最后一条。
    """
    messages = body.get("messages") or []
    has_tool_result = any(m.get("role") == "tool" for m in messages)
    user_texts = [_message_text(m) for m in messages if m.get("role") == "user"]
    return any("写文件" in text for text in user_texts) and not has_tool_result


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

    if body.get("stream"):
        tool_round = _wants_tool_call(body)

        async def stream_chunks():
            if tool_round:
                args = json.dumps(
                    {"file_path": WRITE_FILE_PATH, "content": WRITE_FILE_CONTENT},
                    ensure_ascii=False,
                )
                yield _sse_chunk(model, {"role": "assistant", "content": None})
                yield _sse_chunk(
                    model,
                    {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_mock_write",
                                "type": "function",
                                "function": {"name": "Write", "arguments": args},
                            }
                        ]
                    },
                )
                yield _sse_chunk(model, {}, finish_reason="tool_calls")
            else:
                text = TOOL_REPLY_TEXT if _has_tool_result(body) else REPLY_TEXT
                chunks = [text[i : i + 6] for i in range(0, len(text), 6)]
                for piece in chunks:
                    yield _sse_chunk(model, {"content": piece})
                    await asyncio.sleep(0.01)
                yield _sse_chunk(model, {}, finish_reason="stop")
            yield "data: [DONE]\n\n"

        return StreamingResponse(stream_chunks(), media_type="text/event-stream")

    if _wants_tool_call(body):
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
                                "id": "call_mock_write",
                                "type": "function",
                                "function": {
                                    "name": "Write",
                                    "arguments": json.dumps(
                                        {
                                            "file_path": WRITE_FILE_PATH,
                                            "content": WRITE_FILE_CONTENT,
                                        },
                                        ensure_ascii=False,
                                    ),
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
