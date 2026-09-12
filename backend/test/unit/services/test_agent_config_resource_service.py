from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from yuxi.permissions import agent_config_resource as service
from yuxi.permissions import resource_permission
from yuxi.repositories.agent_repository import AgentRepository


def scope(level, values=()):
    """构造独立的权限输入。"""
    return {
        "access_level": level,
        "department_ids": list(values) if level == "department" else [],
        "user_uids": list(values) if level == "user" else [],
    }


def sharing(read, manage=None):
    """构造完整共享范围。"""
    return {"version": 2, "read_scope": read, "manage_scope": manage}


@pytest.fixture(autouse=True)
def empty_implicit_resources(monkeypatch):
    """未指定资源时默认固化空的可见集合，专项测试可覆盖。"""
    monkeypatch.setattr(service, "list_authorizable_skills", AsyncMock(return_value=[]))
    monkeypatch.setattr(service, "list_authorizable_mcp_servers", AsyncMock(return_value=[]))


@pytest.fixture
def resources(monkeypatch):
    """提供可控资源与真实策略所查询的用户。"""
    provider = SimpleNamespace(
        resource_id="resource",
        is_enabled=True,
        share_config=sharing(scope("department", [1])),
    )
    lookup = AsyncMock(return_value=provider)
    monkeypatch.setattr(service, "get_model_provider_for_user", lookup)
    monkeypatch.setattr(service.model_cache, "canonicalize_spec", lambda spec: "resource:chat")
    monkeypatch.setattr(
        service.model_cache,
        "get_model_info",
        lambda spec: SimpleNamespace(
            resource_id="resource",
            provider_id="legacy",
            model_id="chat",
            model_type="chat",
        ),
    )
    users = {
        "owner": SimpleNamespace(uid="owner", department_id=1, is_deleted=False),
        "outside": SimpleNamespace(uid="outside", department_id=2, is_deleted=False),
        "deleted": SimpleNamespace(uid="deleted", department_id=1, is_deleted=True),
    }

    async def list_users(_self, uids):
        """返回已存在的测试用户。"""
        return [users[uid] for uid in uids if uid in users]

    monkeypatch.setattr(resource_permission.UserRepository, "list_by_uids", list_users)
    return provider, lookup


@pytest.mark.parametrize(
    "read,manage,owner",
    [
        (scope("global"), None, "owner"),
        (scope("department", [1, 2]), None, "owner"),
        (scope("department", [1]), scope("user", ["outside"]), "owner"),
        (scope("department", [1]), scope("global"), "owner"),
        (scope("department", [1]), None, "outside"),
        (scope("user", ["missing"]), None, "owner"),
        (scope("user", ["deleted"]), None, "owner"),
    ],
)
async def test_service_rejects_uncovered_readers_managers_and_owner(resources, read, manage, owner):
    original = {"context": {"model": "legacy:chat"}}
    with pytest.raises(ValueError, match="完整读取范围"):
        await service.authorize_agent_config_resources(
            original,
            db=Mock(),
            user=SimpleNamespace(uid="operator"),
            agent_share_config=sharing(read, manage),
            owner_uid=owner,
        )
    assert original == {"context": {"model": "legacy:chat"}}


async def test_service_canonicalizes_without_mutating_input_and_uses_operator(resources):
    _, lookup = resources
    operator = SimpleNamespace(uid="operator")
    original = {"context": {"model": "legacy:chat", "system_prompt": "keep"}}
    result = await service.authorize_agent_config_resources(
        original,
        db=Mock(),
        user=operator,
        agent_share_config=sharing(scope("user", ["owner"])),
        owner_uid="owner",
    )
    assert result == {"context": {"model": "resource:chat", "system_prompt": "keep", "skills": [], "mcps": []}}
    assert original["context"]["model"] == "legacy:chat"
    assert lookup.await_args.args[2] is operator


async def test_service_resolves_old_cache_payload_through_database(monkeypatch):
    provider = SimpleNamespace(
        resource_id="provider-resource",
        is_enabled=True,
        share_config=sharing(scope("global")),
    )
    resolve_reference = AsyncMock(return_value=provider)
    monkeypatch.setattr(service.model_cache, "canonicalize_spec", lambda spec: "legacy:chat")
    monkeypatch.setattr(
        service.model_cache,
        "get_model_info",
        lambda spec: SimpleNamespace(
            resource_id="legacy",
            provider_id="legacy",
            model_id="/jade/chat",
            model_type="chat",
        ),
    )
    monkeypatch.setattr(service, "get_legacy_model_provider_for_user", resolve_reference, raising=False)

    result = await service.authorize_agent_config_resources(
        {"context": {"model": "legacy:/jade/chat"}},
        db=Mock(),
        user=SimpleNamespace(uid="operator"),
        agent_share_config=sharing(scope("user", ["owner"])),
        owner_uid="owner",
    )

    assert result["context"]["model"] == "provider-resource:/jade/chat"
    resolve_reference.assert_awaited_once()


async def test_service_materializes_missing_context_and_rejects_invalid_context():
    result = await service.authorize_agent_config_resources(
        {},
        db=Mock(),
        user=SimpleNamespace(uid="operator"),
        agent_share_config=sharing(scope("user", ["owner"])),
        owner_uid="owner",
    )
    assert result == {"context": {"skills": [], "mcps": []}}

    with pytest.raises(ValueError, match="context 必须是对象"):
        await service.authorize_agent_config_resources(
            {"context": []},
            db=Mock(),
            user=SimpleNamespace(uid="operator"),
            agent_share_config=sharing(scope("user", ["owner"])),
            owner_uid="owner",
        )


@pytest.mark.parametrize("disabled", [False, True])
async def test_service_rejects_invisible_or_disabled_model(resources, disabled):
    provider, lookup = resources
    provider.is_enabled = not disabled
    if not disabled:
        lookup.return_value = None
    with pytest.raises(ValueError, match="不可访问"):
        await service.authorize_agent_config_resources(
            {"context": {"model": "legacy:chat"}},
            db=Mock(),
            user=SimpleNamespace(uid="operator"),
            agent_share_config=sharing(scope("user", ["owner"])),
            owner_uid="owner",
        )


async def test_service_rejects_mcp_scope_expansion(monkeypatch):
    monkeypatch.setattr(
        service,
        "get_authorizable_mcp_server",
        AsyncMock(
            return_value=SimpleNamespace(
                resource_id="mcp",
                share_config=sharing(scope("department", [1])),
            )
        ),
    )
    with pytest.raises(ValueError, match="MCP 资源未覆盖"):
        await service.authorize_agent_config_resources(
            {"context": {"mcps": ["mcp"]}},
            db=Mock(),
            user=SimpleNamespace(uid="operator"),
            agent_share_config=sharing(scope("global")),
            owner_uid="owner",
        )


async def test_service_freezes_and_validates_implicit_skill_and_mcp_resources(monkeypatch):
    """省略 skills/mcps 时也固化资源 ID，并拒绝未覆盖 Agent 范围的资源。"""
    skill = SimpleNamespace(
        slug="implicit-skill",
        created_by="owner",
        share_config=sharing(scope("global")),
        skill_dependencies=[],
        mcp_dependencies=[],
    )
    monkeypatch.setattr(service, "list_authorizable_skills", AsyncMock(return_value=[skill]))
    monkeypatch.setattr(
        service,
        "list_authorizable_mcp_servers",
        AsyncMock(return_value=[SimpleNamespace(resource_id="implicit-mcp")]),
    )
    mcp = SimpleNamespace(resource_id="implicit-mcp", share_config=sharing(scope("global")))
    monkeypatch.setattr(service, "get_authorizable_mcp_server", AsyncMock(return_value=mcp))

    result = await service.authorize_agent_config_resources(
        {"context": {}},
        db=Mock(),
        user=SimpleNamespace(uid="operator"),
        agent_share_config=sharing(scope("department", [1])),
        owner_uid="owner",
    )
    assert result["context"]["skills"] == ["implicit-skill"]
    assert result["context"]["mcps"] == ["implicit-mcp"]

    mcp.share_config = sharing(scope("department", [2]))
    with pytest.raises(ValueError, match="MCP 资源未覆盖"):
        await service.authorize_agent_config_resources(
            {"context": {}},
            db=Mock(),
            user=SimpleNamespace(uid="operator"),
            agent_share_config=sharing(scope("department", [1])),
            owner_uid="owner",
        )


async def test_service_validates_skill_closure_and_skill_mcp_scope(monkeypatch):
    """保存 Agent 时校验选中 Skill、传递依赖及其 MCP 对完整范围可用。"""
    skills = [
        SimpleNamespace(
            slug="parent",
            source_scope="shared",
            created_by="owner",
            share_config=sharing(scope("global")),
            skill_dependencies=["child"],
            mcp_dependencies=[],
        ),
        SimpleNamespace(
            slug="child",
            source_scope="shared",
            created_by="owner",
            share_config=sharing(scope("global")),
            skill_dependencies=[],
            mcp_dependencies=["skill-mcp"],
        ),
    ]
    monkeypatch.setattr(service, "list_authorizable_skills", AsyncMock(return_value=skills))
    monkeypatch.setattr(
        service,
        "get_authorizable_mcp_server",
        AsyncMock(
            return_value=SimpleNamespace(
                resource_id="skill-mcp",
                share_config=sharing(scope("department", [1])),
            )
        ),
    )

    with pytest.raises(ValueError, match="Skill MCP 资源未覆盖"):
        await service.authorize_agent_config_resources(
            {"context": {"skills": ["parent"], "preload_skills": ["parent"]}},
            db=Mock(),
            user=SimpleNamespace(uid="operator"),
            agent_share_config=sharing(scope("global")),
            owner_uid="owner",
        )


async def test_service_rejects_missing_transitive_skill_and_invalid_preload(monkeypatch):
    skills = [
        SimpleNamespace(
            slug="parent",
            source_scope="shared",
            created_by="owner",
            share_config=sharing(scope("global")),
            skill_dependencies=["missing"],
            mcp_dependencies=[],
        )
    ]
    monkeypatch.setattr(service, "list_authorizable_skills", AsyncMock(return_value=skills))

    with pytest.raises(ValueError, match="依赖不存在"):
        await service.authorize_agent_config_resources(
            {"context": {"skills": ["parent"]}},
            db=Mock(),
            user=SimpleNamespace(uid="operator"),
            agent_share_config=sharing(scope("user", ["owner"])),
            owner_uid="owner",
        )

    skills[0].skill_dependencies = []
    with pytest.raises(ValueError, match="preload_skills"):
        await service.authorize_agent_config_resources(
            {"context": {"skills": ["parent"], "preload_skills": ["other"]}},
            db=Mock(),
            user=SimpleNamespace(uid="operator"),
            agent_share_config=sharing(scope("user", ["owner"])),
            owner_uid="owner",
        )

    result = await service.authorize_agent_config_resources(
        {"context": {"preload_skills": ["parent"]}},
        db=Mock(),
        user=SimpleNamespace(uid="operator"),
        agent_share_config=sharing(scope("user", ["owner"])),
        owner_uid="owner",
    )
    assert result["context"]["skills"] == ["parent"]
    assert result["context"]["preload_skills"] == ["parent"]


@pytest.mark.parametrize("operation", ["create", "update"])
async def test_repository_rejects_missing_operator_before_mutation(operation):
    db = SimpleNamespace(add=Mock(), commit=AsyncMock())
    repo = AgentRepository(db)
    agent = SimpleNamespace(name="unchanged")
    with pytest.raises(ValueError, match="当前操作者"):
        if operation == "create":
            await repo.create(name="blocked", backend_id="ChatbotAgent", config_json={"context": {"model": "x"}})
        else:
            await repo.update(agent, name="blocked")
    assert agent.name == "unchanged"
    db.add.assert_not_called()
    db.commit.assert_not_awaited()


@pytest.mark.parametrize("operation", ["create", "update"])
async def test_repository_rejects_uncovered_reference_without_http(resources, operation):
    db = SimpleNamespace(add=Mock(), commit=AsyncMock())
    repo = AgentRepository(db)
    operator = SimpleNamespace(uid="owner", role="superadmin")
    agent = SimpleNamespace(
        slug="custom",
        name="unchanged",
        created_by="owner",
        config_json={"context": {"model": "legacy:chat"}},
        share_config=sharing(scope("department", [1])),
    )
    before = deepcopy(vars(agent))
    with pytest.raises(ValueError, match="完整读取范围"):
        if operation == "create":
            await repo.create(
                name="blocked",
                backend_id="ChatbotAgent",
                creator=operator,
                config_json=agent.config_json,
                share_config=sharing(scope("global")),
            )
        else:
            await repo.update(
                agent,
                name="blocked",
                updater=operator,
                share_config=sharing(scope("global")),
            )
    assert vars(agent) == before
    db.add.assert_not_called()
    db.commit.assert_not_awaited()
