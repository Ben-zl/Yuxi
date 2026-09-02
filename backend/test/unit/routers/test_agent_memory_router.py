"""Agent 长期记忆公开 API 与删除生命周期测试。"""

from __future__ import annotations

import importlib
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.utils.auth_middleware import get_db, get_required_user
from yuxi.agentscope.client import AgentScopeServiceError

agent_router_module = importlib.import_module("server.routers.agent_router")


def _user():
    return SimpleNamespace(uid="user-1", role="user", department_id=1)


def _agent(slug: str):
    return SimpleNamespace(
        id=1,
        slug=slug,
        name="Memory Agent",
        backend_id="ChatbotAgent",
        description="",
        icon=None,
        pics=[],
        config_json={},
        share_config={},
        is_default=False,
        is_subagent=False,
    )


class _FakeResult:
    def all(self):
        return []


class _FakeDb:
    async def execute(self, _statement):
        return _FakeResult()


class _FakeRepo:
    visible = True
    events: list[str] = []

    def __init__(self, _db):
        pass

    async def get_visible_by_slug(self, *, slug, user, kind="main"):
        del user, kind
        return _agent(slug) if self.visible else None

    async def delete(self, *, agent):
        del agent
        self.events.append("agent_deleted")


class _FakeMemoryClient:
    def __init__(self):
        self.events: list[str] = []
        self.error: AgentScopeServiceError | None = None

    async def list_memories(self, uid, agent_slug, **params):
        self.events.append(f"list:{uid}:{agent_slug}:{params['page']}")
        return {
            "items": [],
            "pagination": {"page": 1, "page_size": 20, "total": 0},
            "scope": {"enabled": False, "last_memory_at": None},
        }

    async def delete_memory_item(self, uid, agent_slug, memory_id):
        self.events.append(f"delete:{uid}:{agent_slug}:{memory_id}")
        if self.error:
            raise self.error

    async def clear_memory_scope(self, uid, agent_slug):
        del uid, agent_slug
        if self.error:
            raise self.error

    async def clear_memory_agent(self, uid, agent_slug):
        del uid, agent_slug
        self.events.append("memory_cleared")
        if self.error:
            raise self.error


def _build_client(monkeypatch, memory_client: _FakeMemoryClient, *, uid: str = "user-1") -> TestClient:
    monkeypatch.setattr(agent_router_module, "AgentRepository", _FakeRepo)
    monkeypatch.setattr(agent_router_module, "_memory_client", lambda: memory_client)
    monkeypatch.setattr(agent_router_module, "user_can_manage_agent", lambda _user, _agent: True)
    monkeypatch.setattr(agent_router_module, "is_builtin_agent", lambda _agent: False)

    app = FastAPI()
    app.include_router(agent_router_module.agent_router, prefix="/api")

    async def fake_db():
        return _FakeDb()

    async def fake_user():
        user = _user()
        user.uid = uid
        return user

    app.dependency_overrides[get_db] = fake_db
    app.dependency_overrides[get_required_user] = fake_user
    return TestClient(app)


def test_visible_agent_memory_remains_manageable_when_disabled(monkeypatch):
    """Memory 关闭后公开 API 仍返回保留 scope 的管理状态。"""
    _FakeRepo.visible = True
    memory_client = _FakeMemoryClient()
    client = _build_client(monkeypatch, memory_client)

    response = client.get("/api/agent/shared-agent/memories")

    assert response.status_code == 200
    assert response.json()["scope"]["enabled"] is False
    assert memory_client.events == ["list:user-1:shared-agent:1"]


def test_invisible_agent_memory_is_not_exposed(monkeypatch):
    """长期记忆沿用 Agent 可见性边界，不能通过 slug 越权读取。"""
    _FakeRepo.visible = False
    memory_client = _FakeMemoryClient()
    client = _build_client(monkeypatch, memory_client)

    response = client.get("/api/agent/private-agent/memories")

    assert response.status_code == 404
    assert memory_client.events == []


def test_same_visible_agent_forwards_each_authenticated_user_scope(monkeypatch):
    """同一共享 Agent 的查询只能携带各自登录用户 UID，接口不能指定目标用户。"""
    _FakeRepo.visible = True
    memory_client = _FakeMemoryClient()

    response_a = _build_client(monkeypatch, memory_client, uid="user-a").get("/api/agent/shared-agent/memories")
    response_b = _build_client(monkeypatch, memory_client, uid="user-b").get("/api/agent/shared-agent/memories")

    assert response_a.status_code == 200
    assert response_b.status_code == 200
    assert memory_client.events == [
        "list:user-a:shared-agent:1",
        "list:user-b:shared-agent:1",
    ]


def test_memory_busy_error_maps_to_stable_public_protocol(monkeypatch):
    """内部 409 在公开 API 中保持 memory_scope_busy。"""
    _FakeRepo.visible = True
    memory_client = _FakeMemoryClient()
    memory_client.error = AgentScopeServiceError("busy", status_code=409)
    client = _build_client(monkeypatch, memory_client)

    response = client.delete("/api/agent/shared-agent/memories/" + "a" * 64)

    assert response.status_code == 409
    assert response.json()["detail"] == "memory_scope_busy"


def test_invalid_memory_id_is_rejected_before_internal_service_call(monkeypatch):
    """公开 API 只接受 SHA-256 memory_id，非法值不能进入内部服务。"""
    _FakeRepo.visible = True
    memory_client = _FakeMemoryClient()
    client = _build_client(monkeypatch, memory_client)

    response = client.delete("/api/agent/shared-agent/memories/not-a-sha256")

    assert response.status_code == 422
    assert memory_client.events == []


def test_invalid_memory_filters_and_pagination_are_rejected_locally(monkeypatch):
    """枚举值和分页范围应由公开 API 拒绝，不能把非法查询转发到内部服务。"""
    _FakeRepo.visible = True
    memory_client = _FakeMemoryClient()
    client = _build_client(monkeypatch, memory_client)

    responses = [
        client.get("/api/agent/shared-agent/memories?kind=transcript"),
        client.get("/api/agent/shared-agent/memories?category=metadata"),
        client.get("/api/agent/shared-agent/memories?page=0"),
        client.get("/api/agent/shared-agent/memories?page_size=101"),
    ]

    assert [response.status_code for response in responses] == [422, 422, 422, 422]
    assert memory_client.events == []


def test_agent_delete_cleans_memory_before_business_record(monkeypatch):
    """Agent 删除必须先清理所有用户 scope，再删除业务记录。"""
    _FakeRepo.visible = True
    _FakeRepo.events = []
    memory_client = _FakeMemoryClient()
    client = _build_client(monkeypatch, memory_client)

    response = client.delete("/api/agent/custom-agent")

    assert response.status_code == 200
    assert memory_client.events == ["memory_cleared"]
    assert _FakeRepo.events == ["agent_deleted"]


def test_agent_delete_stops_when_memory_cleanup_fails(monkeypatch):
    """长期记忆清理失败时不得继续删除 Agent。"""
    _FakeRepo.visible = True
    _FakeRepo.events = []
    memory_client = _FakeMemoryClient()
    memory_client.error = AgentScopeServiceError("unavailable", status_code=500)
    client = _build_client(monkeypatch, memory_client)

    response = client.delete("/api/agent/custom-agent")

    assert response.status_code == 502
    assert _FakeRepo.events == []
