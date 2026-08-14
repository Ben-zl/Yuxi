"""e2e 专用 MCP 服务器（streamable-http）。

以独立容器运行在 compose 网络内，暴露 echo 工具，供 agentscope 的
MCPClient 真实连接与调用。
"""

import os

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("e2e-echo-mcp", host="0.0.0.0")


@mcp.tool()
def echo(text: str) -> str:
    """原样返回输入文本。"""
    return f"echo: {text}"


if __name__ == "__main__":
    mcp.settings.port = int(os.getenv("MCP_PORT", "9000"))
    mcp.run(transport="streamable-http")
