"""yuxi 工具集的 agentscope 适配（迁移工单 07）。

知识库工具与网页搜索从旧 langchain 工具体系移植为 agentscope
FunctionTool：核心调用 KnowledgeBaseManager 的公开方法；可见性沿用
「用户权限（get_databases_by_uid）∩ 会话启用集（knowledge_slugs）」语义。
LITE 模式或知识库管理器不可用时不装配知识库工具。
"""

import json

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
    """构建网页搜索工具（provider 未配置时返回 None，不装配）。"""
    import os

    if not (os.getenv("DOUBAO_SEARCH_API_KEY") or os.getenv("TAVILY_API_KEY")):
        return None
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


async def build_extra_tools(*, uid: str, knowledge_slugs: list[str] | None, agent_id: str, session_id: str) -> list:
    """装配补充工具：download_kb_file / present_artifacts / ocr_parse_file。

    KB 相关工具按可见性约束；LITE 下不装配 KB 部分。
    """
    if await _ensure_kb_manager_ready():
        extra = [_download_kb_file_tool(uid, knowledge_slugs)]
    else:
        extra = []
    extra.append(_present_artifacts_tool(uid, agent_id, session_id))
    ocr_tool = _ocr_parse_file_tool()
    if ocr_tool is not None:
        extra.append(ocr_tool)
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
        # 文本类内容直接内联（模型可读）；二进制返回元数据
        if (
            data is not None
            and media_type.startswith("text/")
            or media_type
            in (
                "application/json",
                "text/markdown",
            )
        ):
            try:
                return data.decode("utf-8") if isinstance(data, bytes) else str(data)
            except UnicodeDecodeError:
                pass
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


def _present_artifacts_tool(uid: str, agent_id: str, session_id: str):
    """产出物展示工具：列出线程工作区 outputs 目录的文件。"""
    import os

    from agentscope.tool import FunctionTool

    base_url = os.getenv("AGENTSCOPE_BASE_URL", "http://agentscope:8100")

    async def present_artifacts() -> str:
        """列出工作区产出目录（/workspace/outputs）中的文件，供用户查看与下载。"""
        try:
            async with httpx.AsyncClient(base_url=base_url, timeout=30.0) as http:
                resp = await http.get(
                    "/workspace/directories",
                    params={
                        "agent_id": agent_id,
                        "session_id": session_id,
                        "path": "/workspace/outputs",
                    },
                    headers={"X-User-ID": uid},
                )
                resp.raise_for_status()
                return _json(resp.json())
        except httpx.HTTPError as exc:
            return f"读取产出目录失败：{exc}"

    return FunctionTool(
        present_artifacts,
        name="present_artifacts",
        description="列出智能体生成的产出文件（用户可在工作区下载）",
        is_read_only=True,
    )


def _ocr_parse_file_tool():
    """OCR 工具（经 PaddleOCR 服务解析图片文本；服务未配置时不装配）。"""
    import os

    from agentscope.tool import FunctionTool

    paddlex_uri = os.getenv("PADDLEX_URI")
    if not paddlex_uri:
        return None

    async def ocr_parse_file(image_base64: str) -> str:
        """对图片执行 OCR 并返回识别文本。

        Args:
            image_base64: 图片的 base64 编码内容（不含 data: 前缀）
        """
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
        description="对图片执行 OCR 识别，返回图片中的文本",
        is_read_only=True,
    )
