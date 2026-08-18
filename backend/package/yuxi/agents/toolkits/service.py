"""内置工具的管理面目录；运行时工具由 yuxi.agentscope.tools 构建。"""

from copy import deepcopy

_BUILTIN_TOOLS = [
    {
        "slug": "web_search",
        "name": "网页搜索",
        "description": "搜索互联网获取最新信息",
        "metadata": {},
        "args": [],
        "category": "buildin",
        "tags": ["搜索"],
        "config_guide": "配置 DOUBAO_SEARCH_API_KEY 或 TAVILY_API_KEY 后启用",
    },
    {
        "slug": "present_artifacts",
        "name": "展示交付物",
        "description": "展示工作区 outputs 目录中的交付物",
        "metadata": {},
        "args": [],
        "category": "buildin",
        "tags": ["文件"],
        "config_guide": "",
    },
    {
        "slug": "ocr_parse_file",
        "name": "OCR 解析文件",
        "description": "将工作区文档解析为 Markdown",
        "metadata": {},
        "args": [],
        "category": "buildin",
        "tags": ["文件", "OCR"],
        "config_guide": "",
    },
    {
        "slug": "read_media",
        "name": "读取图片/PDF",
        "description": "读取工作区图片或文本型 PDF，扫描件可转交 OCR",
        "metadata": {},
        "args": [],
        "category": "buildin",
        "tags": ["文件", "多模态"],
        "config_guide": "",
    },
    {
        "slug": "ask_user_question",
        "name": "向用户提问",
        "description": "在需要补充信息时请求用户输入",
        "metadata": {},
        "args": [],
        "category": "buildin",
        "tags": ["交互"],
        "config_guide": "",
    },
]

_metadata_cache: list[dict] = []


def get_tool_metadata(category: str | None = None) -> list[dict]:
    """返回稳定的内置工具元数据，不触发运行时模块导入。"""
    if not _metadata_cache:
        _metadata_cache.extend(deepcopy(_BUILTIN_TOOLS))
    if category:
        return [item for item in _metadata_cache if item.get("category") == category]
    return list(_metadata_cache)
