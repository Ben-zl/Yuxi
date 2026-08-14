"""OpenAI 兼容的流式 mock 模型服务（e2e 专用）。

以独立容器运行在 compose 网络内，为 agentscope 链路提供真实 HTTP 模型端点：
POST /v1/chat/completions 支持 stream 与非 stream 两种响应，
回复文本确定性生成，便于断言。
"""

import asyncio
import time

from fastapi import FastAPI
from fastapi.responses import StreamingResponse

app = FastAPI(title="openai-mock")

REPLY_TEXT = "你好，我是 e2e mock 模型。这条回复经由 agentscope 全链路返回。"


def _sse(payload: dict) -> str:
    import json

    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/v1/chat/completions")
async def chat_completions(body: dict):
    model = body.get("model", "mock-chat-model")
    if body.get("stream"):

        async def stream_chunks():
            chunks = [REPLY_TEXT[i : i + 6] for i in range(0, len(REPLY_TEXT), 6)]
            for index, piece in enumerate(chunks):
                yield _sse(
                    {
                        "id": "chatcmpl-mock",
                        "object": "chat.completion.chunk",
                        "created": int(time.time()),
                        "model": model,
                        "choices": [{"index": 0, "delta": {"content": piece}}],
                    }
                )
                await asyncio.sleep(0.01)
            yield _sse(
                {
                    "id": "chatcmpl-mock",
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": model,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                }
            )
            yield "data: [DONE]\n\n"

        return StreamingResponse(stream_chunks(), media_type="text/event-stream")

    return {
        "id": "chatcmpl-mock",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": REPLY_TEXT}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
    }
