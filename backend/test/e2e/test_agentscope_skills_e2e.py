"""Skills 渐进披露 e2e（迁移工单 09）。

前置（由验证脚本执行）：写入夹具 Skill 源目录与表行并重启 agentscope
服务（技能种子为启动期快照）。真实链路：模型按需调用 SkillViewer →
读取线程工作区技能分区中的 SKILL.md → 渐进披露内容进入对话。
"""

import json
import os
import uuid

import pytest

from test.e2e.agentscope_e2e_fixtures import (
    PROVIDER_ID,
    cleanup_fixture_agents,
    upsert_mock_provider,
)
from yuxi.agentscope.client import AgentScopeServiceClient
from yuxi.agentscope.gateway import stream_round_to_run_events
from yuxi.agentscope.runner import ensure_thread_session
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import Agent
from yuxi.storage.redis.manager import close_async_redis_client, get_async_redis_client

AGENTSCOPE_BASE_URL = os.getenv("AGENTSCOPE_BASE_URL", "http://agentscope:8100")
CHATBOT_SLUG = "e2e-skills-chatbot"
SKILL_NAME = "e2e-skill-demo"

pytestmark = pytest.mark.e2e


@pytest.fixture
async def db_session():
    pg_manager.initialize()
    await pg_manager.ensure_business_schema()
    async with pg_manager.get_async_session_context() as session:
        await cleanup_fixture_agents(session, CHATBOT_SLUG)
        session.add(
            Agent(
                slug=CHATBOT_SLUG,
                name="技能测试智能体",
                backend_id="ChatbotAgent",
                config_json={
                    "context": {
                        "model": f"{PROVIDER_ID}:mock-chat-model",
                        "system_prompt": "你是技能测试助手。",
                    }
                },
                share_config={},
            )
        )
        await upsert_mock_provider(session)
        yield session
        await cleanup_fixture_agents(session, CHATBOT_SLUG)
    await pg_manager.close()
    pg_manager._initialized = False


async def test_skill_progressive_disclosure(db_session):
    uid = "e2e-skills"
    run_id = f"e2e-run-{uuid.uuid4().hex[:12]}"
    request_id = f"e2e-req-{uuid.uuid4().hex[:8]}"
    thread_id = f"e2e-thread-{uuid.uuid4().hex[:12]}"

    client = AgentScopeServiceClient(AGENTSCOPE_BASE_URL)
    mapping = await ensure_thread_session(
        db_session, client, uid=uid, thread_id=thread_id, agent_slug=CHATBOT_SLUG
    )
    result = await stream_round_to_run_events(
        client,
        uid=uid,
        agent_id=mapping.agentscope_agent_id,
        session_id=mapping.agentscope_session_id,
        text="请查看技能",
        run_id=run_id,
        request_id=request_id,
        thread_id=thread_id,
        read_timeout=300.0,
    )
    assert result.run_status == "completed"

    redis_client = await get_async_redis_client()
    stream_key = f"run:events:{run_id}"
    try:
        frames = await redis_client.xrange(stream_key)
        all_chunks = []
        for _, entry in frames:
            if entry["event_type"] == "messages":
                all_chunks.extend(json.loads(entry["payload"])["payload"]["items"])

        # SkillViewer 工具被调用，且披露内容进入对话
        called_names = [
            frag["name"]
            for c in all_chunks
            if c["status"] == "loading" and c["msg"].get("tool_call_chunks")
            for frag in c["msg"]["tool_call_chunks"]
        ]
        assert "Skill" in called_names, called_names

        finished = [c for c in all_chunks if c["status"] == "stream_event"]
        skill_content = "".join(c["event"]["data"]["output"]["content"] for c in finished)
        assert "用于渐进披露验证的技能" in skill_content
    finally:
        await redis_client.delete(stream_key)
        await close_async_redis_client()
