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


def test_textual_inline_guards_empty_data():
    """回归：application/json 且 data=None 时不得返回字符串 "None"。"""
    from yuxi.agentscope.tools import _textual_inline

    assert _textual_inline(None, "application/json") is None
    assert _textual_inline(None, "text/plain") is None
    assert _textual_inline(b"hello", "text/plain") == "hello"
    assert _textual_inline("hello", "application/json") == "hello"
    assert _textual_inline(b"\xff\xfe", "text/plain") is None  # 非 UTF-8
    assert _textual_inline(b"bin", "application/pdf") is None


def test_build_ask_user_question_tool_is_external_with_structured_schema():
    """主动提问必须由 AgentScope external-execution 原生链路挂起。"""
    tool = tools.build_ask_user_question_tool()

    assert tool.name == "ask_user_question"
    assert tool.is_external_tool is True
    assert tool.is_concurrency_safe is False
    assert tool.input_schema["required"] == ["questions"]
    question_schema = tool.input_schema["properties"]["questions"]["items"]
    assert {"question", "header", "options"} <= set(question_schema["required"])


def test_build_web_search_tool_remains_available_without_provider_keys(monkeypatch):
    """搜索未配置时仍注册工具，由调用结果显式说明缺少配置。"""
    monkeypatch.delenv("DOUBAO_SEARCH_API_KEY", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    tool = tools.build_web_search_tool()

    assert tool is not None
    assert tool.name == "web_search"


async def test_build_optional_tools_respects_empty_and_explicit_allowlist(monkeypatch):
    """tools=[] 禁用全部可选工具，白名单只装配指定项。"""
    monkeypatch.setattr(tools, "build_web_search_tool", lambda: SimpleNamespace(name="web_search"))
    monkeypatch.setattr(tools, "_present_artifacts_tool", lambda *_args: SimpleNamespace(name="present_artifacts"))
    monkeypatch.setattr(tools, "_ocr_parse_file_tool", lambda *_args: SimpleNamespace(name="ocr_parse_file"))

    assert (
        await tools.build_optional_tools(tool_slugs=[], uid="u", knowledge_slugs=[], agent_id="a", session_id="s") == []
    )
    selected = await tools.build_optional_tools(
        tool_slugs=["ask_user_question", "present_artifacts"],
        uid="u",
        knowledge_slugs=[],
        agent_id="a",
        session_id="s",
    )
    assert [item.name for item in selected] == ["ask_user_question", "present_artifacts"]


async def test_skill_dependency_gateway_requires_activation_and_dispatches(monkeypatch):
    """Gateway 只允许调用当前 Session 已经通过 Skill 激活的依赖。"""
    from agentscope.permission import PermissionBehavior, PermissionDecision
    from agentscope.tool import FunctionTool

    async def search(query: str) -> str:
        return f"result:{query}"

    dependency_tool = FunctionTool(search, name="web_search", is_read_only=True)
    dependency_tool.check_permissions = AsyncMock(
        return_value=PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="allowed",
        )
    )
    workspace = SimpleNamespace(
        list_skills=AsyncMock(return_value=[SimpleNamespace(name="research", markdown="# Research")])
    )
    monkeypatch.setattr(
        tools,
        "build_dependency_tools",
        AsyncMock(return_value=[dependency_tool]),
    )
    monkeypatch.setattr(
        tools,
        "build_mcp_tools",
        AsyncMock(return_value=[]),
    )
    projection = SimpleNamespace(
        skill_tool_dependencies={"research": ["web_search"]},
        skill_mcp_servers={"research": []},
        knowledge_slugs=[],
    )

    extensions = await tools.build_skill_dependency_gateway(
        projection=projection,
        workspace=workspace,
        uid="u",
        agent_id="a",
        session_id="s",
    )

    assert extensions[0].name == "Skill"
    assert extensions[0].is_concurrency_safe is True
    gateway = extensions[1]
    assert gateway.name == "skill_dependency_gateway"
    state = SimpleNamespace(tool_context=SimpleNamespace(activated_groups=[]))

    blocked = await gateway.call(
        skill="research",
        tool_name="web_search",
        arguments={"query": "AgentScope"},
        _agent_state=state,
    )
    assert blocked.state == "error"

    viewed = await extensions[0].call(skill="research", _agent_state=state)
    assert "skill_dependency_gateway" in viewed.content[0].text
    assert state.tool_context.activated_groups == ["skill__research"]

    result = await gateway.call(
        skill="research",
        tool_name="web_search",
        arguments={"query": "AgentScope"},
        _agent_state=state,
    )
    assert result.content[0].text == "result:AgentScope"


async def test_external_skill_gateway_requires_token_and_preserves_nested_schema(monkeypatch):
    """外部依赖只能使用 Skill 返回的令牌，questions 保持嵌套协议。"""
    import re

    from agentscope.permission import PermissionBehavior

    external = tools.build_ask_user_question_tool()
    workspace = SimpleNamespace(
        list_skills=AsyncMock(return_value=[SimpleNamespace(name="research", markdown="# Research")])
    )
    monkeypatch.setattr(
        tools,
        "build_dependency_tools",
        AsyncMock(return_value=[external]),
    )
    monkeypatch.setattr(tools, "build_mcp_tools", AsyncMock(return_value=[]))
    projection = SimpleNamespace(
        skill_tool_dependencies={"research": ["ask_user_question"]},
        skill_mcp_servers={"research": []},
        knowledge_slugs=[],
    )
    extensions = await tools.build_skill_dependency_gateway(
        projection=projection,
        workspace=workspace,
        uid="u",
        agent_id="a",
        session_id="s",
    )
    viewer, gateway = extensions
    state = SimpleNamespace(tool_context=SimpleNamespace(activated_groups=[]))
    viewed = await viewer.call(skill="research", _agent_state=state)
    token = re.search(r"activation_token 固定为 `([^`]+)`", viewed.content[0].text).group(1)
    arguments = {
        "questions": [
            {
                "question": "范围？",
                "header": "范围",
                "options": [
                    {"label": "A", "description": "选项 A"},
                    {"label": "B", "description": "选项 B"},
                ],
            }
        ]
    }

    denied = await gateway.check_permissions(
        {
            "skill": "research",
            "tool_name": "ask_user_question",
            "arguments": arguments,
            "activation_token": "invalid",
        },
        SimpleNamespace(),
    )
    allowed = await gateway.check_permissions(
        {
            "skill": "research",
            "tool_name": "ask_user_question",
            "arguments": arguments,
            "activation_token": token,
        },
        SimpleNamespace(),
    )

    assert denied.behavior == PermissionBehavior.DENY
    assert allowed.behavior == PermissionBehavior.ALLOW


async def test_media_reader_returns_image_block_and_rejects_unknown_binary():
    """独立媒体工具按真实文件头返回图片块，拒绝伪装二进制。"""
    from agentscope.message import DataBlock

    backend = SimpleNamespace(
        read_file=AsyncMock(return_value=b"\x89PNG\r\n\x1a\nfixture"),
        basename=lambda path: path.rsplit("/", 1)[-1],
    )
    workspace = SimpleNamespace(get_backend=lambda: backend)
    reader = tools.build_media_reader_tool(workspace)

    image = await reader.call(file_path="/workspace/uploads/demo.png")
    assert isinstance(image.content[1], DataBlock)
    assert image.content[1].source.media_type == "image/png"

    backend.read_file.return_value = b"not-an-image"
    invalid = await reader.call(file_path="/workspace/uploads/demo.png")
    assert invalid.state == "error"


async def test_media_reader_extracts_selected_pdf_pages(monkeypatch):
    """PDF 读取只返回指定的 1-based 页面。"""
    import pypdf

    pages = [
        SimpleNamespace(extract_text=lambda: "FIRST PAGE"),
        SimpleNamespace(extract_text=lambda: "SECOND PAGE"),
    ]
    monkeypatch.setattr(pypdf, "PdfReader", lambda _stream: SimpleNamespace(pages=pages))
    backend = SimpleNamespace(
        read_file=AsyncMock(return_value=b"%PDF fixture"),
        basename=lambda path: path.rsplit("/", 1)[-1],
    )
    reader = tools.build_media_reader_tool(SimpleNamespace(get_backend=lambda: backend))

    result = await reader.call(file_path="/workspace/uploads/demo.pdf", pages=[2])

    assert "SECOND PAGE" in result.content[0].text
    assert "FIRST PAGE" not in result.content[0].text


@pytest.mark.asyncio
async def test_present_artifacts_persists_validated_manifest():
    writes = []

    class Backend:
        async def stat(self, path):
            return SimpleNamespace(is_dir=False) if path == "/workspace/outputs/report.md" else None

        async def write_file(self, path, content):
            writes.append((path, content))

    workspace = SimpleNamespace(get_backend=lambda: Backend())
    tool = tools._present_artifacts_tool(workspace)

    result = await tool.call(filepaths=["/workspace/outputs/report.md"])

    assert "report.md" in str(result.content)
    assert writes[0][0] == "/workspace/data/yuxi-artifacts.json"
    assert b"/workspace/outputs/report.md" in writes[0][1]


@pytest.mark.asyncio
async def test_present_artifacts_rejects_internal_workspace_file():
    workspace = SimpleNamespace(get_backend=lambda: object())
    tool = tools._present_artifacts_tool(workspace)

    with pytest.raises(ValueError, match="outputs"):
        await tool.call(filepaths=["/workspace/skills/private.md"])


async def test_shared_workspace_tools_allow_root_and_reject_traversal(tmp_path, monkeypatch):
    from yuxi.agents.backends.sandbox import paths

    monkeypatch.setattr(paths.conf, "save_dir", str(tmp_path))
    toolset = {tool.name: tool for tool in tools.build_shared_workspace_tools("user-1")}
    await toolset["shared_workspace_write"].call(
        path="/workspace/workspace/report.txt",
        content="result",
    )

    listed = await toolset["shared_workspace_list"].call(path="/workspace/workspace")
    assert "report.txt" in str(listed.content)
    with pytest.raises(ValueError, match="个人工作区"):
        await toolset["shared_workspace_read"].call(path="/workspace/workspace/../agents/AGENTS.md")
