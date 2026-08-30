"""Yuxi Credential/ChatModel Adapter 单测。"""

import subprocess
import sys
from types import SimpleNamespace

import pytest

from agentscope.credential import CredentialFactory
from agentscope.model import AnthropicChatModel, OpenAIChatModel
from agentscope.model._model_response import ChatResponse
from agentscope.model._model_usage import ChatUsage
from yuxi.agentscope.projection import project_chat_model
from yuxi.agentscope.runtime_models import (
    YuxiAnthropicChatModel,
    YuxiOpenAIChatModel,
    register_yuxi_credentials,
)

pytestmark = pytest.mark.unit


def test_runtime_models_imports_in_fresh_interpreter():
    """Credential 的 type 字段不能破坏冷启动时的注解求值。"""
    result = subprocess.run(
        [sys.executable, "-c", "import yuxi.agentscope.runtime_models"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_custom_credential_is_deserialized_by_public_registry():
    """扩展字段保存在 Credential，不依赖 ChatModelConfig fork 字段。"""
    register_yuxi_credentials()
    credential = CredentialFactory.from_dict(
        {
            "type": "yuxi_openai_credential",
            "api_key": "test-key",
            "base_url": "http://model.test/v1",
            "default_headers": {"X-Test": "value"},
            "request_body_overrides": {"enable_thinking": True},
            "context_size": 32768,
        }
    )

    assert credential.default_headers == {"X-Test": "value"}
    assert credential.request_body_overrides == {"enable_thinking": True}
    assert credential.context_size == 32768
    assert credential.get_chat_model_class() is YuxiOpenAIChatModel


def test_openai_adapter_forwards_credential_extensions(monkeypatch):
    """Model Adapter 将 Credential 扩展字段传给原版 OpenAI 模型。"""
    captured = {}

    def fake_init(self, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(OpenAIChatModel, "__init__", fake_init)
    register_yuxi_credentials()
    credential = CredentialFactory.from_dict(
        {
            "type": "yuxi_openai_credential",
            "api_key": "test-key",
            "default_headers": {"X-Test": "value"},
            "request_body_overrides": {"enable_thinking": True},
            "context_size": 32768,
        }
    )

    YuxiOpenAIChatModel(credential=credential, model="test-model")

    assert captured["client_kwargs"] == {"default_headers": {"X-Test": "value"}}
    assert captured["extra_body"] == {"enable_thinking": True}
    assert captured["context_size"] == 32768


def test_projection_carries_catalog_context_length_in_custom_credential():
    """模型目录上下文长度经自定义 Credential 进入原版 AgentScope。"""
    provider = type(
        "Provider",
        (),
        {
            "provider_id": "openai-test",
            "provider_type": "openai",
            "enabled_models": [
                {"id": "test-model", "type": "chat", "context_length": 65536}
            ],
            "api_key": "test-key",
            "api_key_env": None,
            "base_url": "http://model.test/v1",
            "headers_json": {},
            "extra_json": {},
        },
    )()

    credential, _model = project_chat_model(provider, "test-model")

    assert credential["context_size"] == 65536


def test_gemini_rejects_unsupported_request_body_overrides():
    """不能把 Gemini 不支持的通用 body override 静默丢弃。"""
    provider = type(
        "Provider",
        (),
        {
            "provider_id": "gemini-test",
            "provider_type": "gemini",
            "enabled_models": [
                {
                    "id": "gemini-test-model",
                    "type": "chat",
                    "request_body_overrides": {"unsupported": True},
                }
            ],
            "api_key": "test-key",
            "api_key_env": None,
            "base_url": None,
            "headers_json": {},
            "extra_json": {},
        },
    )()

    with pytest.raises(ValueError, match="只有 OpenAI-compatible"):
        project_chat_model(provider, "gemini-test-model")


async def test_anthropic_stream_merges_usage_reported_in_message_delta(monkeypatch):
    """MiniMax 在 message_delta 上报的输入与缓存用量不会丢失。"""

    async def upstream_parser(_self, _start_datetime, response):
        usage = ChatUsage(input_tokens=0, output_tokens=0, time=0.1)
        async with response as stream:
            async for event in stream:
                if event.type == "message_start":
                    yield ChatResponse(content=[], is_last=False, usage=usage)
                elif event.type == "message_delta":
                    usage.output_tokens = event.usage.output_tokens

    monkeypatch.setattr(
        AnthropicChatModel,
        "_parse_anthropic_stream_completion_response",
        upstream_parser,
    )

    class ResponseStream:
        """模拟 Anthropic SDK 返回的异步上下文管理器。"""

        entered = False
        exited = False

        async def __aenter__(self):
            self.entered = True
            return self.events()

        async def __aexit__(self, exc_type, exc_value, traceback):
            self.exited = True

        async def events(self):
            yield SimpleNamespace(type="message_start")
            yield SimpleNamespace(
                type="message_delta",
                usage=SimpleNamespace(
                    input_tokens=223,
                    output_tokens=2,
                    cache_creation_input_tokens=None,
                    cache_read_input_tokens=1024,
                ),
            )

    model = object.__new__(YuxiAnthropicChatModel)
    response_stream = ResponseStream()
    responses = [
        item
        async for item in model._parse_anthropic_stream_completion_response(
            None,
            response_stream,
        )
    ]

    assert response_stream.entered is True
    assert response_stream.exited is True
    assert len(responses) == 1
    assert responses[0].usage.input_tokens == 223
    assert responses[0].usage.output_tokens == 2
    assert responses[0].usage.cache_creation_input_tokens == 0
    assert responses[0].usage.cache_input_tokens == 1024
