"""yuxi 工具集的 agentscope 适配（迁移工单 07）。

知识库工具与网页搜索从旧 langchain 工具体系移植为 agentscope
FunctionTool：核心调用 KnowledgeBaseManager 的公开方法；可见性沿用
「用户权限（get_databases_by_uid）∩ 会话启用集（knowledge_slugs）」语义。
LITE 模式或知识库管理器不可用时不装配知识库工具。
"""

import json

import httpx

from yuxi.agentscope.projection import is_lite_mode
from yuxi.utils import logger

# KB 管理器初始化只尝试一次（与 api 侧 lifespan 语义一致：失败不阻断服务）
_kb_initialize_attempted = False


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
        {"kb_id": s.kb_id, "name": s.name, "description": s.description, "kb_type": s.kb_type}
        for s in summaries
    ]
    if knowledge_slugs is not None:
        enabled = set(knowledge_slugs)
        visible = [kb for kb in visible if kb["kb_id"] in enabled or kb["name"] in enabled]
    return visible


async def _ensure_kb_manager_ready() -> bool:
    """确保知识库管理器已初始化；失败或 LITE 下返回 False（不装配 KB 工具）。"""
    global _kb_initialize_attempted
    if is_lite_mode():
        return False
    if not _kb_initialize_attempted:
        _kb_initialize_attempted = True
        try:
            from yuxi.knowledge.runtime import knowledge_base

            await knowledge_base.initialize()
        except Exception as exc:  # noqa: BLE001 - 与 lifespan 同语义：记录并降级
            logger.error(f"agentscope 侧知识库管理器初始化失败，本轮不装配 KB 工具: {exc}")
            return False
    return True


async def build_kb_tools(
    *, uid: str, knowledge_slugs: list[str] | None
) -> list:
    """构建知识库工具集；不可用（LITE/初始化失败/无用户）时返回空集。"""
    if not await _ensure_kb_manager_ready():
        return []

    from yuxi.knowledge.runtime import knowledge_base

    async def list_kbs() -> str:
        """列出当前用户可见的知识库（返回 kb_id、名称与描述）。"""
        visible = await _visible_knowledge_bases(uid, knowledge_slugs)
        return _json(
            [{"kb_id": kb["kb_id"], "name": kb["name"], "description": kb["description"]} for kb in visible]
        )

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

    async def open_kb_document(
        kb_id: str, file_id: str, offset: int = 0, window_size: int = 200
    ) -> str:
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
        result = await knowledge_base.open_document(
            kb_id, file_id, offset=offset, limit=window_size
        )
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


async def _check_target_visible(
    uid: str, knowledge_slugs: list[str] | None, kb_id: str
) -> str | None:
    """校验 kb_id 对当前用户可见；不可见时返回错误文本。"""
    visible = await _visible_knowledge_bases(uid, knowledge_slugs)
    if not any(kb["kb_id"] == kb_id for kb in visible):
        return f"知识库 {kb_id} 不存在或不可见"
    return None


async def web_search(query: str, count: int = 10) -> str:
    """网页搜索（豆包 provider；返回标题、链接与摘要）。

    Args:
        query: 搜索关键词
        count: 结果条数
    """
    import os

    api_key = os.getenv("DOUBAO_SEARCH_API_KEY")
    if not api_key:
        return "web_search 未配置（缺少 DOUBAO_SEARCH_API_KEY）"
    payload = {
        "query": query,
        "count": count,
        "content_format": "text",
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            "https://open.feedcoopapi.com/search_api/web_search",
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
        )
        resp.raise_for_status()
    return _json(resp.json())


def build_web_search_tool():
    """构建网页搜索工具（provider 未配置时返回 None，不装配）。"""
    import os

    if not os.getenv("DOUBAO_SEARCH_API_KEY"):
        # 仅支持豆包 provider；Tavily 的 REST 实现见切换门禁工具面差额
        return None
    from agentscope.tool import FunctionTool

    return FunctionTool(web_search, name="web_search", description="搜索互联网获取最新信息")


async def bind_thread_mcps(
    db,
    client,
    *,
    uid: str,
    mcp_server_names: list[str] | None,
    agent_id: str,
    session_id: str,
) -> int:
    """把 yuxi 的 MCP 服务器投影绑定到会话 workspace（会话创建期一次性）。

    仅投影启用且 sse/streamable_http 的服务器（stdio 约束在 yuxi 源头
    强制：用户不可创建 stdio）；重名冲突视为已绑定跳过。
    """
    from sqlalchemy import select

    from yuxi.agentscope.client import AgentScopeServiceError
    from yuxi.storage.postgres.models_business import MCPServer

    stmt = select(MCPServer)
    if mcp_server_names is not None:
        if not mcp_server_names:
            return 0
        stmt = stmt.where(MCPServer.slug.in_(mcp_server_names))
    rows = (await db.execute(stmt)).scalars().all()

    bound = 0
    for row in rows:
        if not row.enabled or row.transport not in ("sse", "streamable_http") or not row.url:
            continue
        mcp_config = {"type": "http_mcp", "url": row.url}
        if row.headers:
            mcp_config["headers"] = row.headers
        if row.timeout:
            mcp_config["timeout"] = float(row.timeout)
        payload = {
            "name": row.slug,
            "is_stateful": False,
            "mcp_config": mcp_config,
        }
        if row.disabled_tools:
            payload["disable_tools"] = list(row.disabled_tools)
        try:
            await client.add_workspace_mcp(uid, agent_id, session_id, payload)
            bound += 1
        except AgentScopeServiceError as exc:
            if exc.status_code == 409:
                continue  # 同名 MCP 已绑定
            raise
    return bound
