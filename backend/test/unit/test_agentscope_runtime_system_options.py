"""运行时模型读取数据库系统配置，不能读取过期进程默认值。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from yuxi.agentscope import config_projection as projection
from yuxi.config import UserConfig, config
from yuxi.config.options import Option
from yuxi.models.providers.cache import model_cache


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "requested, configured, expected",
    [
        (None, None, "saved:chat"),
        (None, "saved:agent", "saved:agent"),
        ("saved:request", "saved:agent", "saved:request"),
    ],
)
async def test_runtime_uses_persisted_memory_and_default_models(monkeypatch, requested, configured, expected):
    """页面保存的默认、快速和向量模型必须形成同一运行时投影。"""
    db = object()
    agent = SimpleNamespace(name="test", config_json={"context": {"skills": [], "tools": [], "mcps": []}})
    agent.config_json["context"]["model"] = configured
    monkeypatch.setattr(projection, "_load_user", AsyncMock(return_value=object()))
    monkeypatch.setattr(projection, "_load_agent", AsyncMock(return_value=agent))
    monkeypatch.setattr(config, "default_model", "stale:chat")
    monkeypatch.setattr(config, "fast_model", "stale:fast")
    monkeypatch.setattr(config, "embed_model", "stale:embed")
    stored = {"default_model": "saved:chat", "fast_model": "saved:fast", "embed_model": "saved:embed"}
    get_options = AsyncMock(return_value=stored)
    monkeypatch.setattr(Option, "get", get_options)
    monkeypatch.setattr(model_cache, "canonicalize_spec", lambda spec: spec)
    monkeypatch.setattr(
        UserConfig, "load", AsyncMock(return_value=SimpleNamespace(schema=SimpleNamespace(enable_memory=True)))
    )
    provider = SimpleNamespace(
        provider_type="openai",
        provider_id="saved",
        api_key="test-key",
        api_key_env=None,
        base_url="https://example.test/v1",
        embedding_base_url="https://example.test/v1/embeddings",
        headers_json={},
        extra_json={},
        enabled_models=[
            {"id": "request", "type": "chat"},
            {"id": "agent", "type": "chat"},
            {"id": "chat", "type": "chat"},
            {"id": "fast", "type": "chat"},
            {"id": "embed", "type": "embedding", "dimension": 4096},
        ],
    )
    load_provider = AsyncMock(return_value=provider)
    monkeypatch.setattr(projection, "_load_provider", load_provider)
    monkeypatch.setattr(projection, "list_accessible_skills", AsyncMock(return_value=[]))
    monkeypatch.setattr(projection, "get_tool_metadata", lambda: [])
    monkeypatch.setattr("yuxi.agents.mcp.service.load_enabled_mcp_server_configs", AsyncMock(return_value={}))
    monkeypatch.setattr(projection.AgentRepository, "list_visible_subagents", AsyncMock(return_value=[]))
    result = await projection.project_runtime(db, uid="u", agent_slug="test", model_spec=requested)
    assert result.model_spec == expected
    assert result.memory_chat_model_config["model_config"]["model"] == "fast"
    assert result.memory_embedding_model_config["model"] == "embed"
    assert result.memory_embedding_model_config["dimensions"] == 4096
    assert all(call.args[1] == "saved" for call in load_provider.await_args_list)
    get_options.assert_awaited_once_with(db)
