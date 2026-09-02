"""ReMe 跨 Thread 真实模型召回端到端测试。"""

from __future__ import annotations

import asyncio
import json
import os
import uuid

import httpx
import pytest

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e, pytest.mark.slow]

RUN_TIMEOUT_SECONDS = int(os.getenv("E2E_RUN_TIMEOUT_SECONDS", "300"))
MEMORY_WAIT_SECONDS = int(os.getenv("E2E_MEMORY_WAIT_SECONDS", "120"))

requires_live_memory = pytest.mark.skipif(
    os.getenv("E2E_MEMORY_RECALL_ENABLED", "").lower() not in {"1", "true"},
    reason="未启用 E2E_MEMORY_RECALL_ENABLED，跳过真实模型长期记忆召回",
)


async def _create_user(client: httpx.AsyncClient, admin_headers: dict[str, str]) -> dict:
    """创建只服务于本用例的临时用户并返回登录态。"""
    profile = await client.get("/api/auth/me", headers=admin_headers)
    assert profile.status_code == 200, profile.text
    assert profile.json().get("role") in {"admin", "superadmin"}, "Memory E2E 需要管理员测试账号"

    departments = await client.get("/api/departments", headers=admin_headers)
    assert departments.status_code == 200 and departments.json(), departments.text
    password = f"Pw!{uuid.uuid4().hex[:12]}"
    created = await client.post(
        "/api/auth/users",
        headers=admin_headers,
        json={
            "username": f"e2e_memory_{uuid.uuid4().hex[:10]}",
            "password": password,
            "role": "user",
            "department_id": departments.json()[0]["id"],
        },
    )
    assert created.status_code == 200, created.text
    user = created.json()
    login = await client.post(
        "/api/auth/token",
        data={"username": user["uid"], "password": password},
    )
    assert login.status_code == 200, login.text
    return {
        "user": user,
        "headers": {"Authorization": f"Bearer {login.json()['access_token']}"},
    }


async def _create_thread(client: httpx.AsyncClient, headers: dict[str, str], agent_slug: str) -> str:
    """创建带清理标识的独立对话。"""
    response = await client.post(
        "/api/chat/thread",
        headers=headers,
        json={
            "agent_id": agent_slug,
            "title": f"memory-recall-e2e-{uuid.uuid4().hex[:8]}",
            "metadata": {"_yuxi_e2e": True, "test": "memory-recall-e2e"},
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    thread_id = payload.get("thread_id") or payload.get("id")
    assert thread_id, payload
    return str(thread_id)


async def _run(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    *,
    agent_slug: str,
    thread_id: str,
    query: str,
) -> dict:
    """提交真实 AgentRun 并轮询其公开结果。"""
    response = await client.post(
        "/api/agent/runs",
        headers=headers,
        json={
            "query": query,
            "agent_slug": agent_slug,
            "thread_id": thread_id,
            "meta": {"request_id": f"memory-recall-e2e-{uuid.uuid4()}"},
        },
    )
    assert response.status_code == 200, response.text
    run_id = str(response.json().get("run_id") or "")
    assert run_id, response.text

    deadline = asyncio.get_running_loop().time() + RUN_TIMEOUT_SECONDS
    while asyncio.get_running_loop().time() < deadline:
        result = await client.get(f"/api/agent/runs/{run_id}/result", headers=headers)
        if result.status_code == 200:
            payload = result.json()
            if payload.get("status") in {"completed", "failed", "cancelled", "interrupted"}:
                assert payload.get("status") == "completed", payload
                return payload
        else:
            assert result.status_code in {202, 404}, result.text
        await asyncio.sleep(2)
    pytest.fail(f"Memory E2E AgentRun 超时: {run_id}")


async def _wait_for_memory(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    agent_slug: str,
    marker: str,
) -> None:
    """等待 ReMe 完成异步卡片和索引写入。"""
    deadline = asyncio.get_running_loop().time() + MEMORY_WAIT_SECONDS
    last_payload: dict = {}
    while asyncio.get_running_loop().time() < deadline:
        response = await client.get(f"/api/agent/{agent_slug}/memories", headers=headers)
        assert response.status_code in {200, 409}, response.text
        if response.status_code == 200:
            last_payload = response.json()
            if marker in json.dumps(last_payload.get("items") or [], ensure_ascii=False):
                return
        await asyncio.sleep(2)
    pytest.fail(f"ReMe 未在期限内写入测试事实: {last_payload}")


@requires_live_memory
async def test_real_model_recalls_unique_fact_across_threads(
    e2e_client: httpx.AsyncClient,
    e2e_headers: dict[str, str],
):
    """Thread A 写入的唯一事实必须能在同 Agent 的 Thread B 被真实模型召回。"""
    target = await _create_user(e2e_client, e2e_headers)
    headers = target["headers"]
    agent_slug = "default-chatbot"
    marker = f"REME_E2E_{uuid.uuid4().hex.upper()}"
    thread_ids: list[str] = []

    try:
        enabled = await e2e_client.put(
            "/api/user/config",
            headers=headers,
            json={"enable_memory": True},
        )
        assert enabled.status_code == 200, enabled.text
        assert enabled.json().get("enable_memory") is True

        write_thread = await _create_thread(e2e_client, headers, agent_slug)
        thread_ids.append(write_thread)
        await _run(
            e2e_client,
            headers,
            agent_slug=agent_slug,
            thread_id=write_thread,
            query=f"请把这个稳定项目代号记入长期记忆，并确认已记住：{marker}",
        )
        await _wait_for_memory(e2e_client, headers, agent_slug, marker)

        recall_thread = await _create_thread(e2e_client, headers, agent_slug)
        thread_ids.append(recall_thread)
        recalled = await _run(
            e2e_client,
            headers,
            agent_slug=agent_slug,
            thread_id=recall_thread,
            query="请搜索长期记忆，只回复此前记录的稳定项目代号。",
        )

        assert marker in json.dumps(recalled.get("output"), ensure_ascii=False), recalled
    finally:
        for thread_id in thread_ids:
            response = await e2e_client.delete(f"/api/chat/thread/{thread_id}", headers=headers)
            assert response.status_code in {200, 404}, response.text
        response = await e2e_client.delete(
            f"/api/auth/users/{target['user']['id']}",
            headers=e2e_headers,
        )
        assert response.status_code in {200, 404}, response.text
