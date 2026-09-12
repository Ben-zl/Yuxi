"""运行时配置投影统一夹具（迁移工单 03 · Seam 2 入口）。

给定 yuxi 库夹具（Agent/子智能体/模型供应商/Skill），断言统一投影
产出的完整运行时对象集合；LITE 模式裁剪知识库投影；配置缺失显式失败。
后续工具/Skills/MCP/Team 工单的夹具一律复用本入口，不另起炉灶。
"""

import os
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.agentscope.config_projection import project_runtime
from yuxi.config import config
from yuxi.repositories.agent_repository import DEFAULT_SHARE_CONFIG
from yuxi.storage.postgres.models_business import Agent, Base, MCPServer, ModelProvider, Skill, User

CHATBOT_SLUG = "it-proj-chatbot"
SUBAGENT_SLUG = "it-proj-subagent"
PROVIDER_ID = "it-proj-openai-mock"
PROVIDER_RESOURCE_ID = "11111111-1111-4111-8111-111111111111"
MCP_RESOURCE_ID = "22222222-2222-4222-8222-222222222222"
MODEL_SPEC = f"{PROVIDER_RESOURCE_ID}:mock-chat-model"

pytestmark = pytest.mark.integration


@asynccontextmanager
async def isolated_projection_session():
    """投影夹具仅写随机 schema，不执行实时库升级或删除共享数据。"""
    schema = "test_mcp_projection_" + uuid4().hex
    engine = create_async_engine(os.environ["POSTGRES_URL"])
    isolated = create_async_engine(
        os.environ["POSTGRES_URL"], connect_args={"server_settings": {"search_path": schema}}
    )
    try:
        async with engine.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        async with isolated.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(isolated, expire_on_commit=False)() as session:
            yield session
    finally:
        await isolated.dispose()
        async with engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await engine.dispose()


@pytest.fixture
async def db_session():
    async with isolated_projection_session() as session:
        session.add_all(
            [
                User(
                    uid="it-proj-user",
                    username="it-proj-user",
                    password_hash="test-only",
                    role="superadmin",
                ),
                Agent(
                    slug=CHATBOT_SLUG,
                    name="投影测试智能体",
                    backend_id="ChatbotAgent",
                    config_json={
                        "context": {
                            "model": MODEL_SPEC,
                            "system_prompt": "你是投影测试助手。",
                            "skills": ["it-proj-skill"],
                            "mcps": ["it-proj-mcp"],
                            "knowledges": ["kb-a"],
                        }
                    },
                    share_config=DEFAULT_SHARE_CONFIG,
                ),
                Agent(
                    slug=SUBAGENT_SLUG,
                    name="投影测试子智能体",
                    description="子智能体描述",
                    backend_id="SubAgentBackend",
                    is_subagent=True,
                    config_json={"context": {"skills": [], "mcps": []}},
                    share_config=DEFAULT_SHARE_CONFIG,
                ),
                ModelProvider(
                    resource_id=PROVIDER_RESOURCE_ID,
                    provider_id=PROVIDER_ID,
                    display_name="投影测试供应商",
                    provider_type="openai",
                    base_url=os.getenv("OPENAI_MOCK_URL", "http://openai-mock:8080/v1"),
                    api_key="it-proj-key",
                    capabilities=["chat"],
                    enabled_models=[
                        {
                            "id": "mock-chat-model",
                            "type": "chat",
                            "base_url_override": "http://openai-mock:8080/custom",
                            "request_body_overrides": {"enable_thinking": True},
                        }
                    ],
                    headers_json={"X-Test": "projection"},
                    extra_json={"parameters": {"temperature": 0.2}},
                    is_enabled=True,
                ),
                MCPServer(
                    resource_id=MCP_RESOURCE_ID,
                    slug="it-proj-mcp",
                    name="投影测试 MCP",
                    transport="streamable_http",
                    url="http://mcp-mock:8000/mcp",
                    enabled=1,
                    created_by="integration",
                    updated_by="integration",
                ),
                Skill(
                    slug="it-proj-skill",
                    name="投影测试技能",
                    description="投影测试技能",
                    tool_dependencies=["ask_user_question"],
                    mcp_dependencies=["it-proj-mcp"],
                    skill_dependencies=[],
                    dir_path="workspace/skills/it-proj-skill",
                    share_config=DEFAULT_SHARE_CONFIG,
                    enabled=True,
                ),
            ]
        )
        await session.commit()
        yield session
        await session.execute(delete(Agent).where(Agent.slug.like("it-proj-%")))
        await session.execute(delete(ModelProvider).where(ModelProvider.provider_id == PROVIDER_ID))
        await session.execute(delete(MCPServer).where(MCPServer.slug == "it-proj-mcp"))
        await session.execute(delete(Skill).where(Skill.slug == "it-proj-skill"))
        await session.execute(delete(User).where(User.uid == "it-proj-user"))
        await session.commit()
    from yuxi.storage.redis.manager import close_async_redis_client

    await close_async_redis_client()


async def test_project_runtime_covers_all_components(db_session):
    projection = await project_runtime(db_session, uid="it-proj-user", agent_slug=CHATBOT_SLUG)

    assert projection.model_spec == MODEL_SPEC
    assert projection.agent_request["name"] == "投影测试智能体"
    assert projection.agent_request["system_prompt"] == "你是投影测试助手。"
    # ReAct 迭代上限使用管理配置的默认值 300。
    assert projection.agent_request["react_config"] == {"max_iters": 300}
    assert projection.credential_data["type"] == "yuxi_openai_credential"
    assert projection.credential_data["api_key"] == "it-proj-key"
    assert projection.credential_data["base_url"] == "http://openai-mock:8080/custom"
    assert projection.credential_data["default_headers"] == {"X-Test": "projection"}
    assert projection.credential_data["request_body_overrides"] == {"enable_thinking": True}
    assert projection.chat_model_config["model"] == "mock-chat-model"
    assert projection.chat_model_config["credential_id"] is None  # 由创建方回填
    assert projection.chat_model_config["parameters"] == {"temperature": 0.2}
    assert "client_kwargs" not in projection.chat_model_config
    assert "extra_body" not in projection.chat_model_config

    assert projection.skill_slugs == ["it-proj-skill"]
    assert next(item for item in projection.skills if item["slug"] == "it-proj-skill")["name"] == "投影测试技能"
    assert projection.mcp_servers == [
        {
            "slug": "it-proj-mcp",
            "resource_id": MCP_RESOURCE_ID,
            "transport": "streamable_http",
            "url": "http://mcp-mock:8000/mcp",
        }
    ]
    assert projection.knowledge_slugs == ["kb-a"]
    assert projection.tool_slugs is None
    assert projection.skill_tool_dependencies["it-proj-skill"] == ["ask_user_question"]
    assert projection.skill_mcp_dependencies["it-proj-skill"] == [MCP_RESOURCE_ID]

    template_types = {t["type"] for t in projection.subagent_templates}
    assert SUBAGENT_SLUG in template_types


async def test_projection_duplicate_logical_slugs_resolve_only_by_resource_id(db_session):
    """同名 MCP 不能串用，Skill 依赖和基础工具均投影资源 ID。"""
    from sqlalchemy import select

    second_id = "33333333-3333-4333-8333-333333333333"
    db_session.add(
        MCPServer(
            resource_id=second_id,
            slug="it-proj-mcp",
            name="Second MCP",
            transport="streamable_http",
            url="https://second.example.test/mcp",
            enabled=1,
            created_by="integration",
            updated_by="integration",
        )
    )
    agent = await db_session.scalar(select(Agent).where(Agent.slug == CHATBOT_SLUG))
    skill = await db_session.scalar(select(Skill).where(Skill.slug == "it-proj-skill"))
    context = {**agent.config_json["context"], "mcps": [MCP_RESOURCE_ID, second_id]}
    agent.config_json = {"context": context}
    skill.mcp_dependencies = [second_id]
    await db_session.commit()
    projection = await project_runtime(db_session, uid="it-proj-user", agent_slug=CHATBOT_SLUG)
    assert [item["resource_id"] for item in projection.mcp_servers] == [MCP_RESOURCE_ID, second_id]
    assert {item["slug"] for item in projection.mcp_servers} == {"it-proj-mcp"}
    assert projection.skill_mcp_dependencies["it-proj-skill"] == [second_id]
    assert [item["resource_id"] for item in projection.skill_mcp_servers["it-proj-skill"]] == [second_id]
    agent.config_json = {"context": {**context, "mcps": ["it-proj-mcp"]}}
    await db_session.commit()
    with pytest.raises(ValueError, match="MCP"):
        await project_runtime(db_session, uid="it-proj-user", agent_slug=CHATBOT_SLUG)


async def test_project_runtime_lite_trims_knowledge(db_session, monkeypatch):
    monkeypatch.setenv("LITE_MODE", "true")
    projection = await project_runtime(db_session, uid="it-proj-user", agent_slug=CHATBOT_SLUG)
    assert projection.knowledge_slugs == []
    # 纯聊天核心不受 LITE 影响
    assert projection.chat_model_config["model"] == "mock-chat-model"
    assert projection.agent_request["system_prompt"] == "你是投影测试助手。"


async def test_project_runtime_fails_explicitly(db_session, monkeypatch):
    with pytest.raises(ValueError, match="不存在"):
        await project_runtime(db_session, uid="it-proj-user", agent_slug="no-such-agent")
    # 无模型智能体回落系统默认对话模型（与旧栈一致）；
    # 默认模型的 key 由环境提供，测试内打桩避免依赖本地 .env
    from yuxi.agentscope import projection as proj

    monkeypatch.setattr(proj, "_resolve_api_key", lambda provider: "test-key")
    monkeypatch.setattr(config, "default_model", MODEL_SPEC)
    projection = await project_runtime(db_session, uid="it-proj-user", agent_slug=SUBAGENT_SLUG)
    assert projection.model_spec == MODEL_SPEC
    with pytest.raises(ValueError, match="不存在"):
        await project_runtime(
            db_session,
            uid="it-proj-user",
            agent_slug=CHATBOT_SLUG,
            model_spec="no-such-provider:m",
        )
