"""MCP 地址的凭据只保留在后端运行时，不进入脱敏响应。"""

import json

import pytest

from yuxi.storage.postgres.models_business import MCPServer


@pytest.mark.parametrize(
    "url",
    [
        "https://user:fixture-password@example.test/mcp",
        "https://fixture-user@example.test/mcp",
        "https://@example.test/mcp",
        "https://example.test/mcp?token=fixture-token",
        "https://example.test/mcp?region=public",
        "https://example.test/mcp#fixture-fragment",
        "https://example.test/mcp?",
        "https://example.test/mcp#",
        "https://[invalid-host/mcp",
    ],
)
def test_sensitive_or_unparseable_url_is_omitted(url):
    """即使 query 非秘密也整体隐藏，解析失败不能泄露原值。"""
    server = MCPServer(slug="test", name="Test", transport="streamable_http", url=url)
    result = server.to_dict(sanitize=True)
    assert "url" not in result
    assert url not in json.dumps(result)
    assert result["url_configured"] is True
    assert server.to_dict(sanitize=False)["url"] == url
    assert server.to_mcp_config()["url"] == url


@pytest.mark.parametrize("url", [None, "", "https://example.test/mcp", "https://example.test:8443/mcp"])
def test_plain_url_remains_metadata(url):
    """普通地址仍可展示，未配置状态不伪装成已有地址。"""
    server = MCPServer(slug="test", name="Test", transport="streamable_http", url=url)
    result = server.to_dict(sanitize=True)
    assert result["url"] == url
    assert result["url_configured"] is bool(url)
