"""AgentScope 上下文压缩与页面状态协议 E2E。"""

from __future__ import annotations

import uuid

import httpx
import pytest

from test.e2e.agentscope_e2e_fixtures import PROVIDER_ID, upsert_mock_provider
from test.e2e.test_agent_async_e2e import _create_thread, _delete_agent, _iter_sse, _wait_for_run
from yuxi.storage.postgres.manager import pg_manager

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e, pytest.mark.slow]


async def test_context_compression_streams_lifecycle_and_persists_summary(
    e2e_client: httpx.AsyncClient,
    e2e_headers: dict[str, str],
    e2e_agent_context: dict[str, str],
):
    """长输入触发原版 AgentScope 压缩，Yuxi 实时与刷新状态保持一致。"""
    uid = e2e_agent_context["uid"]
    pg_manager.initialize()
    async with pg_manager.get_async_session_context() as db:
        await upsert_mock_provider(db)
    refresh = await e2e_client.post(
        "/api/system/model-providers/models/cache/refresh",
        headers=e2e_headers,
    )
    assert refresh.status_code == 200, refresh.text

    slug = f"e2e-compression-{uuid.uuid4().hex[:8]}"
    response = await e2e_client.post(
        "/api/agent",
        json={
            "name": f"E2E 上下文压缩 {slug[-8:]}",
            "slug": slug,
            "backend_id": "ChatbotAgent",
            "description": "AgentScope 上下文压缩 E2E",
            "config_json": {
                "context": {
                    "model": f"{PROVIDER_ID}:mock-chat-model",
                    "system_prompt": "压缩长上下文后继续完成用户任务。",
                    "summary_trigger_ratio": 0.8,
                    "summary_reserve_ratio": 0.1,
                    "tools": [],
                    "knowledges": [],
                    "mcps": [],
                    "skills": [],
                    "subagents": [],
                }
            },
            "share_config": {
                "version": 2,
                "read_scope": {"access_level": "user", "department_ids": [], "user_uids": [uid]},
                "manage_scope": None,
            },
        },
        headers=e2e_headers,
    )
    assert response.status_code == 200, response.text
    thread_id = await _create_thread(e2e_client, e2e_headers, slug)

    try:
        long_context = "背景资料：" + "这是需要在摘要中保留的项目背景。" * 7000
        run_response = await e2e_client.post(
            "/api/agent/runs",
            json={
                "query": long_context,
                "agent_slug": slug,
                "thread_id": thread_id,
                "meta": {"request_id": f"compression-{uuid.uuid4()}"},
            },
            headers=e2e_headers,
        )
        assert run_response.status_code == 200, run_response.text
        run_id = str(run_response.json()["run_id"])

        chunks = []
        async for event, envelope in _iter_sse(e2e_client, e2e_headers, run_id):
            if event == "messages":
                chunks.extend((envelope.get("payload") or {}).get("items") or [])
            if event == "end":
                break

        run = await _wait_for_run(e2e_client, e2e_headers, run_id)
        assert run["status"] == "completed", run
        compression = [chunk["compression"] for chunk in chunks if chunk.get("status") == "context_compression"]
        assert [item["status"] for item in compression] == ["started", "completed"]

        state_response = await e2e_client.get(f"/api/chat/thread/{thread_id}/state", headers=e2e_headers)
        assert state_response.status_code == 200, state_response.text
        token_usage = state_response.json()["agent_state"]["token_usage"]
        assert token_usage["summary_active"] is True
        assert token_usage["summary_message_tokens"] > 0
        assert token_usage["context_window"] == 32768
    finally:
        await _delete_agent(e2e_client, e2e_headers, slug)
