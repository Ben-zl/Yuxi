"""yuxi 工具集的 agentscope 适配（迁移工单 07）。

知识库工具与网页搜索从旧 langchain 工具体系移植为 agentscope
FunctionTool：核心调用 KnowledgeBaseManager 的公开方法；可见性沿用
「用户权限（get_databases_by_uid）∩ 会话启用集（knowledge_slugs）」语义。
LITE 模式或知识库管理器不可用时不装配知识库工具。
"""

import base64
import io
import json
import mimetypes
import secrets
from pathlib import Path, PurePosixPath

import httpx

from yuxi.agentscope.projection import is_lite_mode

# 成功后不重复初始化；失败不置位，下一轮可重试。
_kb_initialized = False

_TEAM_WORKER_PROTOCOL = """
你是 Team worker。AgentCreate 的 prompt 会作为首个任务自动交给你。
完成任务后必须调用 TeamSay，把完整结果报告给 leader；to 使用 null 即可广播给团队中的其他成员。
TeamSay 成功前不得结束本轮，也不能只用普通文本声称已经完成。
""".strip()


def _json(value) -> str:
    """工具返回值统一序列化为中文友好的 JSON 文本。"""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


async def _visible_knowledge_bases(uid: str, knowledge_slugs: list[str] | None) -> list[dict]:
    """用户可见知识库（uid 权限 ∩ 会话启用集；None 表示全部可见）。"""
    from yuxi.knowledge.runtime import knowledge_base

    summaries = await knowledge_base.get_databases_by_uid(uid)
    visible = [
        {"kb_id": s.kb_id, "name": s.name, "description": s.description, "kb_type": s.kb_type} for s in summaries
    ]
    if knowledge_slugs is not None:
        enabled = set(knowledge_slugs)
        visible = [kb for kb in visible if kb["kb_id"] in enabled or kb["name"] in enabled]
    return visible


async def _ensure_kb_manager_ready() -> bool:
    """确保知识库管理器已初始化；失败显式抛出并允许下一轮重试。"""
    global _kb_initialized
    if is_lite_mode():
        return False
    if not _kb_initialized:
        from yuxi.knowledge.runtime import knowledge_base

        await knowledge_base.initialize()
        _kb_initialized = True
    return True


async def build_kb_tools(*, uid: str, knowledge_slugs: list[str] | None) -> list:
    """构建知识库工具集；不可用（LITE/初始化失败/无用户）时返回空集。"""
    if not await _ensure_kb_manager_ready():
        return []

    from yuxi.knowledge.runtime import knowledge_base

    async def list_kbs() -> str:
        """列出当前用户可见的知识库（返回 kb_id、名称与描述）。"""
        visible = await _visible_knowledge_bases(uid, knowledge_slugs)
        return _json([{"kb_id": kb["kb_id"], "name": kb["name"], "description": kb["description"]} for kb in visible])

    async def query_kb(kb_id: str, query_text: str, file_name: str | None = None) -> str:
        """在指定知识库中检索与 query_text 相关的内容片段。

        Args:
            kb_id: 知识库 ID（见 list_kbs）
            query_text: 检索文本
            file_name: 可选，限定文件名包含该值的文档
        """
        target_error = await _check_target_visible(uid, knowledge_slugs, kb_id)
        if target_error:
            return target_error
        result = await knowledge_base.retrieve(kb_id, query_text, file_name=file_name)
        return _json(result)

    async def open_kb_document(kb_id: str, file_id: str, offset: int = 0, window_size: int = 200) -> str:
        """分页打开知识库文档内容。

        Args:
            kb_id: 知识库 ID
            file_id: 文件 ID
            offset: 起始行偏移
            window_size: 每页行数
        """
        target_error = await _check_target_visible(uid, knowledge_slugs, kb_id)
        if target_error:
            return target_error
        result = await knowledge_base.open_document(kb_id, file_id, offset=offset, limit=window_size)
        return _json(result)

    async def find_kb_document(
        kb_id: str,
        file_id: str,
        patterns: list[str],
        use_regex: bool = False,
        case_sensitive: bool = False,
    ) -> str:
        """在文档中查找匹配文本的窗口片段。

        Args:
            kb_id: 知识库 ID
            file_id: 文件 ID
            patterns: 查找模式列表
            use_regex: 是否按正则匹配
            case_sensitive: 是否区分大小写
        """
        target_error = await _check_target_visible(uid, knowledge_slugs, kb_id)
        if target_error:
            return target_error
        result = await knowledge_base.find_in_document(
            kb_id, file_id, patterns, use_regex=use_regex, case_sensitive=case_sensitive
        )
        return _json(result)

    async def search_file(query: str | None = None, limit: int = 50) -> str:
        """按文件名在可见知识库中搜索文件。"""
        visible = await _visible_knowledge_bases(uid, knowledge_slugs)
        result = await knowledge_base.search_document_files(visible, query=query, limit=limit)
        return _json(result)

    async def get_mindmap(kb_name: str) -> str:
        """读取知识库的思维导图（层级文本，- 缩进表示层级）。"""
        visible = await _visible_knowledge_bases(uid, knowledge_slugs)
        target = next((kb for kb in visible if kb["name"] == kb_name), None)
        if target is None:
            return f"知识库 {kb_name} 不存在或不可见"
        from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository

        record = await KnowledgeBaseRepository().get_by_kb_id(target["kb_id"])
        if record is None or not record.mindmap:
            return f"知识库 {kb_name} 暂无思维导图"

        def _render(nodes: list[dict], depth: int) -> list[str]:
            lines = []
            for node in nodes or []:
                lines.append(f"{'  ' * depth}- {node.get('content', '')}")
                lines.extend(_render(node.get("children"), depth + 1))
            return lines

        return "\n".join(_render(record.mindmap if isinstance(record.mindmap, list) else [], 0))

    from agentscope.tool import FunctionTool

    # 全部为只读检索类工具，无需人工审批（与旧栈 KB 工具语义一致）
    definitions = [
        (list_kbs, "list_kbs", "列出当前用户可见的知识库"),
        (query_kb, "query_kb", "在知识库中检索相关内容片段"),
        (open_kb_document, "open_kb_document", "分页读取知识库文档内容"),
        (find_kb_document, "find_kb_document", "在文档中查找匹配文本"),
        (search_file, "search_file", "按文件名搜索知识库文件"),
        (get_mindmap, "get_mindmap", "读取知识库思维导图"),
    ]
    return [
        FunctionTool(func, name=name, description=description, is_read_only=True)
        for func, name, description in definitions
    ]


async def _check_target_visible(uid: str, knowledge_slugs: list[str] | None, kb_id: str) -> str | None:
    """校验 kb_id 对当前用户可见；不可见时返回错误文本。"""
    visible = await _visible_knowledge_bases(uid, knowledge_slugs)
    if not any(kb["kb_id"] == kb_id for kb in visible):
        return f"知识库 {kb_id} 不存在或不可见"
    return None


def _textual_inline(data, media_type: str) -> str | None:
    """文本类内容内联为模型可读字符串；二进制或无数据返回 None。

    application/json 的 media_type 不带 text/ 前缀，需并列判断；
    data 为空时不得落入文本分支（回归：曾因运算符优先级返回 "None"）。
    """
    if data is None:
        return None
    is_textual = media_type.startswith("text/") or media_type == "application/json"
    if not is_textual:
        return None
    try:
        return data.decode("utf-8") if isinstance(data, bytes) else str(data)
    except UnicodeDecodeError:
        return None


async def _doubao_search(query: str, count: int, api_key: str) -> str:
    """豆包搜索 provider（Bearer 认证）。"""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            "https://open.feedcoopapi.com/search_api/web_search",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"query": query, "count": count, "content_format": "text"},
        )
        resp.raise_for_status()
    return _json(resp.json())


async def _tavily_search(query: str, count: int, api_key: str) -> str:
    """Tavily 搜索 provider（REST API）。"""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            "https://api.tavily.com/search",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"query": query, "max_results": count},
        )
        resp.raise_for_status()
    return _json(resp.json())


async def web_search(query: str, count: int = 10) -> str:
    """网页搜索（豆包/Tavily 按配置分派；返回标题、链接与摘要）。

    Args:
        query: 搜索关键词
        count: 结果条数
    """
    import os

    doubao_key = os.getenv("DOUBAO_SEARCH_API_KEY")
    if doubao_key:
        return await _doubao_search(query, count, doubao_key)
    tavily_key = os.getenv("TAVILY_API_KEY")
    if tavily_key:
        return await _tavily_search(query, count, tavily_key)
    return "web_search 未配置（缺少 DOUBAO_SEARCH_API_KEY 或 TAVILY_API_KEY）"


def build_web_search_tool():
    """构建网页搜索工具，未配置 provider 时由工具调用显式报错。"""
    from agentscope.tool import FunctionTool

    return FunctionTool(web_search, name="web_search", description="搜索互联网获取最新信息")


async def build_mcp_tools(
    *,
    mcp_servers: list[dict],
) -> list:
    """按统一投影在 AgentScope 服务进程中装配当前轮次 HTTP MCP 工具。"""
    from yuxi.agents.mcp.service import get_mcp_client

    tools = []
    for config in mcp_servers:
        slug = config["slug"]
        client = await get_mcp_client({slug: {key: value for key, value in config.items() if key != "slug"}})
        tools.extend(await client.list_tools())
    return tools


def build_ask_user_question_tool():
    """构建由前端执行并通过 external-execution 结果恢复的提问工具。"""
    from typing import Any

    from agentscope.permission import PermissionBehavior, PermissionDecision
    from agentscope.tool import ToolBase

    class AskUserQuestion(ToolBase):
        """向用户展示结构化问题并挂起当前回复。"""

        name = "ask_user_question"
        description = "需要用户补充关键信息时，展示一组结构化问题并等待回答。"
        is_external_tool = True
        is_concurrency_safe = False
        is_read_only = True
        input_schema: dict[str, Any] = {
            "type": "object",
            "properties": {
                "questions": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 4,
                    "items": {
                        "type": "object",
                        "properties": {
                            "question": {"type": "string", "minLength": 1},
                            "header": {"type": "string", "minLength": 1, "maxLength": 12},
                            "options": {
                                "type": "array",
                                "minItems": 2,
                                "maxItems": 4,
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "label": {"type": "string", "minLength": 1},
                                        "description": {"type": "string", "minLength": 1},
                                    },
                                    "required": ["label", "description"],
                                    "additionalProperties": False,
                                },
                            },
                        },
                        "required": ["question", "header", "options"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["questions"],
            "additionalProperties": False,
        }

        async def check_permissions(self, tool_input, context) -> PermissionDecision:
            """提问只读取用户输入，无需额外工具审批。"""
            return PermissionDecision(
                behavior=PermissionBehavior.ALLOW,
                decision_reason="ask_user_question is an external read-only tool",
                message="允许向当前用户提问",
            )

    return AskUserQuestion()


def build_media_reader_tool(workspace):
    """构建独立图片/PDF读取工具，避免修改 AgentScope 内置 Read。"""
    if workspace is None:
        raise ValueError("read_media 需要已初始化的 AgentScope workspace")

    from agentscope.message import Base64Source, DataBlock, TextBlock, ToolResultState
    from agentscope.permission import PermissionBehavior, PermissionDecision
    from agentscope.tool import ToolBase, ToolChunk

    class MediaReader(ToolBase):
        """读取 workspace 中的图片或文本型 PDF。"""

        name = "read_media"
        description = "读取 workspace 图片或 PDF；扫描 PDF 无文本时请改用 ocr_parse_file。"
        is_concurrency_safe = True
        is_read_only = True
        input_schema = {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "minLength": 1},
                "pages": {
                    "type": "array",
                    "items": {"type": "integer", "minimum": 1},
                    "uniqueItems": True,
                },
            },
            "required": ["file_path"],
            "additionalProperties": False,
        }

        async def check_permissions(self, tool_input, context) -> PermissionDecision:
            """媒体读取为只读操作，交由权限引擎继续匹配规则。"""
            return PermissionDecision(
                behavior=PermissionBehavior.PASSTHROUGH,
                message="Media reading is read-only.",
            )

        async def call(self, file_path: str, pages: list[int] | None = None) -> ToolChunk:
            """返回图片 DataBlock 或指定 PDF 页面的文本。"""
            raw = await workspace.get_backend().read_file(file_path)
            if len(raw) > 20 * 1024 * 1024:
                return _gateway_error("媒体文件超过 20 MB 读取限制。")

            media_type = _detect_image_media_type(raw)
            if media_type:
                return ToolChunk(
                    content=[
                        TextBlock(text=f"Image: {file_path}"),
                        DataBlock(
                            source=Base64Source(
                                data=base64.b64encode(raw).decode("ascii"),
                                media_type=media_type,
                            ),
                            name=workspace.get_backend().basename(file_path),
                        ),
                    ],
                    state=ToolResultState.RUNNING,
                )

            guessed_type = mimetypes.guess_type(file_path)[0]
            if guessed_type != "application/pdf" and not file_path.lower().endswith(".pdf"):
                return _gateway_error("read_media 仅支持 PNG/JPEG/GIF/WebP 图片和 PDF。")

            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(raw))
            if len(reader.pages) > 10 and not pages:
                return _gateway_error("PDF 超过 10 页，请通过 pages 指定需要读取的页码。")
            selected = pages or list(range(1, len(reader.pages) + 1))
            invalid = [page for page in selected if page > len(reader.pages)]
            if invalid:
                return _gateway_error(f"PDF 页码越界: {invalid}")
            texts = [reader.pages[page - 1].extract_text() or "" for page in selected]
            if not any(text.strip() for text in texts):
                return _gateway_error("PDF 没有可提取文本，请使用 ocr_parse_file 处理扫描页。")
            content = "\n\n".join(f"--- Page {page} ---\n{text}" for page, text in zip(selected, texts))
            return ToolChunk(content=[TextBlock(text=content)], state=ToolResultState.RUNNING)

    return MediaReader()


def build_shared_workspace_tools(uid: str) -> list:
    """构建服务进程侧个人工作区工具，不向 Docker workspace 暴露宿主路径。"""
    from agentscope.tool import FunctionTool
    from yuxi.agents.backends.sandbox.paths import global_user_data_dir

    user_data_root = global_user_data_dir(uid)
    user_data_root.mkdir(parents=True, exist_ok=True)
    workspace_root = user_data_root / "workspace"
    if workspace_root.is_symlink():
        raise ValueError("个人工作区根目录不能是符号链接")
    workspace_root.mkdir(parents=True, exist_ok=True)
    root = workspace_root.resolve()
    if not root.is_relative_to(user_data_root.resolve()):
        raise ValueError("个人工作区根目录越界")

    def resolve(path: str, *, allow_missing: bool = False) -> Path:
        virtual = PurePosixPath("/" + path.lstrip("/"))
        prefix = PurePosixPath("/workspace/workspace")
        if ".." in virtual.parts or not virtual.is_relative_to(prefix):
            raise ValueError("个人工作区路径必须位于 /workspace/workspace")
        target = root.joinpath(*virtual.relative_to(prefix).parts)
        parent = target.parent.resolve()
        parent_outside = target != root and not parent.is_relative_to(root)
        target_outside = target.exists() and not target.resolve().is_relative_to(root)
        if parent_outside or target_outside:
            raise ValueError("个人工作区路径越界")
        if not allow_missing and not target.exists():
            raise FileNotFoundError(path)
        return target

    async def shared_workspace_list(path: str = "/workspace/workspace") -> str:
        """列出个人工作区目录。"""
        target = resolve(path)
        if not target.is_dir():
            raise ValueError("路径不是目录")
        return _json(
            [
                {"name": child.name, "is_dir": child.is_dir(), "size": child.stat().st_size if child.is_file() else 0}
                for child in sorted(target.iterdir(), key=lambda item: item.name.lower())
                if child.resolve().is_relative_to(root)
            ]
        )

    async def shared_workspace_read(path: str) -> str:
        """读取个人工作区 UTF-8 文本文件。"""
        target = resolve(path)
        if not target.is_file() or target.stat().st_size > 2 * 1024 * 1024:
            raise ValueError("只能读取 2 MB 以内的文本文件")
        return target.read_text(encoding="utf-8")

    async def shared_workspace_write(path: str, content: str) -> str:
        """写入个人工作区 UTF-8 文本文件。"""
        if len(content.encode("utf-8")) > 2 * 1024 * 1024:
            raise ValueError("写入内容不能超过 2 MB")
        target = resolve(path, allow_missing=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return _json({"path": path, "size": target.stat().st_size})

    return [
        FunctionTool(shared_workspace_list, name="shared_workspace_list", description="列出个人共享工作区目录"),
        FunctionTool(
            shared_workspace_read,
            name="shared_workspace_read",
            description="读取个人共享工作区文本文件",
            is_read_only=True,
        ),
        FunctionTool(shared_workspace_write, name="shared_workspace_write", description="写入个人共享工作区文本文件"),
    ]


def _detect_image_media_type(raw: bytes) -> str | None:
    """根据真实文件头识别允许的图片类型。"""
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if raw.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if raw.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    return None


async def build_optional_tools(
    *,
    tool_slugs: list[str] | None,
    uid: str,
    knowledge_slugs: list[str] | None,
    agent_id: str,
    session_id: str,
    workspace=None,
) -> list:
    """按 Agent 白名单装配 Yuxi 可选工具；None 表示全部目录项。"""
    from yuxi.agents.toolkits.service import get_tool_metadata

    selected = [item["slug"] for item in get_tool_metadata()] if tool_slugs is None else tool_slugs
    tools_by_slug = {
        "ask_user_question": build_ask_user_question_tool,
        "present_artifacts": lambda: _present_artifacts_tool(workspace),
        "ocr_parse_file": lambda: _ocr_parse_file_tool(uid, agent_id, session_id),
        "read_media": lambda: build_media_reader_tool(workspace),
        "web_search": build_web_search_tool,
    }
    result = []
    for slug in selected:
        factory = tools_by_slug.get(slug)
        if factory is None:
            raise ValueError(f"不支持的 Yuxi 可选工具: {slug}")
        tool = factory()
        if tool is not None:
            result.append(tool)
    return result


async def build_dependency_tools(
    *,
    tool_slugs: list[str],
    uid: str,
    knowledge_slugs: list[str] | None,
    agent_id: str,
    session_id: str,
    workspace=None,
) -> list:
    """构建 Skill 声明的 Yuxi 工具依赖，并严格按 slug 返回。"""
    from yuxi.agents.toolkits.service import get_tool_metadata

    optional_names = {item["slug"] for item in get_tool_metadata()}
    selected_optional = [slug for slug in tool_slugs if slug in optional_names]
    result = await build_optional_tools(
        tool_slugs=selected_optional,
        uid=uid,
        knowledge_slugs=knowledge_slugs,
        agent_id=agent_id,
        session_id=session_id,
        workspace=workspace,
    )

    kb_tools = await build_kb_tools(uid=uid, knowledge_slugs=knowledge_slugs)
    if await _ensure_kb_manager_ready():
        kb_tools.append(_download_kb_file_tool(uid, knowledge_slugs))
    kb_by_name = {tool.name: tool for tool in kb_tools}
    result.extend(kb_by_name[slug] for slug in tool_slugs if slug in kb_by_name)

    resolved = {tool.name for tool in result}
    missing = [slug for slug in tool_slugs if slug not in resolved]
    if missing:
        raise ValueError("Skill 工具依赖当前不可用: " + ", ".join(missing))
    return result


async def build_skill_dependency_gateway(
    *,
    projection,
    workspace,
    uid: str,
    agent_id: str,
    session_id: str,
) -> list:
    """构建 Skill 激活器和按 Session 隔离的依赖调用 Gateway。"""
    from jsonschema import ValidationError, validate

    from agentscope.message import TextBlock, ToolResultState
    from agentscope.permission import PermissionBehavior, PermissionDecision
    from agentscope.tool import ToolBase, ToolChunk

    dependency_tools: dict[str, dict[str, ToolBase]] = {}
    external_tokens: dict[str, str] = {}
    for skill_slug in projection.skill_tool_dependencies:
        configured = await build_dependency_tools(
            tool_slugs=projection.skill_tool_dependencies.get(skill_slug, []),
            uid=uid,
            knowledge_slugs=projection.knowledge_slugs,
            agent_id=agent_id,
            session_id=session_id,
            workspace=workspace,
        )
        configured.extend(
            await build_mcp_tools(
                mcp_servers=projection.skill_mcp_servers.get(skill_slug, []),
            )
        )
        if configured:
            dependency_tools[skill_slug] = {tool.name: tool for tool in configured}
            if any(tool.is_external_tool for tool in configured):
                external_tokens[skill_slug] = secrets.token_urlsafe(24)

    async def list_skills() -> dict:
        items = await workspace.list_skills(agent_id=agent_id)
        return {item.name: item for item in items}

    class ActivatingSkillViewer(ToolBase):
        """读取 Skill 内容，并在当前 Session state 中激活其依赖组。"""

        name = "Skill"
        description = "读取与当前任务匹配的 Skill 指令。"
        is_state_injected = True
        is_read_only = True
        is_concurrency_safe = True
        input_schema = {
            "type": "object",
            "properties": {"skill": {"type": "string", "minLength": 1}},
            "required": ["skill"],
            "additionalProperties": False,
        }

        async def check_permissions(self, tool_input, context) -> PermissionDecision:
            """Skill 读取及依赖激活均为只读操作。"""
            return PermissionDecision(
                behavior=PermissionBehavior.ALLOW,
                decision_reason="Skill viewer is read-only",
                message="允许读取 Skill",
            )

        async def call(self, skill: str, _agent_state) -> ToolChunk:
            """返回 Skill 正文并激活当前 Session 的依赖 Gateway。"""
            skills = await list_skills()
            target = skills.get(skill)
            if target is None:
                return ToolChunk(
                    content=[TextBlock(text=f"SkillNotFoundError: Skill '{skill}' not found.")],
                    state=ToolResultState.ERROR,
                )
            group = f"skill__{skill}"
            if skill in dependency_tools and group not in _agent_state.tool_context.activated_groups:
                _agent_state.tool_context.activated_groups.append(group)

            guidance = _dependency_guidance(
                skill,
                dependency_tools.get(skill, {}),
                external_tokens.get(skill),
            )
            return ToolChunk(content=[TextBlock(text=f"{target.markdown}{guidance}")])

    class SkillDependencyGateway(ToolBase):
        """调用已激活 Skill 的服务端工具或 HTTP MCP。"""

        name = "skill_dependency_gateway"
        description = "调用已通过 Skill 工具激活的依赖工具；参数格式由 Skill 返回内容提供。"
        is_state_injected = True
        is_read_only = False
        is_concurrency_safe = False
        input_schema = {
            "type": "object",
            "properties": {
                "skill": {"type": "string", "enum": list(dependency_tools)},
                "tool_name": {
                    "type": "string",
                    "enum": sorted(
                        {
                            name
                            for tools_by_name in dependency_tools.values()
                            for name, tool in tools_by_name.items()
                            if not tool.is_external_tool
                        }
                    ),
                },
                "arguments": {"type": "object"},
            },
            "required": ["skill", "tool_name", "arguments"],
            "additionalProperties": False,
        }

        async def check_permissions(self, tool_input, context) -> PermissionDecision:
            """沿用被代理工具的审批策略。"""
            target = _resolve_dependency(
                dependency_tools,
                tool_input.get("skill"),
                tool_input.get("tool_name"),
                external=False,
            )
            if target is None:
                return PermissionDecision(
                    behavior=PermissionBehavior.DENY,
                    message="Skill 依赖工具不存在或类型不匹配。",
                )
            arguments = tool_input.get("arguments")
            if not isinstance(arguments, dict):
                return PermissionDecision(
                    behavior=PermissionBehavior.DENY,
                    message="Skill 依赖工具 arguments 必须是对象。",
                )
            return await target.check_permissions(arguments, context)

        async def call(self, skill: str, tool_name: str, arguments: dict, _agent_state):
            """校验激活状态与原始 schema 后调用依赖。"""
            if f"skill__{skill}" not in _agent_state.tool_context.activated_groups:
                return _gateway_error(f"Skill '{skill}' 尚未激活，请先调用 Skill。")
            target = _resolve_dependency(
                dependency_tools,
                skill,
                tool_name,
                external=False,
            )
            if target is None:
                return _gateway_error(f"Skill '{skill}' 没有可调用依赖 '{tool_name}'。")
            try:
                validate(instance=arguments, schema=target.input_schema)
            except ValidationError as exc:
                return _gateway_error(f"依赖工具参数不合法: {exc.message}")
            call_args = dict(arguments)
            if target.is_state_injected:
                call_args["_agent_state"] = _agent_state
            return await target(**call_args)

    class SkillExternalDependencyGateway(ToolBase):
        """把已激活 Skill 的外部工具调用交给 Yuxi 页面执行。"""

        name = "skill_external_dependency_gateway"
        description = "调用 Skill 返回内容中声明的外部依赖；必须携带该内容提供的 activation_token。"
        is_external_tool = True
        is_read_only = True
        is_concurrency_safe = False
        input_schema = {
            "type": "object",
            "properties": {
                "skill": {"type": "string", "enum": sorted(external_tokens)},
                "tool_name": {
                    "type": "string",
                    "enum": sorted(
                        {
                            name
                            for tools_by_name in dependency_tools.values()
                            for name, tool in tools_by_name.items()
                            if tool.is_external_tool
                        }
                    ),
                },
                "arguments": {"type": "object"},
                "activation_token": {"type": "string"},
            },
            "required": ["skill", "tool_name", "arguments", "activation_token"],
            "additionalProperties": False,
        }

        async def check_permissions(self, tool_input, context) -> PermissionDecision:
            """用当前进程签发的激活令牌阻止未查看 Skill 的外部调用。"""
            skill = tool_input.get("skill")
            target = _resolve_dependency(
                dependency_tools,
                skill,
                tool_input.get("tool_name"),
                external=True,
            )
            if target is None or not secrets.compare_digest(
                str(tool_input.get("activation_token") or ""),
                external_tokens.get(skill, ""),
            ):
                return PermissionDecision(
                    behavior=PermissionBehavior.DENY,
                    message="Skill 外部依赖未激活或令牌已失效，请重新调用 Skill。",
                )
            arguments = tool_input.get("arguments")
            if not isinstance(arguments, dict):
                return PermissionDecision(
                    behavior=PermissionBehavior.DENY,
                    message="Skill 外部依赖 arguments 必须是对象。",
                )
            try:
                validate(instance=arguments, schema=target.input_schema)
            except ValidationError as exc:
                return PermissionDecision(
                    behavior=PermissionBehavior.DENY,
                    message=f"Skill 外部依赖参数不合法: {exc.message}",
                )
            return await target.check_permissions(arguments, context)

    extensions = [ActivatingSkillViewer()]
    if any(not tool.is_external_tool for item in dependency_tools.values() for tool in item.values()):
        extensions.append(SkillDependencyGateway())
    if external_tokens:
        extensions.append(SkillExternalDependencyGateway())
    return extensions


def _resolve_dependency(dependencies: dict, skill: str | None, tool_name: str | None, *, external: bool):
    """按 Skill 和工具名解析依赖，并校验外部执行类型。"""
    target = dependencies.get(skill, {}).get(tool_name)
    if target is None or target.is_external_tool is not external:
        return None
    return target


def _dependency_guidance(skill: str, dependencies: dict, activation_token: str | None) -> str:
    """生成只在 Skill 读取后返回的依赖调用说明。"""
    if not dependencies:
        return ""
    lines = ["\n\n## 已激活的依赖工具", "依赖必须通过下列 Gateway 调用，不要直接调用原工具名："]
    for name, tool in dependencies.items():
        gateway = "skill_external_dependency_gateway" if tool.is_external_tool else "skill_dependency_gateway"
        schema = json.dumps(tool.input_schema, ensure_ascii=False)
        lines.append(f"- `{name}` → `{gateway}`，arguments schema: `{schema}`")
    lines.append(f"Gateway 的 skill 固定为 `{skill}`。")
    if activation_token:
        lines.append(f"外部 Gateway 的 activation_token 固定为 `{activation_token}`。")
    return "\n".join(lines)


def _gateway_error(message: str):
    """构造统一的 Gateway 错误结果。"""
    from agentscope.message import TextBlock, ToolResultState
    from agentscope.tool import ToolChunk

    return ToolChunk(content=[TextBlock(text=message)], state=ToolResultState.ERROR)


async def build_subagent_tools(
    *,
    storage,
    message_bus,
    workspace_manager,
    user_id: str,
    agent_id: str,
    session_id: str,
    templates: list[dict],
) -> list:
    """按当前线程允许集合覆盖内建 AgentCreate；worker 会话不暴露 leader 工具。"""
    session = await storage.get_session(user_id, agent_id, session_id)
    if session is None:
        raise ValueError("AgentScope 会话不存在，无法装配子智能体模板")
    if session.team_id is not None:
        team = await storage.get_team(user_id, session.team_id)
        if team is None:
            raise ValueError("AgentScope Team 已不存在，无法装配子智能体模板")
        if team.session_id != session_id:
            return []

    from copy import deepcopy

    from agentscope.app import SubAgentTemplate
    from agentscope.app._tool import AgentCreate
    from agentscope.tool import FunctionTool

    if not templates:

        async def unavailable_agent_create() -> str:
            """当前智能体没有可用的受管子智能体模板。"""
            raise ValueError("当前智能体没有可用的受管子智能体模板")

        return [
            FunctionTool(
                unavailable_agent_create,
                name="AgentCreate",
                description="当前智能体没有可用的受管子智能体模板，不能创建 Team worker。",
            )
        ]

    indexed = {}
    for item in templates:
        template = dict(item)
        user_prompt = str(template["system_prompt_template"]).rstrip()
        template["system_prompt_template"] = f"{user_prompt}\n\n{_TEAM_WORKER_PROTOCOL}"
        indexed[template["type"]] = SubAgentTemplate(**template)
    tool = AgentCreate(
        storage=storage,
        message_bus=message_bus,
        workspace_manager=workspace_manager,
        user_id=user_id,
        agent_id=agent_id,
        session_id=session_id,
        sub_agent_templates=indexed,
    )
    # AgentScope 会自动追加 default。Yuxi 的受管模板必须显式选择，禁止
    # 回落到通用 worker 绕过主 Agent 的允许列表。
    tool._sub_agent_templates.pop("default", None)
    schema = deepcopy(tool.input_schema)
    schema["properties"]["subagent_type"]["enum"] = list(indexed)
    schema["properties"]["subagent_type"]["description"] = "受管子智能体类型，必须来自当前允许集合。"
    schema.setdefault("required", []).append("subagent_type")
    tool.input_schema = schema
    return [tool]


async def build_extra_tools(
    *,
    uid: str,
    knowledge_slugs: list[str] | None,
    agent_id: str,
    session_id: str,
    tool_slugs: list[str] | None = None,
    workspace=None,
) -> list:
    """装配知识库下载工具与受 Agent 白名单控制的可选工具。

    KB 相关工具按可见性约束；LITE 下不装配 KB 部分。
    """
    if await _ensure_kb_manager_ready():
        extra = [_download_kb_file_tool(uid, knowledge_slugs)]
    else:
        extra = []
    extra.extend(
        await build_optional_tools(
            tool_slugs=tool_slugs,
            uid=uid,
            knowledge_slugs=knowledge_slugs,
            agent_id=agent_id,
            session_id=session_id,
            workspace=workspace,
        )
    )
    return extra


def _download_kb_file_tool(uid: str, knowledge_slugs: list[str] | None):
    """下载知识库文件工具（文本内联返回；二进制返回元数据与说明）。"""
    from agentscope.tool import FunctionTool

    async def download_kb_file(kb_id: str, file_id: str) -> str:
        """读取知识库文件内容。文本文件返回内容；其他类型返回文件信息。

        Args:
            kb_id: 知识库 ID
            file_id: 文件 ID
        """
        from yuxi.knowledge.runtime import knowledge_base

        target_error = await _check_target_visible(uid, knowledge_slugs, kb_id)
        if target_error:
            return target_error
        info = await knowledge_base.get_file_download(kb_id, file_id, variant="original")
        if not isinstance(info, dict):
            return _json(info)
        data = info.get("data")
        media_type = str(info.get("media_type") or "")
        inline = _textual_inline(data, media_type)
        if inline is not None:
            return inline
        return _json(
            {
                "file_id": file_id,
                "kb_id": kb_id,
                "media_type": media_type,
                "size_bytes": info.get("size_bytes"),
                "filename": info.get("filename"),
                "note": "非文本文件不支持内联读取，请引导用户在工作区文件列表中下载",
            }
        )

    return FunctionTool(
        download_kb_file,
        name="download_kb_file",
        description="读取知识库文件内容（文本内联，二进制返回信息）",
        is_read_only=True,
    )


def _present_artifacts_tool(workspace):
    """产出物展示工具：校验并持久化明确的 outputs 文件清单。"""
    from agentscope.tool import FunctionTool

    async def present_artifacts(filepaths: list[str]) -> str:
        """展示已生成的文件；filepaths 必须是 /workspace/outputs 下的文件。"""
        from pathlib import PurePosixPath

        if not filepaths:
            raise ValueError("至少需要一个产出文件")
        backend = workspace.get_backend()
        validated = []
        root = PurePosixPath("/workspace/outputs")
        for value in filepaths:
            path = PurePosixPath(value)
            if not path.is_absolute() or ".." in path.parts or path == root or not path.is_relative_to(root):
                raise ValueError(f"产出文件必须位于 /workspace/outputs: {value}")
            stat = await backend.stat(str(path))
            if stat is None or getattr(stat, "is_dir", False):
                raise ValueError(f"产出文件不存在或不是普通文件: {value}")
            validated.append(str(path))
        await backend.write_file(
            "/workspace/data/yuxi-artifacts.json",
            json.dumps({"filepaths": validated}, ensure_ascii=False).encode("utf-8"),
        )
        return _json({"filepaths": validated})

    return FunctionTool(
        present_artifacts,
        name="present_artifacts",
        description="向用户展示已生成的 /workspace/outputs 文件",
    )


def _ocr_parse_file_tool(
    uid: str | None = None,
    agent_id: str | None = None,
    session_id: str | None = None,
):
    """OCR 工具（经 PaddleOCR 服务解析图片文本；服务未配置时不装配）。"""
    import os

    from agentscope.tool import FunctionTool

    paddlex_uri = os.getenv("PADDLEX_URI")
    if not paddlex_uri:
        return None

    async def ocr_parse_file(
        file_path: str | None = None,
        image_base64: str | None = None,
    ) -> str:
        """对 workspace 图片或 base64 图片执行 OCR 并返回识别文本。

        Args:
            file_path: AgentScope workspace 内的绝对图片路径
            image_base64: 图片的 base64 编码内容（不含 data: 前缀）
        """
        if bool(file_path) == bool(image_base64):
            raise ValueError("file_path 和 image_base64 必须且只能提供一个")
        if file_path:
            from base64 import b64encode
            from pathlib import PurePosixPath

            from yuxi.agentscope.client import AgentScopeServiceClient

            path = PurePosixPath(file_path)
            if not path.is_absolute() or path.parts[:2] != ("/", "workspace") or ".." in path.parts:
                raise ValueError("OCR 文件路径必须位于 /workspace")
            if not uid or not agent_id or not session_id:
                raise ValueError("OCR workspace 路径缺少会话上下文")
            client = AgentScopeServiceClient(os.getenv("AGENTSCOPE_BASE_URL", "http://agentscope:8100"))
            data = await client.read_workspace_file(uid, agent_id, session_id, str(path))
            image_base64 = b64encode(data).decode("ascii")
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{paddlex_uri}/ocr",
                json={"image": image_base64},
            )
            resp.raise_for_status()
            return _json(resp.json())

    return FunctionTool(
        ocr_parse_file,
        name="ocr_parse_file",
        description="对 /workspace 图片路径或 base64 图片执行 OCR，返回识别文本",
        is_read_only=True,
    )
