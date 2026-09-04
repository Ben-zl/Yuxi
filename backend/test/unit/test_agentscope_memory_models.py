"""ReMe 使用的 AgentScope 模型适配测试。"""

from types import SimpleNamespace

import pytest

from yuxi.agentscope.memory import build_memory_models, build_scoped_reme_middleware
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


def test_scoped_reme_middleware_setup_failure_is_fail_open(monkeypatch):
    """ReMe 模型构造失败只能关闭本轮记忆，不能阻断普通对话装配。"""
    import yuxi.agentscope.memory as memory_module

    projection = SimpleNamespace(agent_slug="test-agent")
    warnings = []

    def fail_build(_projection):
        raise ValueError("Memory Chat 模型 API Key 必须是 ASCII 字符")

    monkeypatch.setattr(memory_module, "build_memory_models", fail_build)
    monkeypatch.setattr(memory_module.logger, "warning", warnings.append)

    middleware = build_scoped_reme_middleware(
        projection,
        registry=object(),
        uid="test-user",
        on_memory_updated=None,
    )

    assert middleware is None
    assert warnings == ["ReMe middleware disabled for test-user/test-agent: ValueError"]


def test_scoped_reme_middleware_constructor_failure_is_fail_open(monkeypatch):
    """中间件自身构造失败同样不能中断普通对话装配。"""
    import yuxi.agentscope.memory as memory_module

    projection = SimpleNamespace(agent_slug="test-agent")
    warnings = []

    monkeypatch.setattr(memory_module, "build_memory_models", lambda _projection: (object(), object(), "fp"))
    monkeypatch.setattr(
        memory_module,
        "ScopedReMeMiddleware",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("constructor failed")),
    )
    monkeypatch.setattr(memory_module.logger, "warning", warnings.append)

    middleware = build_scoped_reme_middleware(
        projection,
        registry=object(),
        uid="test-user",
        on_memory_updated=None,
    )

    assert middleware is None
    assert warnings == ["ReMe middleware disabled for test-user/test-agent: RuntimeError"]
