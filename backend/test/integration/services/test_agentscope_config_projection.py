"""运行时配置投影统一夹具（迁移工单 03 · Seam 2 入口）。

给定 yuxi 库夹具（Agent/子智能体/模型供应商/Skill），断言统一投影
产出的完整运行时对象集合；LITE 模式裁剪知识库投影；配置缺失显式失败。
后续工具/Skills/MCP/Team 工单的夹具一律复用本入口，不另起炉灶。
"""

import pytest
from sqlalchemy import delete

from yuxi.agentscope.config_projection import project_runtime
from yuxi.config import config
from yuxi.repositories.agent_repository import DEFAULT_SHARE_CONFIG
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import Agent, MCPServer, ModelProvider, Skill, User

CHATBOT_SLUG = "it-proj-chatbot"
SUBAGENT_SLUG = "it-proj-subagent"
PROVIDER_ID = "it-proj-openai-mock"
MODEL_SPEC = f"{PROVIDER_ID}:mock-chat-model"

pytestmark = pytest.mark.integration


@pytest.fixture
async def db_session():
    pg_manager.initialize()
    await pg_manager.ensure_business_schema()
    async with pg_manager.get_async_session_context() as session:
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
                            "skills": None,
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
                    config_json={"context": {}},
                    share_config=DEFAULT_SHARE_CONFIG,
                ),
                ModelProvider(
                    provider_id=PROVIDER_ID,
                    display_name="投影测试供应商",
                    provider_type="openai",
                    base_url="http://openai-mock:8080/v1",
                    api_key="it-proj-key",
                    capabilities=["chat"],
                    enabled_models=[{"id": "mock-chat-model", "type": "chat"}],
                    is_enabled=True,
                ),
                MCPServer(
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
                    tool_dependencies=[],
                    mcp_dependencies=[],
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
    await pg_manager.close()
    pg_manager._initialized = False
    from yuxi.storage.redis.manager import close_async_redis_client

    await close_async_redis_client()


async def test_project_runtime_covers_all_components(db_session):
    projection = await project_runtime(db_session, uid="it-proj-user", agent_slug=CHATBOT_SLUG)

    assert projection.model_spec == MODEL_SPEC
    assert projection.agent_request["name"] == "投影测试智能体"
    assert projection.agent_request["system_prompt"] == "你是投影测试助手。"
    # ReAct 迭代上限使用管理配置的默认值 300。
    assert projection.agent_request["react_config"] == {"max_iters": 300}
    assert projection.credential_data["type"] == "openai_credential"
    assert projection.credential_data["api_key"] == "it-proj-key"
    assert projection.credential_data["base_url"] == "http://openai-mock:8080/v1"
    assert projection.chat_model_config["model"] == "mock-chat-model"
    assert projection.chat_model_config["credential_id"] is None  # 由创建方回填

    # skills=None 表示全部可用：投影为全部可见 Skill（dev 库存量技能 + 夹具技能）
    assert "it-proj-skill" in projection.skill_slugs
    assert next(item for item in projection.skills if item["slug"] == "it-proj-skill")["name"] == "投影测试技能"
    assert projection.mcp_servers == [
        {
            "slug": "it-proj-mcp",
            "transport": "streamable_http",
            "url": "http://mcp-mock:8000/mcp",
        }
    ]
    assert projection.knowledge_slugs == ["kb-a"]

    template_types = {t["type"] for t in projection.subagent_templates}
    assert SUBAGENT_SLUG in template_types


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
    projection = await project_runtime(db_session, uid="it-proj-user", agent_slug=SUBAGENT_SLUG)
    assert projection.model_spec == config.default_model
    with pytest.raises(ValueError, match="不存在"):
        await project_runtime(
            db_session,
            uid="it-proj-user",
            agent_slug=CHATBOT_SLUG,
            model_spec="no-such-provider:m",
        )
