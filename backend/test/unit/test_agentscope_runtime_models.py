"""Yuxi Credential/ChatModel Adapter 单测。"""

import subprocess
import sys

import pytest

from agentscope.credential import CredentialFactory
from agentscope.model import OpenAIChatModel
from yuxi.agentscope.runtime_models import YuxiOpenAIChatModel, register_yuxi_credentials
from yuxi.agentscope.projection import project_chat_model

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
