"""知识库工具可见性与 LITE 门控单测（迁移工单 07）。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from yuxi.agentscope import tools

pytestmark = pytest.mark.unit


def _summary(kb_id: str, name: str) -> SimpleNamespace:
    return SimpleNamespace(kb_id=kb_id, name=name, description=f"{name} 描述", kb_type="milvus")


@pytest.fixture
def kb_manager(monkeypatch):
    """替换知识库管理器的用户查询，返回固定可见集。"""
    summaries = [_summary("kb-1", "产品手册"), _summary("kb-2", "内部规范")]

    async def fake_get_databases_by_uid(uid: str):
        return summaries if uid == "u1" else []

    import yuxi.knowledge.runtime as runtime

    monkeypatch.setattr(runtime.knowledge_base, "get_databases_by_uid", fake_get_databases_by_uid)
    return runtime.knowledge_base


async def test_visible_knowledge_bases_filters_by_user_and_session(kb_manager):
    visible = await tools._visible_knowledge_bases("u1", None)
    assert [kb["kb_id"] for kb in visible] == ["kb-1", "kb-2"]

    # 会话启用集按 kb_id 交集
    visible = await tools._visible_knowledge_bases("u1", ["kb-2"])
    assert [kb["kb_id"] for kb in visible] == ["kb-2"]

    # 其他用户无可见知识库
    assert await tools._visible_knowledge_bases("u2", None) == []


async def test_target_visibility_check(kb_manager):
    assert await tools._check_target_visible("u1", None, "kb-1") is None
    error = await tools._check_target_visible("u1", ["kb-2"], "kb-1")
    assert "不可见" in error
    assert "不存在" in await tools._check_target_visible("u2", None, "kb-1")


async def test_lite_mode_skips_kb_tools(monkeypatch, kb_manager):
    monkeypatch.setenv("LITE_MODE", "true")
    monkeypatch.setattr(tools, "_kb_initialized", False)
    assert await tools.build_kb_tools(uid="u1", knowledge_slugs=None) == []


async def test_build_kb_tools_returns_toolset(monkeypatch, kb_manager):
    monkeypatch.delenv("LITE_MODE", raising=False)
    monkeypatch.setattr(tools, "_kb_initialized", True)
    toolset = await tools.build_kb_tools(uid="u1", knowledge_slugs=None)
    names = {t.name for t in toolset}
    assert {"list_kbs", "query_kb", "open_kb_document"} <= names


async def test_build_mcp_tools_runs_http_client_in_service_process(monkeypatch):
    """每轮仅装配启用的 HTTP MCP，并传递禁用工具配置。"""
    created = []
    exposed_tool = SimpleNamespace(name="mcp__internal-mcp__echo")

    class FakeMCPClient:
        async def list_tools(self):
            return [exposed_tool]

    async def fake_get_mcp_client(config):
        created.append(config)
        return FakeMCPClient()

    monkeypatch.setattr("yuxi.agents.mcp.service.get_mcp_client", fake_get_mcp_client)

    result = await tools.build_mcp_tools(
        mcp_servers=[
            {
                "slug": "internal-mcp",
                "transport": "streamable_http",
                "url": "http://internal-mcp:9000/mcp",
                "headers": {"Authorization": "secret"},
                "timeout": 12,
                "disabled_tools": ["admin"],
            }
        ],
    )

    assert result == [exposed_tool]
    assert len(created) == 1
    assert created == [
        {
            "internal-mcp": {
                "transport": "streamable_http",
                "url": "http://internal-mcp:9000/mcp",
                "headers": {"Authorization": "secret"},
                "timeout": 12,
                "disabled_tools": ["admin"],
            }
        }
    ]


async def test_build_mcp_tools_propagates_health_failure(monkeypatch):
    """已配置 MCP 不可达时显式终止本轮，不静默撤掉工具。"""

    class BrokenMCPClient:
        async def list_tools(self):
            raise ConnectionError("mcp unavailable")

    async def fake_get_mcp_client(_config):
        return BrokenMCPClient()

    monkeypatch.setattr("yuxi.agents.mcp.service.get_mcp_client", fake_get_mcp_client)

    with pytest.raises(ConnectionError, match="mcp unavailable"):
        await tools.build_mcp_tools(
            mcp_servers=[
                {
                    "slug": "broken-mcp",
                    "transport": "streamable_http",
                    "url": "http://broken:9000/mcp",
                }
            ]
        )


async def test_build_mcp_tools_rejects_stdio_projection():
    """stdio 不得进入服务进程动态 MCP 装配。"""
    with pytest.raises(ValueError, match="不允许"):
        await tools.build_mcp_tools(mcp_servers=[{"slug": "stdio", "transport": "stdio"}])


async def test_build_subagent_tools_overrides_agent_create_with_session_templates():
    """leader 的 AgentCreate schema 只暴露本轮投影允许的模板。"""
    storage = SimpleNamespace(
        get_session=AsyncMock(return_value=SimpleNamespace(team_id=None)),
    )

    built = await tools.build_subagent_tools(
        storage=storage,
        message_bus=SimpleNamespace(),
        workspace_manager=SimpleNamespace(),
        user_id="u",
        agent_id="a",
        session_id="s",
        templates=[
            {
                "type": "researcher",
                "description": "调研",
                "system_prompt_template": "research {member_name}",
            },
            {
                "type": "verifier",
                "description": "核验",
                "system_prompt_template": "verify {member_name}",
            },
        ],
    )

    assert [tool.name for tool in built] == ["AgentCreate"]
    assert built[0].input_schema["properties"]["subagent_type"]["enum"] == [
        "researcher",
        "verifier",
    ]
    assert "subagent_type" in built[0].input_schema["required"]
    researcher_prompt = built[0]._sub_agent_templates["researcher"].system_prompt_template
    assert researcher_prompt.startswith("research {member_name}")
    assert "必须调用 TeamSay" in researcher_prompt
    assert "TeamSay 成功前不得结束本轮" in researcher_prompt


async def test_build_subagent_tools_disables_agent_create_when_allowlist_is_empty():
    """显式空允许集合必须覆盖内建 default，调用时明确拒绝。"""
    storage = SimpleNamespace(get_session=AsyncMock(return_value=SimpleNamespace(team_id=None)))

    built = await tools.build_subagent_tools(
        storage=storage,
        message_bus=SimpleNamespace(),
        workspace_manager=SimpleNamespace(),
        user_id="u",
        agent_id="a",
        session_id="s",
        templates=[],
    )

    assert [tool.name for tool in built] == ["AgentCreate"]
    assert "没有可用" in built[0].description


async def test_build_subagent_tools_does_not_expose_agent_create_to_worker():
    """Team worker 不得因动态覆盖重新获得 leader 专属工具。"""
    storage = SimpleNamespace(
        get_session=AsyncMock(return_value=SimpleNamespace(team_id="team")),
        get_team=AsyncMock(return_value=SimpleNamespace(session_id="leader-session")),
    )

    built = await tools.build_subagent_tools(
        storage=storage,
        message_bus=SimpleNamespace(),
        workspace_manager=SimpleNamespace(),
        user_id="u",
        agent_id="worker",
        session_id="worker-session",
        templates=[
            {
                "type": "researcher",
                "description": "调研",
                "system_prompt_template": "research",
            }
        ],
    )

    assert built == []
