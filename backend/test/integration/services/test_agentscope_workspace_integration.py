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
from test.e2e.agentscope_e2e_fixtures import PROVIDER_ID, cleanup_fixture_agents, upsert_mock_provider

from yuxi.agentscope.client import AgentScopeServiceClient
from yuxi.agentscope.runner import collect_chat_round, ensure_thread_session
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import Agent, AgentScopeThreadSession, User
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
        await session.execute(delete(User).where(User.uid == USER_ID))
        session.add_all(
            [
                User(
                    uid=USER_ID,
                    username=USER_ID,
                    password_hash="test-only",
                    role="superadmin",
                ),
                Agent(
                    slug=CHATBOT_SLUG,
                    name="沙盒测试智能体",
                    backend_id="ChatbotAgent",
                    config_json={
                        "context": {
                            "model": f"{PROVIDER_ID}:mock-chat-model",
                            "mcps": [],
                            "system_prompt": "你是沙盒测试助手。",
                        }
                    },
                    share_config={},
                ),
            ]
        )
        await upsert_mock_provider(session)
        await session.commit()
        yield session
        await session.execute(delete(AgentScopeThreadSession).where(AgentScopeThreadSession.uid == USER_ID))
        await cleanup_fixture_agents(session, CHATBOT_SLUG)
        await session.execute(delete(User).where(User.uid == USER_ID))
        await session.commit()
    await close_async_redis_client()
    await pg_manager.close()
    pg_manager._initialized = False


async def test_write_then_edit_tools_execute_in_docker_workspace(db_session):
    uid = USER_ID
    thread_id = f"e2e-ws-thread-{uuid.uuid4().hex[:12]}"

    client = AgentScopeServiceClient(AGENTSCOPE_BASE_URL)
    mapping = await ensure_thread_session(db_session, client, uid=uid, thread_id=thread_id, agent_slug=CHATBOT_SLUG)
    try:
        # 文件写默认触发人工审批（审批链路在工单 10 验证），此处放行编辑类操作
        await client.set_permission_mode(
            uid, mapping.agentscope_agent_id, mapping.agentscope_session_id, "accept_edits"
        )
        result = await collect_chat_round(
            client,
            uid=uid,
            agent_id=mapping.agentscope_agent_id,
            session_id=mapping.agentscope_session_id,
            text="请写文件到 outputs 目录",
            read_timeout=600.0,
        )

        types = [ev["type"] for ev in result.events]
        assert any("TOOL_CALL" in t for t in types), types
        assert any("TOOL_RESULT" in t for t in types), types
        assert result.text.startswith("文件已写入沙盒")

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

        edit_result = await collect_chat_round(
            client,
            uid=uid,
            agent_id=mapping.agentscope_agent_id,
            session_id=mapping.agentscope_session_id,
            text="请编辑已有文件",
            read_timeout=600.0,
        )
        called = {
            event.get("tool_call_name")
            for event in edit_result.events
            if str(event.get("type", "")).upper() == "TOOL_CALL_START"
        }
        failed_results = [
            event
            for event in edit_result.events
            if str(event.get("type", "")).upper() == "TOOL_RESULT_END"
            and str(event.get("state", "")).lower() == "error"
        ]
        assert {"Read", "Edit"} <= called, called
        assert not failed_results, failed_results

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
        await client.delete_session(uid, mapping.agentscope_agent_id, mapping.agentscope_session_id)
        await client.delete_agent(uid, mapping.agentscope_agent_id)
        await client.delete_credential(uid, mapping.agentscope_credential_id)


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
