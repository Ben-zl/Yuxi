"""通用 AgentScope 模型适配器测试。"""

from types import SimpleNamespace

import pytest

from yuxi.models import chat
from yuxi.models.providers.cache import ModelInfo

pytestmark = pytest.mark.unit


def _info(provider_type: str = "openai") -> ModelInfo:
    return ModelInfo(
        provider_id="provider",
        model_id="model",
        model_type="chat",
        display_name="Model",
        api_key="secret",
        base_url="https://example.test/v1",
        provider_type=provider_type,
        headers={"X-Test": "1"},
        request_body_overrides={"enable_thinking": False},
    )


async def test_adapter_preserves_call_contract_and_normalizes_messages(monkeypatch):
    captured = {}

    class FakeResponse:
        content = [SimpleNamespace(type="text", text="hello")]

    class FakeModel:
        async def __call__(self, messages):
            captured["messages"] = messages
            return FakeResponse()

    monkeypatch.setattr(chat.AgentScopeChatAdapter, "_build_model", lambda self, stream: FakeModel())
    adapter = chat.AgentScopeChatAdapter(_info(), model_params={"temperature": 0.2})

    response = await adapter.call([{"role": "user", "content": "Say hello"}])

    assert response.content == "hello"
    assert response.is_full is False
    assert captured["messages"][0].role == "user"
    assert captured["messages"][0].get_text_content() == "Say hello"


def test_select_model_rejects_unknown_or_non_chat_model(monkeypatch):
    monkeypatch.setattr(chat.model_cache, "get_model_info", lambda _spec: None)
    monkeypatch.setattr(chat.model_cache, "get_all_specs", lambda _kind: [])
    with pytest.raises(ValueError, match="未找到模型"):
        chat.select_model("missing:model")

    monkeypatch.setattr(
        chat.model_cache,
        "get_model_info",
        lambda _spec: SimpleNamespace(model_type="embedding"),
    )
    with pytest.raises(ValueError, match="not a chat model"):
        chat.select_model("provider:model")
