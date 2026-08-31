"""ReMe 使用的 AgentScope 模型适配测试。"""

from types import SimpleNamespace

import pytest

from yuxi.agentscope.memory import build_memory_models
from yuxi.agentscope.runtime_models import (
    YuxiOpenAICredential,
    YuxiOpenAIEmbeddingModel,
)

pytestmark = pytest.mark.unit


def test_embedding_adapter_uses_headers_endpoint_parent_and_omits_dimensions(monkeypatch):
    """兼容端点收到自定义 Header，且请求不携带 dimensions。"""
    calls = []

    class FakeClient:
        def __init__(self, **kwargs):
            calls.append(kwargs)

    monkeypatch.setattr("openai.AsyncClient", FakeClient)
    credential = YuxiOpenAICredential(
        api_key="secret",
        base_url="https://chat.example.test/v1",
        default_headers={"X-Tenant": "alpha"},
    )

    model = YuxiOpenAIEmbeddingModel(
        credential=credential,
        model="embed-v1",
        dimensions=1024,
        embedding_base_url="https://embed.example.test/v1/embeddings",
    )

    assert model.pass_dimensions is False
    assert calls[-1] == {
        "api_key": "secret",
        "base_url": "https://embed.example.test/v1",
        "default_headers": {"X-Tenant": "alpha"},
    }


def test_memory_models_reject_non_ascii_api_key():
    """HTTP Header 无法编码的占位密钥必须在 scope 启动时显式失败。"""
    projection = SimpleNamespace(
        memory_chat_model_config={
            "credential_data": {
                "type": "yuxi_openai_credential",
                "api_key": "请配置真实密钥",
            },
            "model_config": {"model": "chat-model", "parameters": {}},
        },
        memory_embedding_model_config={
            "credential_data": {
                "type": "yuxi_openai_credential",
                "api_key": "ascii-key",
            },
            "model": "embedding-model",
            "dimensions": 1024,
            "base_url": "https://example.test/v1/embeddings",
        },
    )

    with pytest.raises(ValueError, match="Chat 模型 API Key"):
        build_memory_models(projection)
