"""代码管理的 MCP 内置资源目录。"""

DEFAULT_MCP_SERVERS = {
    "mcp-server-chart": {
        "command": "npx",
        "args": ["-y", "@antv/mcp-server-chart"],
        "transport": "stdio",
        "description": "图表生成工具，支持生成各类图表（柱状图、折线图、饼图等）",
        "icon": "📊",
        "tags": ["内置", "图表"],
    },
}
BUILTIN_MCP_SERVER_SLUGS = tuple(DEFAULT_MCP_SERVERS)
RETIRED_BUILTIN_MCP_SERVER_SLUGS = ("sequentialthinking",)
SYNCED_MCP_FIELDS = (
    "description",
    "transport",
    "url",
    "command",
    "args",
    "env",
    "headers",
    "timeout",
    "sse_read_timeout",
    "tags",
    "icon",
)


def is_builtin_mcp_slug(slug: str) -> bool:
    """判断 slug 是否来自代码内置 MCP 注册表。"""
    return slug in BUILTIN_MCP_SERVER_SLUGS
