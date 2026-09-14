"""为 assembled-path E2E 提供最小 OpenAI 兼容确定性响应。"""

from __future__ import annotations

import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

EXPECTED_OUTPUT = "DETERMINISTIC_AGENT_E2E_OK"
EXPECTED_AUTHORIZATION = "Bearer ci-replay-key"
EXPECTED_MODEL = "deterministic-chat"
EXPECTED_PRELOADED_SKILL_MARKER = "# 图片生成技能"
EXPECTED_PRELOADED_TOOL = "skill_dependency_gateway"
EXPECTED_DEPENDENCY_TOOL = "present_artifacts"
EXPECTED_TOOL_CALL_ID = "call-preloaded-tool"
EXPECTED_TOOL_RESULT_MARKER = "已将交付物展示给用户"
WRITE_TOOL_NAME = "Write"
WRITE_FILE_PATH = "/workspace/outputs/deterministic.txt"


def _validate_request(authorization: str | None, request: dict) -> str | None:
    """拒绝没有走预期模型适配契约的 replay 请求。"""

    if authorization != EXPECTED_AUTHORIZATION:
        return "invalid_authorization"
    if request.get("model") != EXPECTED_MODEL:
        return "invalid_model"
    if request.get("stream") is not True:
        return "stream_required"
    messages = request.get("messages")
    if not isinstance(messages, list) or not messages:
        return "messages_required"
    serialized_messages = json.dumps(messages, ensure_ascii=False)
    if EXPECTED_OUTPUT not in serialized_messages:
        return "expected_input_missing"
    if EXPECTED_PRELOADED_SKILL_MARKER not in serialized_messages:
        return "preloaded_skill_missing"
    tools = request.get("tools")
    tool_names = {
        item.get("function", {}).get("name")
        for item in tools or []
        if isinstance(item, dict) and isinstance(item.get("function"), dict)
    }
    if EXPECTED_PRELOADED_TOOL not in tool_names:
        return "preloaded_tool_missing"
    tool_messages = [message for message in messages if isinstance(message, dict) and message.get("role") == "tool"]
    dependency_called = any(
        function.get("name") == EXPECTED_PRELOADED_TOOL
        and _gateway_calls_expected_dependency(function.get("arguments"))
        for message in messages
        if isinstance(message, dict) and message.get("role") == "assistant"
        for call in message.get("tool_calls") or []
        if isinstance(call, dict)
        for function in [call.get("function") or {}]
    )
    called_tools = {
        function.get("name")
        for message in messages
        if isinstance(message, dict) and message.get("role") == "assistant"
        for call in message.get("tool_calls") or []
        if isinstance(call, dict)
        for function in [call.get("function") or {}]
    }
    if tool_messages and not dependency_called and WRITE_TOOL_NAME not in called_tools:
        return "tool_execution_result_missing"
    present_result = next(
        (message for message in tool_messages if message.get("tool_call_id") == EXPECTED_TOOL_CALL_ID),
        None,
    )
    present_content = str(present_result.get("content", "")) if present_result is not None else ""
    if present_result is not None and (
        EXPECTED_TOOL_RESULT_MARKER not in present_content and "filepaths" not in present_content
    ):
        return "tool_execution_result_missing"
    return None


def _gateway_calls_expected_dependency(raw_arguments: object) -> bool:
    """确认 replay 中的 Gateway 调用仍指向预期 Skill 依赖。"""
    try:
        arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else raw_arguments
    except json.JSONDecodeError:
        return False
    return (
        isinstance(arguments, dict)
        and arguments.get("skill") == "image-gen"
        and arguments.get("tool_name") == EXPECTED_DEPENDENCY_TOOL
        and arguments.get("arguments") == {"filepaths": [WRITE_FILE_PATH]}
    )


def _stream_payloads(model: str, messages: list[dict]) -> list[dict]:
    common = {
        "id": "chatcmpl-yuxi-deterministic",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
    }
    called_tools = {
        call.get("function", {}).get("name")
        for message in messages
        if isinstance(message, dict) and message.get("role") == "assistant"
        for call in message.get("tool_calls") or []
        if isinstance(call, dict)
    }
    if EXPECTED_PRELOADED_TOOL in called_tools:
        return [
            {
                **common,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant", "content": EXPECTED_OUTPUT},
                        "finish_reason": None,
                    }
                ],
            },
            {
                **common,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 8, "completion_tokens": 4, "total_tokens": 12},
            },
        ]

    if WRITE_TOOL_NAME in called_tools:
        return [
            {
                **common,
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": EXPECTED_TOOL_CALL_ID,
                                    "type": "function",
                                    "function": {
                                        "name": EXPECTED_PRELOADED_TOOL,
                                        "arguments": json.dumps(
                                            {
                                                "skill": "image-gen",
                                                "tool_name": EXPECTED_DEPENDENCY_TOOL,
                                                "arguments": {"filepaths": [WRITE_FILE_PATH]},
                                            }
                                        ),
                                    },
                                }
                            ],
                        },
                        "finish_reason": None,
                    }
                ],
            },
            {
                **common,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
                "usage": {"prompt_tokens": 8, "completion_tokens": 2, "total_tokens": 10},
            },
        ]

    return [
        {
            **common,
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "write-deterministic-file",
                                "type": "function",
                                "function": {
                                    "name": WRITE_TOOL_NAME,
                                    "arguments": json.dumps(
                                        {
                                            "file_path": WRITE_FILE_PATH,
                                            "content": f"{EXPECTED_OUTPUT}\n",
                                        }
                                    ),
                                },
                            }
                        ],
                    },
                    "finish_reason": None,
                }
            ],
        },
        {
            **common,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
            "usage": {"prompt_tokens": 8, "completion_tokens": 2, "total_tokens": 10},
        },
    ]


class ReplayHandler(BaseHTTPRequestHandler):
    """只实现测试所需的 health 与 chat completions 协议。"""

    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._write_json(200, {"status": "ok"})
            return
        self._write_json(404, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path.rstrip("/") != "/v1/chat/completions":
            self._write_json(404, {"error": "not_found"})
            return

        try:
            length = int(self.headers.get("content-length", "0"))
            request = json.loads(self.rfile.read(length) or b"{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            self._write_json(400, {"error": "invalid_json"})
            return

        request_error = _validate_request(self.headers.get("authorization"), request)
        if request_error:
            self._write_json(422, {"error": request_error})
            return

        model = str(request["model"])
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        for payload in _stream_payloads(model, request["messages"]):
            self.wfile.write(f"data: {json.dumps(payload)}\n\n".encode())
            self.wfile.flush()
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()
        self.close_connection = True

    def log_message(self, format: str, *args: object) -> None:
        return

    def _write_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", default=8765, type=int)
    args = parser.parse_args()
    ThreadingHTTPServer((args.host, args.port), ReplayHandler).serve_forever()


if __name__ == "__main__":
    main()
