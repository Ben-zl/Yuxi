"""暂停专用槽位 worker 后验证提交快照；READY 后恢复 worker 完成真实执行。"""

import asyncio
import json
import os
from uuid import uuid4

import asyncpg
import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


async def test_resource_snapshot_survives_permission_and_credential_change(e2e_client, e2e_headers):
    """真实 HTTP 提交、旧权限撤销、独立 worker 和同一 Run PostgreSQL 终态回读。"""
    if os.getenv("E2E_SNAPSHOT_PAUSED_WORKER") != "1":
        pytest.skip("仅在独立槽位暂停 worker 后运行；见测试文档")
    client, root = e2e_client, e2e_headers
    suffix = uuid4().hex[:8]
    provider_id = thread_id = agent_slug = department_id = admin_id = run_id = None
    db = await asyncpg.connect(os.environ["POSTGRES_URL"].replace("+asyncpg", ""))
    try:
        login_name, password = f"snap_{suffix}", f"Snapshot!{suffix}"
        response = await client.post(
            "/api/departments",
            headers=root,
            json={
                "name": f"snapshot-{suffix}",
                "admin_uid": login_name,
                "admin_password": password,
            },
        )
        assert response.status_code == 201, response.text
        department_id = response.json()["id"]
        response = await client.post("/api/auth/token", data={"username": login_name, "password": password})
        assert response.status_code == 200
        admin_id = response.json()["user_id"]
        user_headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
        share = {
            "version": 2,
            "read_scope": {
                "access_level": "department",
                "department_ids": [department_id],
                "user_uids": [],
            },
            "manage_scope": None,
        }
        response = await client.post(
            "/api/system/model-providers",
            headers=root,
            json={
                "provider_id": f"snapshot-{suffix}",
                "display_name": "Snapshot E2E",
                "base_url": "http://openai-mock:8080/v1",
                "api_key": "snapshot-fixture-key",
                "capabilities": ["chat"],
                "enabled_models": [{"id": "snapshot-proof", "type": "chat"}],
                "share_config": share,
            },
        )
        assert response.status_code == 200, response.text
        provider_id = response.json()["data"]["resource_id"]
        spec = f"{provider_id}:snapshot-proof"
        agent_slug = f"e2e-snapshot-{suffix}"
        response = await client.post(
            "/api/agent",
            headers=user_headers,
            json={
                "slug": agent_slug,
                "name": "Snapshot E2E",
                "backend_id": "ChatbotAgent",
                "share_config": share,
                "config_json": {
                    "context": {
                        "model": spec,
                        "system_prompt": "Reply briefly.",
                        "tools": [],
                        "mcps": [],
                        "skills": [],
                        "knowledges": [],
                        "subagents": [],
                    }
                },
            },
        )
        assert response.status_code == 200, response.text
        response = await client.post(
            "/api/chat/thread",
            headers=user_headers,
            json={
                "agent_id": agent_slug,
                "title": f"snapshot-{suffix}",
            },
        )
        assert response.status_code == 200, response.text
        thread_id = response.json().get("thread_id") or response.json()["id"]
        response = await client.post(
            "/api/agent/runs",
            headers=user_headers,
            json={
                "agent_slug": agent_slug,
                "thread_id": thread_id,
                "query": "snapshot proof",
                "meta": {"request_id": f"snapshot-{suffix}"},
            },
        )
        assert response.status_code == 200, response.text
        run_id = response.json()["run_id"]
        row = await db.fetchrow("SELECT status, manifest, manifest_fingerprint FROM agent_runs WHERE id=$1", run_id)
        assert row["status"] == "pending", "测试必须在 worker 暂停时提交"
        original_fingerprint = row["manifest_fingerprint"]
        manifest = json.loads(row["manifest"]) if isinstance(row["manifest"], str) else row["manifest"]
        assert manifest["runtime_snapshot"]["id"]
        assert "snapshot-fixture-key" not in json.dumps(manifest)
        response = await client.put(
            f"/api/system/model-providers/{provider_id}",
            headers=root,
            json={
                "api_key": "replacement-key",
                "base_url": "http://127.0.0.1:1/v1",
                "share_config": {
                    "version": 2,
                    "read_scope": {
                        "access_level": "department",
                        "department_ids": [1],
                        "user_uids": [],
                    },
                    "manage_scope": None,
                },
            },
        )
        assert response.status_code == 200, response.text
        rejected_request_id = f"snapshot-rejected-{suffix}"
        rejected = await client.post(
            "/api/agent/runs",
            headers=user_headers,
            json={
                "agent_slug": agent_slug,
                "thread_id": thread_id,
                "query": "must reject",
                "meta": {"request_id": rejected_request_id},
            },
        )
        assert rejected.status_code == 422, rejected.text
        assert rejected.json() == {"detail": f"模型资源不可访问: '{spec}'"}
        assert (
            await db.fetchval("SELECT count(*) FROM agent_run_requests WHERE request_id=$1", rejected_request_id) == 0
        )
        assert await db.fetchval("SELECT count(*) FROM agent_runs WHERE request_id=$1", rejected_request_id) == 0
        print("SNAPSHOT_READY_TO_RESUME_WORKER", flush=True)
        for _ in range(120):
            row = await db.fetchrow(
                "SELECT status, error_message, manifest_fingerprint, output_message_id FROM agent_runs WHERE id=$1",
                run_id,
            )
            if row["status"] in {"completed", "failed", "cancelled", "interrupted"}:
                break
            await asyncio.sleep(1)
        assert row["status"] == "completed", (row["status"], row["error_message"])
        output = await db.fetchrow("SELECT run_id, content FROM messages WHERE id=$1", row["output_message_id"])
        assert output["run_id"] == run_id
        assert "e2e mock" in output["content"]
        assert row["manifest_fingerprint"] == original_fingerprint
    finally:
        if run_id:
            await client.post(f"/api/agent/runs/{run_id}/cancel", headers=user_headers)
        if thread_id:
            await client.delete(f"/api/chat/thread/{thread_id}", headers=user_headers)
        if agent_slug:
            await client.delete(f"/api/agent/{agent_slug}", headers=root)
        if provider_id:
            await client.delete(f"/api/system/model-providers/{provider_id}", headers=root)
        if department_id:
            await client.delete(f"/api/departments/{department_id}", headers=root)
        if admin_id:
            await client.delete(f"/api/auth/users/{admin_id}", headers=root)
        if run_id:
            await db.execute("DELETE FROM run_resource_snapshots WHERE uid=$1", login_name)
        await db.close()
