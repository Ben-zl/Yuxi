"""Skills 渐进披露 e2e（迁移工单 09）。

前置（由验证脚本执行）：写入夹具 Skill 源目录与表行并重启 agentscope
服务（技能种子为启动期快照）。真实链路：模型按需调用 SkillViewer →
读取线程工作区技能分区中的 SKILL.md → 渐进披露内容进入对话。
"""

import json
import os
import uuid

import pytest
from sqlalchemy import delete

from e2e_helpers import consume_events, wait_for_run
from test.e2e.agentscope_e2e_fixtures import (
    PROVIDER_RESOURCE_ID,
    cleanup_fixture_agents,
    cleanup_test_users,
    open_fixture_http_client,
    seed_test_users,
    upsert_mock_provider,
)
from yuxi.agents.skills.service import refresh_user_skill_projection_async
from yuxi.storage.postgres.manager import pg_manager
from yuxi.config import get_skill_data_dir
from yuxi.storage.postgres.models_business import Agent, Skill
from yuxi.storage.redis.manager import close_async_redis_client, get_async_redis_client

AGENTSCOPE_BASE_URL = os.getenv("AGENTSCOPE_BASE_URL", "http://agentscope:8100")
CHATBOT_SLUG = "e2e-skills-chatbot"
SKILL_NAME = "e2e-skill-demo"
USER_ID = "e2e-skills"

pytestmark = pytest.mark.e2e


@pytest.fixture
async def db_session():
    pg_manager.initialize()
    await pg_manager.ensure_business_schema()
    async with pg_manager.get_async_session_context() as session:
        await cleanup_fixture_agents(session, CHATBOT_SLUG)
        await seed_test_users(session, USER_ID)
        skill_dir = get_skill_data_dir() / "shared" / SKILL_NAME
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            (
                "---\n"
                "slug: e2e-skill-demo\n"
                "name: e2e-skill-demo\n"
                "description: 用于渐进披露验证的技能\n"
                "---\n\n"
                "用于渐进披露验证的技能。\n"
            ),
            encoding="utf-8",
        )
        session.add(
            Skill(
                slug=SKILL_NAME,
                name=SKILL_NAME,
                description="用于渐进披露验证的技能",
                source_type="upload",
                dir_path=f"shared/{SKILL_NAME}",
                share_config={
                    "version": 2,
                    "read_scope": {"access_level": "global", "department_ids": [], "user_uids": []},
                    "manage_scope": None,
                },
                created_by="e2e",
                updated_by="e2e",
            )
        )
        session.add(
            Agent(
                slug=CHATBOT_SLUG,
                name="技能测试智能体",
                backend_id="ChatbotAgent",
                config_json={
                    "context": {
                        "model": f"{PROVIDER_RESOURCE_ID}:mock-chat-model",
                        "mcps": [],
                        "tools": [],
                        "subagents": [],
                        "skills": [SKILL_NAME],
                        "system_prompt": "你是技能测试助手。",
                    }
                },
                share_config={},
            )
        )
        await upsert_mock_provider(session)
        await refresh_user_skill_projection_async(USER_ID)
        yield session
        await cleanup_fixture_agents(session, CHATBOT_SLUG)
        await session.execute(delete(Skill).where(Skill.slug == SKILL_NAME))
        await session.commit()
        await cleanup_test_users(session, USER_ID)
        import shutil

        shutil.rmtree(get_skill_data_dir() / "shared" / SKILL_NAME, ignore_errors=True)
    await close_async_redis_client()
    await pg_manager.close()
    pg_manager._initialized = False


async def test_skill_progressive_disclosure(db_session):
    uid = USER_ID
    request_id = f"e2e-req-{uuid.uuid4().hex[:8]}"
    client, headers = await open_fixture_http_client(uid)
    try:
        thread_response = await client.post(
            "/api/chat/thread",
            json={"agent_id": CHATBOT_SLUG, "title": f"skills-{uuid.uuid4().hex[:8]}"},
            headers=headers,
        )
        assert thread_response.status_code == 200, thread_response.text
        thread_id = str(thread_response.json().get("thread_id") or thread_response.json().get("id"))
        run_response = await client.post(
            "/api/agent/runs",
            json={
                "query": "请查看技能",
                "agent_slug": CHATBOT_SLUG,
                "thread_id": thread_id,
                "meta": {"request_id": request_id},
            },
            headers=headers,
        )
        assert run_response.status_code == 200, run_response.text
        run_id = str(run_response.json()["run_id"])
        event_counts = await consume_events(client, headers, run_id)
        assert event_counts.get("end", 0) == 1, event_counts
        run = await wait_for_run(client, headers, run_id)
        assert run["status"] == "completed", run

        redis_client = await get_async_redis_client()
        stream_key = f"run:events:{run_id}"
        try:
            frames = await redis_client.xrange(stream_key)
            all_chunks = []
            for _, entry in frames:
                if entry["event_type"] == "messages":
                    all_chunks.extend(json.loads(entry["payload"])["payload"]["items"])

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
    finally:
        await client.aclose()


async def test_skill_dependency_runs_through_gateway(db_session):
    """原版 AgentScope 通过 Yuxi Gateway 执行激活后的 Skill 依赖。"""
    request_id = f"e2e-req-{uuid.uuid4().hex[:8]}"
    client, headers = await open_fixture_http_client(USER_ID)
    try:
        thread_response = await client.post(
            "/api/chat/thread",
            json={"agent_id": CHATBOT_SLUG, "title": f"skill-dependency-{uuid.uuid4().hex[:8]}"},
            headers=headers,
        )
        assert thread_response.status_code == 200, thread_response.text
        thread_id = str(thread_response.json().get("thread_id") or thread_response.json().get("id"))
        run_response = await client.post(
            "/api/agent/runs",
            json={
                "query": "请调用技能依赖完成查询",
                "agent_slug": CHATBOT_SLUG,
                "thread_id": thread_id,
                "tool_approval_mode": "always_trust",
                "meta": {"request_id": request_id},
            },
            headers=headers,
        )
        assert run_response.status_code == 200, run_response.text
        run_id = str(run_response.json()["run_id"])
        event_counts = await consume_events(client, headers, run_id)
        assert event_counts.get("end", 0) == 1, event_counts
        run = await wait_for_run(client, headers, run_id)
        assert run["status"] == "completed", run

        redis_client = await get_async_redis_client()
        try:
            frames = await redis_client.xrange(f"run:events:{run_id}")
            chunks = [
                chunk
                for _, entry in frames
                if entry["event_type"] == "messages"
                for chunk in json.loads(entry["payload"])["payload"]["items"]
            ]
            called_names = [
                fragment["name"]
                for chunk in chunks
                if chunk["status"] == "loading" and chunk["msg"].get("tool_call_chunks")
                for fragment in chunk["msg"]["tool_call_chunks"]
            ]
            assert "Skill" in called_names
            assert "skill_dependency_gateway" in called_names
            call_names_by_id = {
                fragment["id"]: fragment["name"]
                for chunk in chunks
                if chunk["status"] == "loading" and chunk["msg"].get("tool_call_chunks")
                for fragment in chunk["msg"]["tool_call_chunks"]
            }
            finished = [chunk for chunk in chunks if chunk["status"] == "stream_event"]
            assert any(
                call_names_by_id.get(chunk["event"]["data"]["tool_call_id"]) == "skill_dependency_gateway"
                for chunk in finished
            )
        finally:
            await redis_client.delete(f"run:events:{run_id}")
            await close_async_redis_client()
    finally:
        await client.aclose()
