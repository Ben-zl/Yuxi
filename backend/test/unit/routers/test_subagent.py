from __future__ import annotations

import importlib
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from server.utils.auth_middleware import get_admin_user, get_db, get_required_user

agent_router_module = importlib.import_module("server.routers.agent_router")
resource_service = importlib.import_module("yuxi.permissions.agent_config_resource")


@pytest.fixture(autouse=True)
def empty_implicit_resources(monkeypatch):
    async def no_skills(*_args, **_kwargs):
        return []

    async def no_mcps(*_args, **_kwargs):
        return []

    monkeypatch.setattr(resource_service, "list_authorizable_skills", no_skills)
    monkeypatch.setattr(resource_service, "list_authorizable_mcp_servers", no_mcps)


def _user(role: str = "admin"):
    uid = "admin" if role in {"admin", "superadmin"} else "user"
    return SimpleNamespace(uid=uid, role=role, department_id=1)


def _agent(slug: str, *, backend_id: str = "ChatbotAgent", is_subagent: bool = False):
    return SimpleNamespace(
        id=slug,
        slug=slug,
        name=slug,
        backend_id=backend_id,
        description="",
        icon=None,
        pics=[],
        config_json={},
        share_config={
            "version": 2,
            "read_scope": {
                "access_level": "user",
                "department_ids": [],
                "user_uids": ["admin"],
            },
            "manage_scope": None,
        },
        is_default=False,
        is_subagent=is_subagent,
        created_by="admin",
        can_manage=True,
    )


class _FakeAgentManager:
    def get_agent(self, backend_id: str):
        if backend_id in {"ChatbotAgent", "SubAgentBackend"}:
            return SimpleNamespace(context_schema=None)
        return None


class _ListRepo:
    items = [
        _agent("chatbot", backend_id="ChatbotAgent"),
        _agent("worker", backend_id="SubAgentBackend", is_subagent=True),
    ]
    include_subagent_definition_calls: list[bool] = []
    get_definition_calls: list[str] = []

    def __init__(self, _db):
        pass

    async def ensure_default_agent(self):
        return self.items[0]

    async def list_visible(self, *, user, include_subagent_definitions: bool = False):
        del user
        self.include_subagent_definition_calls.append(include_subagent_definitions)
        if include_subagent_definitions:
            return self.items
        return [item for item in self.items if not item.is_subagent]

    async def get_visible_by_slug(self, *, slug, user, kind="main"):
        del user
        if kind == "any":
            self.get_definition_calls.append(slug)
            return next((item for item in self.items if item.slug == slug), None)
        if kind == "subagent":
            return next((item for item in self.items if item.slug == slug and item.is_subagent), None)
        return next((item for item in self.items if item.slug == slug and not item.is_subagent), None)

    async def serialize(self, item, **_kwargs):
        return dict(item.__dict__)


class _CreateRepo(_ListRepo):
    created_payload = None

    async def create(self, **kwargs):
        type(self).created_payload = kwargs
        return _agent(kwargs["slug"], backend_id=kwargs["backend_id"], is_subagent=kwargs["is_subagent"])


class _RejectingCreateRepo(_ListRepo):
    async def create(self, **_kwargs):
        raise ValueError("SubAgentBackend 与 is_subagent 必须保持一致")


class _UpdateRepo(_ListRepo):
    items = [_agent("chatbot", backend_id="ChatbotAgent")]
    update_called = False

    async def update(self, *_args, **_kwargs):
        type(self).update_called = True
        raise AssertionError("资源授权失败时不应写入 Agent")


def _build_app(monkeypatch, repo_cls, *, role: str = "admin") -> TestClient:
    monkeypatch.setattr(agent_router_module, "agent_manager", _FakeAgentManager())
    monkeypatch.setattr(agent_router_module, "AgentRepository", repo_cls)

    app = FastAPI()
    app.include_router(agent_router_module.agent_router, prefix="/api")

    async def fake_db():
        return None

    async def fake_user():
        return _user(role)

    app.dependency_overrides[get_db] = fake_db
    app.dependency_overrides[get_required_user] = fake_user
    app.dependency_overrides[get_admin_user] = fake_user
    return TestClient(app)


def test_agent_list_excludes_subagents_by_default(monkeypatch):
    _ListRepo.include_subagent_definition_calls = []
    client = _build_app(monkeypatch, _ListRepo)

    response = client.get("/api/agent")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert [agent["slug"] for agent in payload["agents"]] == ["chatbot"]
    assert _ListRepo.include_subagent_definition_calls == [False]


def test_agent_management_list_can_include_subagents(monkeypatch):
    _ListRepo.include_subagent_definition_calls = []
    client = _build_app(monkeypatch, _ListRepo)

    response = client.get("/api/agent?include_subagents=true")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert [agent["slug"] for agent in payload["agents"]] == ["chatbot", "worker"]
    assert payload["agents"][1]["is_subagent"] is True
    assert _ListRepo.include_subagent_definition_calls == [True]


def test_agent_detail_can_load_subagent_definition(monkeypatch):
    _ListRepo.get_definition_calls = []
    client = _build_app(monkeypatch, _ListRepo)

    response = client.get("/api/agent/worker")

    assert response.status_code == 200, response.text
    assert response.json()["agent"]["slug"] == "worker"
    assert response.json()["agent"]["is_subagent"] is True
    assert _ListRepo.get_definition_calls == ["worker"]


def test_normal_user_can_create_agent(monkeypatch):
    _CreateRepo.created_payload = None
    client = _build_app(monkeypatch, _CreateRepo, role="user")

    response = client.post(
        "/api/agent",
        json={
            "name": "Personal Bot",
            "slug": "personal-bot",
            "backend_id": "ChatbotAgent",
            "share_config": {
                "version": 2,
                "read_scope": {"access_level": "user", "user_uids": ["user"]},
                "manage_scope": None,
            },
        },
    )

    assert response.status_code == 200, response.text
    assert _CreateRepo.created_payload["created_by"] == "user"
    assert _CreateRepo.created_payload["share_config"] == {
        "version": 2,
        "read_scope": {"access_level": "user", "department_ids": [], "user_uids": ["user"]},
        "manage_scope": None,
    }


def test_create_agent_canonicalizes_visible_model_and_mcp_references(monkeypatch):
    _CreateRepo.created_payload = None
    global_share = {
        "version": 2,
        "read_scope": {"access_level": "global", "department_ids": [], "user_uids": []},
        "manage_scope": None,
    }
    provider = SimpleNamespace(resource_id="model-resource", is_enabled=True, share_config=global_share)
    mcp_server = SimpleNamespace(resource_id="mcp-resource", share_config=global_share)

    model_types = {
        "model-resource:chat": "chat",
        "model-resource:embedding": "embedding",
        "model-resource:rerank": "rerank",
    }
    monkeypatch.setattr(
        resource_service.model_cache,
        "canonicalize_spec",
        lambda spec: f"model-resource:{spec.split(':', 1)[1]}",
    )
    monkeypatch.setattr(
        resource_service.model_cache,
        "get_model_info",
        lambda spec: SimpleNamespace(
            resource_id="model-resource",
            provider_id="legacy-provider",
            model_type=model_types[spec],
        ),
    )

    async def fake_get_model_provider_for_user(_db, resource_id, user):
        assert resource_id == "model-resource"
        assert user.uid == "admin"
        return provider

    async def fake_get_authorizable_mcp_server(db, reference, *, user):
        assert reference == "legacy-mcp"
        assert db is None
        assert user.uid == "admin"
        return mcp_server

    monkeypatch.setattr(resource_service, "get_model_provider_for_user", fake_get_model_provider_for_user)
    monkeypatch.setattr(resource_service, "get_authorizable_mcp_server", fake_get_authorizable_mcp_server)

    async def no_skills(*_args, **_kwargs):
        return []

    monkeypatch.setattr(resource_service, "list_authorizable_skills", no_skills)

    client = _build_app(monkeypatch, _CreateRepo)
    response = client.post(
        "/api/agent",
        json={
            "name": "Scoped Bot",
            "slug": "scoped-bot",
            "backend_id": "ChatbotAgent",
            "config_json": {
                "context": {
                    "model": "legacy-provider:chat",
                    "embedding_model_spec": "legacy-provider:embedding",
                    "reranker_model": "legacy-provider:rerank",
                    "mcps": ["legacy-mcp"],
                }
            },
        },
    )

    assert response.status_code == 200, response.text
    assert _CreateRepo.created_payload["config_json"]["context"] == {
        "model": "model-resource:chat",
        "embedding_model_spec": "model-resource:embedding",
        "reranker_model": "model-resource:rerank",
        "skills": [],
        "mcps": ["mcp-resource"],
    }


def test_create_agent_rejects_resource_not_covering_full_read_scope(monkeypatch):
    _CreateRepo.created_payload = None
    department_share = {
        "version": 2,
        "read_scope": {"access_level": "department", "department_ids": [1], "user_uids": []},
        "manage_scope": None,
    }
    model_info = SimpleNamespace(resource_id="department-model", provider_id="provider", model_type="chat")
    provider = SimpleNamespace(resource_id="department-model", is_enabled=True, share_config=department_share)

    monkeypatch.setattr(resource_service.model_cache, "canonicalize_spec", lambda spec: "department-model:chat")
    monkeypatch.setattr(resource_service.model_cache, "get_model_info", lambda spec: model_info)

    async def fake_get_model_provider_for_user(_db, _resource_id, _user):
        return provider

    monkeypatch.setattr(resource_service, "get_model_provider_for_user", fake_get_model_provider_for_user)

    client = _build_app(monkeypatch, _CreateRepo, role="superadmin")
    response = client.post(
        "/api/agent",
        json={
            "name": "Global Bot",
            "slug": "global-bot",
            "backend_id": "ChatbotAgent",
            "share_config": {
                "version": 2,
                "read_scope": {"access_level": "global"},
                "manage_scope": None,
            },
            "config_json": {"context": {"model": "department-model:chat"}},
        },
    )

    assert response.status_code == 422
    assert "完整读取范围" in response.json()["detail"]
    assert _CreateRepo.created_payload is None


def test_create_agent_rejects_ambiguous_or_invisible_legacy_mcp_slug(monkeypatch):
    _CreateRepo.created_payload = None

    async def fake_get_authorizable_mcp_server(db, _reference, *, user):
        assert db is None
        assert user.uid == "admin"
        return None

    monkeypatch.setattr(resource_service, "get_authorizable_mcp_server", fake_get_authorizable_mcp_server)

    async def no_skills(*_args, **_kwargs):
        return []

    monkeypatch.setattr(resource_service, "list_authorizable_skills", no_skills)

    client = _build_app(monkeypatch, _CreateRepo)
    response = client.post(
        "/api/agent",
        json={
            "name": "Invalid MCP Bot",
            "slug": "invalid-mcp-bot",
            "backend_id": "ChatbotAgent",
            "config_json": {"context": {"mcps": ["duplicate-or-cross-department"]}},
        },
    )

    assert response.status_code == 422
    assert "不可访问或旧 slug 存在歧义" in response.json()["detail"]
    assert _CreateRepo.created_payload is None


def test_update_agent_rechecks_existing_resources_before_expanding_read_scope(monkeypatch):
    _UpdateRepo.update_called = False
    _UpdateRepo.items[0].config_json = {"context": {"model": "department-model:chat"}}
    department_share = {
        "version": 2,
        "read_scope": {"access_level": "department", "department_ids": [1], "user_uids": []},
        "manage_scope": None,
    }
    model_info = SimpleNamespace(resource_id="department-model", provider_id="provider", model_type="chat")
    provider = SimpleNamespace(resource_id="department-model", is_enabled=True, share_config=department_share)

    monkeypatch.setattr(resource_service.model_cache, "canonicalize_spec", lambda spec: spec)
    monkeypatch.setattr(resource_service.model_cache, "get_model_info", lambda spec: model_info)

    async def fake_get_model_provider_for_user(_db, _resource_id, _user):
        return provider

    monkeypatch.setattr(resource_service, "get_model_provider_for_user", fake_get_model_provider_for_user)

    client = _build_app(monkeypatch, _UpdateRepo, role="superadmin")
    response = client.put(
        "/api/agent/chatbot",
        json={
            "share_config": {
                "version": 2,
                "read_scope": {"access_level": "global"},
                "manage_scope": None,
            }
        },
    )

    assert response.status_code == 422
    assert "完整读取范围" in response.json()["detail"]
    assert _UpdateRepo.update_called is False


def test_create_subagent_backend_agent_sets_subagent_flag(monkeypatch):
    _CreateRepo.created_payload = None
    client = _build_app(monkeypatch, _CreateRepo)

    response = client.post(
        "/api/agent",
        json={
            "name": "Worker",
            "slug": "worker",
            "backend_id": "SubAgentBackend",
            "is_subagent": True,
        },
    )

    assert response.status_code == 200, response.text
    assert _CreateRepo.created_payload["backend_id"] == "SubAgentBackend"
    assert _CreateRepo.created_payload["is_subagent"] is True
    assert response.json()["agent"]["is_subagent"] is True


def test_create_subagent_backend_rejects_mismatched_flag(monkeypatch):
    client = _build_app(monkeypatch, _RejectingCreateRepo)

    response = client.post(
        "/api/agent",
        json={
            "name": "Worker",
            "slug": "worker",
            "backend_id": "SubAgentBackend",
            "is_subagent": False,
        },
    )

    assert response.status_code == 422
    assert "is_subagent" in response.json()["detail"]
