"""运行时模型读取数据库系统配置，不能读取过期进程默认值。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from yuxi.agentscope import config_projection as projection
from yuxi.agentscope import tools
from yuxi.config import UserConfig, config
from yuxi.config.options import Option
from yuxi.models.providers.cache import model_cache
from yuxi.services.department_context_service import DepartmentContext


def _department_context() -> DepartmentContext:
    """构造投影测试用的固定部门上下文。"""
    return DepartmentContext(
        id=1,
        uid="u",
        username="u",
        account_role="user",
        department_id=11,
        department_name="投影部门",
        role="user",
        session_id=None,
        revision=0,
    )


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
    monkeypatch.setattr(projection, "_load_user", AsyncMock(return_value=SimpleNamespace(id=1)))
    monkeypatch.setattr(projection, "_load_agent", AsyncMock(return_value=agent))
    monkeypatch.setattr(projection, "resolve_department_context", AsyncMock(return_value=_department_context()))
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
    result = await projection.project_runtime(db, uid="u", agent_slug="test", model_spec=requested, department_id=11)
    assert result.model_spec == expected
    assert result.memory_chat_model_config["model_config"]["model"] == "fast"
    assert result.memory_embedding_model_config["model"] == "embed"
    assert result.memory_embedding_model_config["dimensions"] == 4096
    assert all(call.args[1] == "saved" for call in load_provider.await_args_list)
    get_options.assert_awaited_once_with(db)


@pytest.mark.asyncio
async def test_preloaded_external_dependency_is_only_exposed_through_gateway(monkeypatch, tmp_path):
    """preload 不能把 external Skill 依赖提升为可绕过 token 的顶层工具。"""
    skill_dir = tmp_path / "question-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# Question skill\n", encoding="utf-8")
    db = object()
    user = SimpleNamespace(id=1)
    agent = SimpleNamespace(
        name="test",
        config_json={
            "context": {
                "model": "saved:chat",
                "skills": ["question-skill"],
                "preload_skills": ["question-skill"],
                "tools": [],
                "mcps": [],
            }
        },
    )
    skill = SimpleNamespace(
        slug="question-skill",
        name="Question skill",
        description="Ask a question",
        source_dir=skill_dir,
        source_scope="builtin",
        skill_dependencies=[],
        tool_dependencies=["ask_user_question"],
        mcp_dependencies=[],
    )
    provider = SimpleNamespace(
        provider_type="openai",
        provider_id="saved",
        api_key="test-key",
        api_key_env=None,
        base_url="https://example.test/v1",
        embedding_base_url=None,
        headers_json={},
        extra_json={},
        enabled_models=[{"id": "chat", "type": "chat"}],
    )
    monkeypatch.setattr(projection, "_load_user", AsyncMock(return_value=user))
    monkeypatch.setattr(projection, "_load_agent", AsyncMock(return_value=agent))
    monkeypatch.setattr(projection, "resolve_department_context", AsyncMock(return_value=_department_context()))
    monkeypatch.setattr(Option, "get", AsyncMock(return_value={"default_model": "saved:chat"}))
    monkeypatch.setattr(model_cache, "canonicalize_spec", lambda spec: spec)
    monkeypatch.setattr(
        UserConfig, "load", AsyncMock(return_value=SimpleNamespace(schema=SimpleNamespace(enable_memory=False)))
    )
    monkeypatch.setattr(projection, "_load_provider", AsyncMock(return_value=provider))
    monkeypatch.setattr(projection, "list_accessible_skills", AsyncMock(return_value=[skill]))
    monkeypatch.setattr(
        projection,
        "get_tool_metadata",
        lambda: [{"slug": "ask_user_question"}],
    )
    monkeypatch.setattr("yuxi.agents.mcp.service.load_enabled_mcp_server_configs", AsyncMock(return_value={}))
    monkeypatch.setattr(projection.AgentRepository, "list_visible_subagents", AsyncMock(return_value=[]))
    runtime = await projection.project_runtime(db, uid="u", agent_slug="test", department_id=11)

    monkeypatch.setattr(tools, "_ensure_kb_manager_ready", AsyncMock(return_value=False))
    monkeypatch.setattr(tools, "build_mcp_tools", AsyncMock(return_value=[]))
    workspace = SimpleNamespace(
        list_skills=AsyncMock(return_value=[SimpleNamespace(name="question-skill", markdown="# Question skill")])
    )
    top_level = await tools.build_extra_tools(
        uid="u",
        knowledge_slugs=[],
        agent_id="a",
        session_id="s",
        tool_slugs=runtime.tool_slugs,
        workspace=workspace,
    )
    gateways = await tools.build_skill_dependency_gateway(
        projection=runtime,
        workspace=workspace,
        uid="u",
        agent_id="a",
        session_id="s",
        user=user,
    )

    assert [item.name for item in top_level] == []
    assert "ask_user_question" not in {item.name for item in top_level}
    assert {item.name for item in gateways} == {"Skill", "skill_external_dependency_gateway"}
