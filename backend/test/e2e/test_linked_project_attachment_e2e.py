"""linked Project 附件必须贯通 API、Worker 和 AgentScope workspace。"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from agentscope_e2e_fixtures import run_e2e_cleanup_steps
from e2e_helpers import cancel_run, consume_events, wait_for_run
from test.live_api_cleanup import (
    make_test_conversation_metadata,
    make_test_conversation_title,
    make_test_resource_id,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e, pytest.mark.slow]


async def test_linked_project_attachment_materializes_in_worker(
    tmp_path: Path,
    e2e_client: httpx.AsyncClient,
    e2e_headers: dict[str, str],
    e2e_mock_model_spec: str,
) -> None:
    """非 projects 前缀的 Workdir 附件也必须完成真实 Run。"""
    suffix = uuid4().hex[:8]
    directory_name = f"e2e-linked-{suffix}"
    project_id = agent_slug = thread_id = run_id = None
    primary_error: BaseException | None = None

    me_response = await e2e_client.get("/api/auth/me", headers=e2e_headers)
    assert me_response.status_code == 200, me_response.text
    uid = str(me_response.json()["uid"])

    directory_response = await e2e_client.post(
        "/api/workspace/directory",
        headers=e2e_headers,
        json={"parent_path": "/", "name": directory_name},
    )
    assert directory_response.status_code == 200, directory_response.text

    try:
        project_response = await e2e_client.post(
            "/api/projects",
            headers=e2e_headers,
            json={
                "request_id": make_test_resource_id("linked-project"),
                "name": f"Linked {suffix}",
                "workdir": {"mode": "linked", "path": directory_name},
            },
        )
        assert project_response.status_code == 200, project_response.text
        project_id = str(project_response.json()["id"])

        agent_slug = f"e2e-linked-agent-{suffix}"
        agent_response = await e2e_client.post(
            "/api/agent",
            headers=e2e_headers,
            json={
                "slug": agent_slug,
                "name": f"Linked attachment {suffix}",
                "backend_id": "ChatbotAgent",
                "config_json": {
                    "context": {
                        "model": e2e_mock_model_spec,
                        "system_prompt": "Reply briefly.",
                        "tools": [],
                        "skills": [],
                        "mcps": [],
                        "knowledges": [],
                        "subagents": [],
                    }
                },
                "share_config": {
                    "version": 2,
                    "read_scope": {
                        "access_level": "user",
                        "department_ids": [],
                        "user_uids": [uid],
                    },
                    "manage_scope": None,
                },
            },
        )
        assert agent_response.status_code == 200, agent_response.text

        thread_response = await e2e_client.post(
            "/api/chat/thread",
            headers=e2e_headers,
            json={
                "agent_id": agent_slug,
                "project_id": project_id,
                "title": make_test_conversation_title("linked-attachment-e2e"),
                "metadata": make_test_conversation_metadata("linked-attachment-e2e", e2e=True),
            },
        )
        assert thread_response.status_code == 200, thread_response.text
        thread_id = str(thread_response.json().get("thread_id") or thread_response.json()["id"])
        assert thread_response.json()["workdir_path"] == directory_name

        attachment_path = tmp_path / "linked-proof.txt"
        attachment_path.write_text("linked project attachment proof", encoding="utf-8")
        with attachment_path.open("rb") as handle:
            upload_response = await e2e_client.post(
                "/api/chat/attachments/tmp",
                headers=e2e_headers,
                files={"file": (attachment_path.name, handle, "text/plain")},
            )
        assert upload_response.status_code == 200, upload_response.text
        uploaded = upload_response.json()
        confirm_response = await e2e_client.post(
            f"/api/chat/thread/{thread_id}/attachments/confirm",
            headers=e2e_headers,
            json={
                "attachments": [
                    {
                        "file_name": uploaded["file_name"],
                        "file_type": uploaded.get("file_type"),
                        "bucket_name": uploaded["bucket_name"],
                        "object_name": uploaded["object_name"],
                    }
                ]
            },
        )
        assert confirm_response.status_code == 200, confirm_response.text
        attachment = confirm_response.json()["attachments"][0]
        assert attachment["original_path"].startswith(f"/home/gem/user-data/{directory_name}/")

        run_response = await e2e_client.post(
            "/api/agent/runs",
            headers=e2e_headers,
            json={
                "query": "确认收到附件并简短回复。",
                "agent_slug": agent_slug,
                "thread_id": thread_id,
                "tool_approval_mode": "always_trust",
                "meta": {
                    "request_id": make_test_resource_id("linked-run"),
                    "attachment_file_ids": [str(attachment["file_id"])],
                },
            },
        )
        assert run_response.status_code == 200, run_response.text
        run_id = str(run_response.json()["run_id"])
        events = await consume_events(e2e_client, e2e_headers, run_id)
        assert events.get("end") == 1, events
        run = await wait_for_run(e2e_client, e2e_headers, run_id)
        assert run["status"] == "completed", run
        artifact_response = await e2e_client.get(
            attachment["original_artifact_url"],
            headers=e2e_headers,
        )
        assert artifact_response.status_code == 200, artifact_response.text
        assert artifact_response.content == b"linked project attachment proof"
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        cleanup_steps = []
        if run_id:
            target_run_id = run_id

            async def cancel_target_run() -> None:
                await cancel_run(e2e_client, e2e_headers, target_run_id)

            cleanup_steps.append((f"cancel run {run_id}", cancel_target_run))
        if thread_id:
            target_thread_id = thread_id

            async def delete_thread() -> None:
                response = await e2e_client.delete(
                    f"/api/chat/thread/{target_thread_id}",
                    headers=e2e_headers,
                )
                assert response.status_code in {200, 404}, response.text

            cleanup_steps.append((f"delete thread {thread_id}", delete_thread))
        if agent_slug:
            target_agent_slug = agent_slug

            async def delete_agent() -> None:
                response = await e2e_client.delete(
                    f"/api/agent/{target_agent_slug}",
                    headers=e2e_headers,
                )
                assert response.status_code in {200, 404}, response.text

            cleanup_steps.append((f"delete agent {agent_slug}", delete_agent))
        if project_id:
            target_project_id = project_id

            async def delete_project() -> None:
                response = await e2e_client.delete(
                    f"/api/projects/{target_project_id}",
                    headers=e2e_headers,
                )
                assert response.status_code in {200, 404}, response.text

            cleanup_steps.append((f"delete project {project_id}", delete_project))

        async def delete_directory() -> None:
            response = await e2e_client.request(
                "DELETE",
                "/api/workspace/file",
                headers=e2e_headers,
                params={"path": directory_name},
            )
            assert response.status_code in {200, 404}, response.text

        cleanup_steps.append((f"delete directory {directory_name}", delete_directory))
        await run_e2e_cleanup_steps(cleanup_steps, primary_error=primary_error)
