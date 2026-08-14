"""Docker workspace 沙盒 e2e（迁移工单 06）。

真实链路验证：模型发起 Write 工具调用 → agentscope 在 Docker workspace
容器内执行 → 文件落在线程隔离的工作区 → 经 workspace 文件端点读回。
前置：AGENTSCOPE_WORKSPACE_BACKEND=docker。
"""

import os
import uuid

import httpx
import pytest
from test.e2e.agentscope_e2e_fixtures import PROVIDER_ID, cleanup_fixture_agents, upsert_mock_provider

from yuxi.agentscope.client import AgentScopeServiceClient
from yuxi.agentscope.runner import collect_chat_round, ensure_thread_session
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import Agent

AGENTSCOPE_BASE_URL = os.getenv("AGENTSCOPE_BASE_URL", "http://agentscope:8100")
CHATBOT_SLUG = "e2e-ws-chatbot"
WRITE_PATH = "/workspace/outputs/hello.txt"
WRITE_CONTENT = "agentscope workspace 写入测试\n"

pytestmark = pytest.mark.e2e


@pytest.fixture
async def db_session():
    pg_manager.initialize()
    await pg_manager.ensure_business_schema()
    async with pg_manager.get_async_session_context() as session:
        await cleanup_fixture_agents(session, CHATBOT_SLUG)
        session.add_all(
            [
                Agent(
                    slug=CHATBOT_SLUG,
                    name="沙盒测试智能体",
                    backend_id="ChatbotAgent",
                    config_json={
                        "context": {
                            "model": f"{PROVIDER_ID}:mock-chat-model",
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
        await cleanup_fixture_agents(session, CHATBOT_SLUG)
    await pg_manager.close()
    pg_manager._initialized = False


async def test_write_tool_executes_in_docker_workspace(db_session):
    uid = "e2e-ws"
    thread_id = f"e2e-ws-thread-{uuid.uuid4().hex[:12]}"

    client = AgentScopeServiceClient(AGENTSCOPE_BASE_URL)
    mapping = await ensure_thread_session(
        db_session, client, uid=uid, thread_id=thread_id, agent_slug=CHATBOT_SLUG
    )
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

    # 模型发起了 Write 调用且工具在沙盒内执行成功
    types = [ev["type"] for ev in result.events]
    assert any("TOOL_CALL" in t for t in types), types
    assert any("TOOL_RESULT" in t for t in types), types
    assert result.text.startswith("文件已写入沙盒")

    # 文件落在线程工作区，可经 workspace 文件端点读回
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
