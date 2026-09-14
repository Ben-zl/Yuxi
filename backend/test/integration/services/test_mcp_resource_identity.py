"""真实 MCP 协议验证同名服务器的客户端与工具身份。"""

import os
from uuid import uuid4
from types import SimpleNamespace

import pytest

from yuxi.agents.mcp.service import clear_mcp_server_tools_cache
from yuxi.agentscope.tools import build_mcp_tools


@pytest.mark.integration
async def test_same_slug_tools_remain_distinct_over_real_mcp():
    """相同逻辑名的两个资源可同时装配和调用，不被工具字典覆盖。"""
    resource_ids = [str(uuid4()), str(uuid4())]
    try:
        tools = await build_mcp_tools(
            mcp_servers=[
                {
                    "resource_id": resource_id,
                    "slug": "same-logical-name",
                    "transport": "streamable_http",
                    "url": os.getenv("MCP_MOCK_URL", "http://mcp-mock:9000/mcp"),
                }
                for resource_id in resource_ids
            ],
            user=SimpleNamespace(uid="mcp-integration"),
        )
        assert len(tools) == 2
        assert len({tool.name: tool for tool in tools}) == 2
        for resource_id, tool in zip(resource_ids, tools, strict=True):
            assert resource_id in tool.name
            result = await tool(text=resource_id)
            assert f"echo: {resource_id}" in str(result)
    finally:
        for resource_id in resource_ids:
            clear_mcp_server_tools_cache(resource_id)
