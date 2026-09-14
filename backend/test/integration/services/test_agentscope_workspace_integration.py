"""Docker workspace 与 AgentScope 服务集成测试（迁移工单 06）。

真实链路验证：模型先用 Write 创建文件，再在同一 session 用 Read/Edit 修改；
工具在 Docker workspace 容器内执行，最终经 workspace 文件端点读回。
前置：AGENTSCOPE_WORKSPACE_BACKEND=docker。
"""

import os
import uuid
from pathlib import Path

import httpx
import pytest
from sqlalchemy import delete
from test.e2e.agentscope_e2e_fixtures import (
    PROVIDER_RESOURCE_ID,
    cleanup_fixture_agents,
    cleanup_test_users,
    open_fixture_http_client,
    seed_test_users,
    upsert_mock_provider,
)
from test.e2e.e2e_helpers import consume_events, wait_for_run

from yuxi.agentscope.client import AgentScopeServiceClient
from yuxi.agentscope.runner import ensure_thread_session
from yuxi.repositories.agentscope_thread_sessions import get_thread_session
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import Agent, AgentScopeThreadSession
from yuxi.storage.redis import close_async_redis_client
from yuxi.services.thread_workspace_service import list_visible_files, read_file

AGENTSCOPE_BASE_URL = os.getenv("AGENTSCOPE_BASE_URL", "http://agentscope:8100")
CHATBOT_SLUG = "e2e-ws-chatbot"
USER_ID = "e2e-ws"
WRITE_PATH = "/workspace/outputs/hello.txt"
WRITE_CONTENT = "agentscope workspace 写入测试\n"
EDIT_CONTENT = "agentscope workspace 编辑成功\n"

pytestmark = pytest.mark.integration


@pytest.fixture
async def db_session():
    await close_async_redis_client()
    pg_manager.initialize()
    await pg_manager.ensure_business_schema()
    async with pg_manager.get_async_session_context() as session:
        await cleanup_fixture_agents(session, CHATBOT_SLUG)
        await session.execute(delete(AgentScopeThreadSession).where(AgentScopeThreadSession.uid == USER_ID))
        await seed_test_users(session, USER_ID)
        await upsert_mock_provider(session)
        session.add(
            Agent(
                slug=CHATBOT_SLUG,
                name="沙盒测试智能体",
                backend_id="ChatbotAgent",
                config_json={
                    "context": {
                        "model": f"{PROVIDER_RESOURCE_ID}:mock-chat-model",
                        "mcps": [],
                        "skills": [],
                        "system_prompt": "你是沙盒测试助手。",
                    }
                },
                share_config={
                    "version": 2,
                    "read_scope": {
                        "access_level": "global",
                        "department_ids": [],
                        "user_uids": [],
                    },
                    "manage_scope": None,
                },
                created_by=USER_ID,
            )
        )
        await session.commit()
        yield session
        await session.execute(delete(AgentScopeThreadSession).where(AgentScopeThreadSession.uid == USER_ID))
        await cleanup_fixture_agents(session, CHATBOT_SLUG)
        await cleanup_test_users(session, USER_ID)
    await close_async_redis_client()
    await pg_manager.close()
    pg_manager._initialized = False


async def test_write_then_edit_tools_execute_in_docker_workspace(db_session):
    uid = USER_ID
    client, headers = await open_fixture_http_client(uid)
    thread_id: str | None = None
    try:
        thread_response = await client.post(
            "/api/chat/thread",
            json={
                "agent_id": CHATBOT_SLUG,
                "title": f"workspace-{uuid.uuid4().hex[:8]}",
            },
            headers=headers,
        )
        assert thread_response.status_code == 200, thread_response.text
        thread_id = str(thread_response.json().get("thread_id") or thread_response.json()["id"])

        write_response = await client.post(
            "/api/agent/runs",
            json={
                "query": "请写文件到 outputs 目录",
                "agent_slug": CHATBOT_SLUG,
                "thread_id": thread_id,
                "meta": {"request_id": f"workspace-write-{uuid.uuid4()}"},
                "tool_approval_mode": "always_trust",
            },
            headers=headers,
        )
        assert write_response.status_code == 200, write_response.text
        write_run_id = str(write_response.json()["run_id"])
        write_events = await consume_events(client, headers, write_run_id)
        assert write_events.get("messages", 0) > 0, write_events
        assert write_events.get("end", 0) == 1, write_events
        write_run = await wait_for_run(client, headers, write_run_id)
        assert write_run["status"] == "completed", write_run

        write_result = await client.get(f"/api/agent/runs/{write_run_id}/result", headers=headers)
        assert write_result.status_code == 200, write_result.text
        assert write_result.json()["output"].startswith("文件已写入沙盒")

        mapping = await get_thread_session(db_session, uid=uid, thread_id=thread_id)
        assert mapping is not None
        async with httpx.AsyncClient(
            base_url=AGENTSCOPE_BASE_URL,
            headers={"X-User-ID": uid},
            timeout=60.0,
        ) as http:
            resp = await http.get(
                "/workspace/files",
                params={
                    "agent_id": mapping.agentscope_agent_id,
                    "session_id": mapping.agentscope_session_id,
                    "path": WRITE_PATH,
                },
            )
            assert resp.status_code == 200, resp.text
            assert resp.text == WRITE_CONTENT

        visible = await list_visible_files(db_session, uid=uid, thread_id=thread_id)
        assert [item["path"] for item in visible.items] == ["/home/gem/user-data/outputs/hello.txt"]
        assert visible.truncated is False
        assert (
            await read_file(
                db_session,
                uid=uid,
                thread_id=thread_id,
                virtual_path="/home/gem/user-data/outputs/hello.txt",
            )
            == WRITE_CONTENT.encode()
        )

        edit_response = await client.post(
            "/api/agent/runs",
            json={
                "query": "请编辑已有文件",
                "agent_slug": CHATBOT_SLUG,
                "thread_id": thread_id,
                "meta": {"request_id": f"workspace-edit-{uuid.uuid4()}"},
                "tool_approval_mode": "always_trust",
            },
            headers=headers,
        )
        assert edit_response.status_code == 200, edit_response.text
        edit_run_id = str(edit_response.json()["run_id"])
        edit_events = await consume_events(client, headers, edit_run_id)
        assert edit_events.get("messages", 0) > 0, edit_events
        assert edit_events.get("end", 0) == 1, edit_events
        edit_run = await wait_for_run(client, headers, edit_run_id)
        assert edit_run["status"] == "completed", edit_run

        async with httpx.AsyncClient(
            base_url=AGENTSCOPE_BASE_URL,
            headers={"X-User-ID": uid},
            timeout=60.0,
        ) as http:
            edited = await http.get(
                "/workspace/files",
                params={
                    "agent_id": mapping.agentscope_agent_id,
                    "session_id": mapping.agentscope_session_id,
                    "path": WRITE_PATH,
                },
            )
            assert edited.status_code == 200, edited.text
            assert edited.text == EDIT_CONTENT

        assert (
            await read_file(
                db_session,
                uid=uid,
                thread_id=thread_id,
                virtual_path="/home/gem/user-data/outputs/hello.txt",
            )
            == EDIT_CONTENT.encode()
        )
    finally:
        if thread_id:
            delete_response = await client.delete(f"/api/chat/thread/{thread_id}", headers=headers)
            assert delete_response.status_code in {200, 404}, delete_response.text
        await client.aclose()


async def test_attachment_upload_is_confined_and_size_limited(db_session, tmp_path: Path):
    """真实 Docker workspace 附件写入可读，且拒绝越界路径和超大文件。"""
    uid = USER_ID
    thread_id = f"e2e-ws-upload-{uuid.uuid4().hex[:12]}"
    client = AgentScopeServiceClient(AGENTSCOPE_BASE_URL)
    mapping = await ensure_thread_session(db_session, client, uid=uid, thread_id=thread_id, agent_slug=CHATBOT_SLUG)
    source = tmp_path / "attachment.txt"
    source.write_text("workspace attachment", encoding="utf-8")

    try:
        path = await client.upload_workspace_file(
            uid,
            mapping.agentscope_agent_id,
            mapping.agentscope_session_id,
            source_path=str(source),
            destination="/workspace/uploads/attachment.txt",
        )
        assert path == "/workspace/uploads/attachment.txt"

        async with httpx.AsyncClient(
            base_url=AGENTSCOPE_BASE_URL,
            headers={"X-User-ID": uid},
            timeout=120.0,
        ) as http:
            read = await http.get(
                "/workspace/files",
                params={
                    "agent_id": mapping.agentscope_agent_id,
                    "session_id": mapping.agentscope_session_id,
                    "path": path,
                },
            )
            assert read.status_code == 200, read.text
            assert read.text == "workspace attachment"

            traversal = await http.post(
                "/yuxi/workspace/file",
                params={
                    "agent_id": mapping.agentscope_agent_id,
                    "session_id": mapping.agentscope_session_id,
                    "destination": "/workspace/uploads/../escape.txt",
                },
                files={"file": ("escape.txt", b"blocked")},
            )
            assert traversal.status_code == 422, traversal.text

            oversized = await http.post(
                "/yuxi/workspace/file",
                params={
                    "agent_id": mapping.agentscope_agent_id,
                    "session_id": mapping.agentscope_session_id,
                    "destination": "/workspace/uploads/oversized.bin",
                },
                files={"file": ("oversized.bin", b"x" * (25 * 1024 * 1024 + 1))},
            )
            assert oversized.status_code == 413, oversized.text
    finally:
        await client.delete_session(uid, mapping.agentscope_agent_id, mapping.agentscope_session_id)
        await client.delete_agent(uid, mapping.agentscope_agent_id)
        await client.delete_credential(uid, mapping.agentscope_credential_id)
