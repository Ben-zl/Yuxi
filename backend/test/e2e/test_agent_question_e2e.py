"""主动提问从页面协议到 AgentScope 恢复的完整 E2E。"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import httpx
import pytest

from test.e2e.test_agent_async_e2e import _create_thread, _delete_agent, _iter_sse, _wait_for_run

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e]


async def _create_question_agent(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    uid: str,
    model_spec: str,
) -> str:
    """创建只开放主动提问工具的 mock 模型 Agent。"""
    slug = f"e2e-question-agent-{uuid.uuid4().hex[:8]}"
    context: dict[str, Any] = {
        "model": model_spec,
        "system_prompt": "需要用户选择时必须调用 ask_user_question；得到回答后简短确认。",
        "tools": ["ask_user_question"],
        "knowledges": [],
        "mcps": [],
        "skills": [],
        "subagents": [],
    }
    response = await client.post(
        "/api/agent",
        json={
            "name": f"E2E 主动提问 {slug[-8:]}",
            "slug": slug,
            "backend_id": "ChatbotAgent",
            "description": "主动提问完整链路测试",
            "config_json": {"context": context},
            "share_config": {
                "version": 2,
                "read_scope": {"access_level": "user", "department_ids": [], "user_uids": [uid]},
                "manage_scope": None,
            },
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return slug


async def _consume_until_end(client, headers, run_id) -> list[dict]:
    chunks = []
    async for event, envelope in _iter_sse(client, headers, run_id):
        if event == "messages":
            chunks.extend((envelope.get("payload") or {}).get("items") or [])
        if event == "end":
            break
    return chunks


async def test_question_survives_refresh_and_answer_resumes_same_reply(
    e2e_client: httpx.AsyncClient,
    e2e_headers: dict[str, str],
    e2e_agent_context: dict[str, str],
    e2e_mock_model_spec: str,
):
    uid = e2e_agent_context["uid"]
    agent_slug = await _create_question_agent(
        e2e_client,
        e2e_headers,
        uid,
        e2e_mock_model_spec,
    )
    thread_id = await _create_thread(e2e_client, e2e_headers, agent_slug)

    try:
        request_id = f"question-{uuid.uuid4()}"
        response = await e2e_client.post(
            "/api/agent/runs",
            json={
                "query": "这个任务需要确认方案后再继续",
                "agent_slug": agent_slug,
                "thread_id": thread_id,
                "meta": {"request_id": request_id},
            },
            headers=e2e_headers,
        )
        assert response.status_code == 200, response.text
        interrupted_run_id = str(response.json()["run_id"])
        chunks = await asyncio.wait_for(
            _consume_until_end(e2e_client, e2e_headers, interrupted_run_id),
            timeout=120,
        )
        question = next(chunk for chunk in chunks if chunk.get("status") == "ask_user_question_required")
        assert question["questions"][0]["question"] == "希望采用哪种交付方式？"

        interrupted_run = await _wait_for_run(e2e_client, e2e_headers, interrupted_run_id)
        assert interrupted_run["status"] == "interrupted"

        state_response = await e2e_client.get(
            f"/api/chat/thread/{thread_id}/state?include_messages=true",
            headers=e2e_headers,
        )
        assert state_response.status_code == 200, state_response.text
        restored = state_response.json()["interrupt"]
        assert restored["status"] == "ask_user_question_required"
        assert restored["questions"] == question["questions"]

        malformed = await e2e_client.post(
            "/api/agent/runs",
            json={
                "agent_slug": agent_slug,
                "thread_id": thread_id,
                "resume": {"answer": {}},
                "created_by_run_id": interrupted_run_id,
            },
            headers=e2e_headers,
        )
        assert malformed.status_code == 422, malformed.text

        resume_response = await e2e_client.post(
            "/api/agent/runs",
            json={
                "agent_slug": agent_slug,
                "thread_id": thread_id,
                "resume": {"answer": {"希望采用哪种交付方式？": "分步交付"}},
                "created_by_run_id": interrupted_run_id,
                "meta": {"request_id": f"question-resume-{uuid.uuid4()}"},
            },
            headers=e2e_headers,
        )
        assert resume_response.status_code == 200, resume_response.text
        resume_run_id = str(resume_response.json()["run_id"])
        await asyncio.wait_for(_consume_until_end(e2e_client, e2e_headers, resume_run_id), timeout=120)
        resumed_run = await _wait_for_run(e2e_client, e2e_headers, resume_run_id)
        assert resumed_run["status"] == "completed"

        final_state = await e2e_client.get(
            f"/api/chat/thread/{thread_id}/state?include_messages=true",
            headers=e2e_headers,
        )
        assert final_state.status_code == 200, final_state.text
        assert "interrupt" not in final_state.json()
        history = json.dumps(final_state.json().get("messages") or [], ensure_ascii=False)
        assert "文件已写入沙盒" in history
        assert "分步交付" in history
    finally:
        await _delete_agent(e2e_client, e2e_headers, agent_slug)
