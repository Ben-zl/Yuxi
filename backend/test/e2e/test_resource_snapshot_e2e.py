"""暂停专用槽位 worker 后验证提交快照；READY 后恢复 worker 完成真实执行。"""

import asyncio
import json
import os
import shutil
from uuid import uuid4

import asyncpg
import pytest

from test.e2e.agentscope_e2e_fixtures import cleanup_test_users, run_e2e_cleanup_steps
from test.live_api_cleanup import (
    make_test_conversation_metadata,
    make_test_conversation_title,
    make_test_resource_id,
)
from yuxi.config import get_skill_data_dir, get_user_data_dir
from yuxi.storage.postgres.manager import pg_manager

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


async def test_resource_snapshot_survives_permission_and_credential_change(e2e_client, e2e_headers):
    """真实 HTTP 提交、旧权限撤销、独立 worker 和同一 Run PostgreSQL 终态回读。"""
    if os.getenv("E2E_SNAPSHOT_PAUSED_WORKER") != "1":
        pytest.skip("仅在独立槽位暂停 worker 后运行；见测试文档")
    client, root = e2e_client, e2e_headers
    suffix = uuid4().hex[:8]
    provider_id = thread_id = agent_slug = skill_slug = department_id = admin_id = run_id = None
    login_name = None
    user_headers = None
    skill_dir = None
    primary_error: BaseException | None = None
    db = await asyncpg.connect(os.environ["POSTGRES_URL"].replace("+asyncpg", ""))
    try:
        login_name, password = f"e2e_snap_{suffix}", f"Snapshot!{suffix}"
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
        skill_slug = f"snapshot-skill-{suffix}"
        skill_dir = get_skill_data_dir() / "shared" / skill_slug
        (skill_dir / "scripts").mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            (
                "---\n"
                f"slug: {skill_slug}\n"
                f"name: {skill_slug}\n"
                "description: Run snapshot skill\n"
                "---\n\n"
                "submitted skill content\n"
            ),
            encoding="utf-8",
        )
        script_path = skill_dir / "scripts" / "run.sh"
        script_path.write_text("#!/bin/sh\necho submitted\n", encoding="utf-8")
        script_path.chmod(0o755)
        await db.execute(
            """
            INSERT INTO skills (
                slug,
                name,
                description,
                source_type,
                tool_dependencies,
                mcp_dependencies,
                skill_dependencies,
                dir_path,
                version,
                content_hash,
                share_config,
                enabled,
                created_by,
                updated_by
            ) VALUES (
                $1,
                $1,
                'Run snapshot skill',
                'upload',
                '[]'::json,
                '[]'::json,
                '[]'::json,
                $2,
                '1',
                'submitted',
                $3::jsonb,
                TRUE,
                'e2e',
                'e2e'
            )
            """,
            skill_slug,
            f"shared/{skill_slug}",
            json.dumps(share),
        )
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
                        "skills": [skill_slug],
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
                "title": make_test_conversation_title("resource-snapshot"),
                "metadata": make_test_conversation_metadata("resource-snapshot", e2e=True),
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
                "meta": {"request_id": make_test_resource_id("resource-snapshot")},
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
        await db.execute(
            "UPDATE skills SET share_config=$1::jsonb WHERE slug=$2",
            json.dumps(
                {
                    "version": 2,
                    "read_scope": {
                        "access_level": "department",
                        "department_ids": [1],
                        "user_uids": [],
                    },
                    "manage_scope": None,
                }
            ),
            skill_slug,
        )
        shutil.rmtree(skill_dir)
        rejected_skill_request_id = make_test_resource_id("snapshot-skill-rejected")
        rejected_skill = await client.post(
            "/api/agent/runs",
            headers=user_headers,
            json={
                "agent_slug": agent_slug,
                "thread_id": thread_id,
                "query": "must reject revoked skill",
                "meta": {"request_id": rejected_skill_request_id},
            },
        )
        assert rejected_skill.status_code == 422, rejected_skill.text
        assert skill_slug in rejected_skill.text
        assert (
            await db.fetchval(
                "SELECT count(*) FROM agent_run_requests WHERE request_id=$1",
                rejected_skill_request_id,
            )
            == 0
        )
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
        rejected_request_id = make_test_resource_id("snapshot-model-rejected")
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
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        cleanup_steps = []

        if run_id and user_headers:

            async def cancel_run() -> None:
                response = await client.post(f"/api/agent/runs/{run_id}/cancel", headers=user_headers)
                assert response.status_code in {200, 404}, response.text
                for _ in range(100):
                    status = await db.fetchval("SELECT status FROM agent_runs WHERE id=$1", run_id)
                    if status is None or status in {"completed", "failed", "cancelled", "interrupted"}:
                        return
                    await asyncio.sleep(0.1)
                raise AssertionError(f"Run cleanup did not reach a terminal status: {run_id}")

            cleanup_steps.append(("cancel snapshot run", cancel_run))

        if thread_id and user_headers:

            async def delete_thread() -> None:
                response = await client.delete(f"/api/chat/thread/{thread_id}", headers=user_headers)
                assert response.status_code in {200, 404}, response.text

            cleanup_steps.append(("delete snapshot thread", delete_thread))

        if agent_slug:

            async def delete_agent() -> None:
                response = await client.delete(f"/api/agent/{agent_slug}", headers=root)
                assert response.status_code in {200, 404}, response.text

            cleanup_steps.append(("delete snapshot agent", delete_agent))

        if provider_id:

            async def delete_provider() -> None:
                response = await client.delete(f"/api/system/model-providers/{provider_id}", headers=root)
                assert response.status_code in {200, 404}, response.text

            cleanup_steps.append(("delete snapshot provider", delete_provider))

        if skill_slug:

            async def delete_skill() -> None:
                await db.execute("DELETE FROM skills WHERE slug=$1", skill_slug)

            cleanup_steps.append(("delete snapshot skill row", delete_skill))

        if skill_dir:

            async def delete_skill_dir() -> None:
                if skill_dir.exists():
                    shutil.rmtree(skill_dir)

            cleanup_steps.append(("delete snapshot skill directory", delete_skill_dir))

        if department_id:

            async def delete_department() -> None:
                response = await client.delete(f"/api/departments/{department_id}", headers=root)
                assert response.status_code in {200, 404}, response.text

            cleanup_steps.append(("delete snapshot department", delete_department))

        if admin_id:

            async def delete_admin() -> None:
                response = await client.delete(f"/api/auth/users/{admin_id}", headers=root)
                assert response.status_code in {200, 404}, response.text

            cleanup_steps.append(("soft-delete snapshot admin", delete_admin))

        if login_name:

            async def delete_snapshots() -> None:
                await db.execute("DELETE FROM run_resource_snapshots WHERE uid=$1", login_name)

            cleanup_steps.append(("delete snapshot payload rows", delete_snapshots))

        if login_name:

            async def cleanup_user_rows() -> None:
                pg_manager.initialize()
                await pg_manager.ensure_business_schema()
                async with pg_manager.get_async_session_context() as session:
                    await cleanup_test_users(session, login_name)

            cleanup_steps.append(("physically delete snapshot user resources", cleanup_user_rows))

            async def verify_cleanup() -> None:
                assert await db.fetchval("SELECT count(*) FROM users WHERE uid=$1", login_name) == 0
                assert (
                    await db.fetchval(
                        "SELECT count(*) FROM agentscope_thread_sessions WHERE uid=$1",
                        login_name,
                    )
                    == 0
                )
                assert await db.fetchval("SELECT count(*) FROM user_config WHERE uid=$1", login_name) == 0
                assert await db.fetchval("SELECT count(*) FROM run_resource_snapshots WHERE uid=$1", login_name) == 0
                if thread_id:
                    assert (
                        await db.fetchval(
                            "SELECT count(*) FROM conversations WHERE thread_id=$1",
                            thread_id,
                        )
                        == 0
                    )
                    assert (
                        await db.fetchval(
                            "SELECT count(*) FROM agent_runs WHERE conversation_thread_id=$1",
                            thread_id,
                        )
                        == 0
                    )
                    assert (
                        await db.fetchval(
                            "SELECT count(*) FROM agent_run_requests WHERE conversation_thread_id=$1",
                            thread_id,
                        )
                        == 0
                    )
                if department_id:
                    assert (
                        await db.fetchval(
                            "SELECT count(*) FROM departments WHERE id=$1",
                            department_id,
                        )
                        == 0
                    )
                assert not (get_user_data_dir() / "shared" / login_name).exists()

            cleanup_steps.append(("verify snapshot cleanup", verify_cleanup))

        async def close_database() -> None:
            await db.close()

        cleanup_steps.append(("close snapshot database connection", close_database))
        await run_e2e_cleanup_steps(cleanup_steps, primary_error=primary_error)
