"""长期记忆公开 API 的真实服务集成测试。"""

from __future__ import annotations

import uuid

import httpx
import pytest

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_disabled_memory_scope_can_be_listed_and_cleared(
    test_client: httpx.AsyncClient,
    admin_headers: dict[str, str],
):
    """Memory 默认关闭时，临时 Agent 的保留数据管理接口仍可用且清理幂等。"""
    slug = f"pytest-memory-{uuid.uuid4().hex[:10]}"
    created = await test_client.post(
        "/api/agent",
        headers=admin_headers,
        json={
            "name": "pytest memory agent",
            "slug": slug,
            "backend_id": "ChatbotAgent",
            "config_json": {},
        },
    )
    assert created.status_code == 200, created.text

    try:
        listed = await test_client.get(f"/api/agent/{slug}/memories", headers=admin_headers)
        assert listed.status_code == 200, listed.text
        payload = listed.json()
        assert payload["items"] == []
        assert payload["pagination"] == {"page": 1, "page_size": 20, "total": 0}
        assert payload["scope"]["enabled"] is False

        cleared = await test_client.delete(f"/api/agent/{slug}/memories", headers=admin_headers)
        assert cleared.status_code == 200, cleared.text
        assert cleared.json() == {"success": True}
    finally:
        deleted = await test_client.delete(f"/api/agent/{slug}", headers=admin_headers)
        assert deleted.status_code in (200, 404), deleted.text
