from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from yuxi.agents.mcp.service import MCPServerNotFoundError
from yuxi.storage.postgres.models_business import MCPServer, User

from server.routers.mcp_router import mcp
from server.utils.auth_middleware import get_admin_user, get_db, get_required_user


def _build_app(*, allow_admin: bool = True, superadmin: bool = False) -> FastAPI:
    app = FastAPI()
    app.include_router(mcp, prefix="/api")

    async def fake_db():
        return None

    async def fake_admin_user():
        if not allow_admin:
            from fastapi import HTTPException

            raise HTTPException(status_code=403, detail="需要管理员权限")
        return User(
            username="admin",
            uid="admin",
            password_hash="x",
            role="superadmin" if superadmin else "admin",
        )

    async def fake_required_user():
        return User(
            username="admin" if allow_admin else "user",
            uid="admin" if allow_admin else "user",
            password_hash="x",
            role=("superadmin" if superadmin else "admin") if allow_admin else "user",
        )

    app.dependency_overrides[get_db] = fake_db
    app.dependency_overrides[get_admin_user] = fake_admin_user
    app.dependency_overrides[get_required_user] = fake_required_user
    return app


def test_update_mcp_server_status(monkeypatch):
    captured = {}

    class DummyServer:
        def __init__(self, enabled):
            self.slug = "demo-mcp"
            self.transport = "streamable_http"
            self.enabled = enabled

        def to_dict(self, *, sanitize=False):
            return {"name": "demo-mcp", "enabled": self.enabled}

    async def fake_set_server_enabled(db, name, enabled, updated_by=None, *, operator=None, resource_only=False):
        captured["name"] = name
        captured["enabled"] = enabled
        captured["updated_by"] = updated_by
        return enabled, DummyServer(enabled)

    monkeypatch.setattr("server.routers.mcp_router.set_server_enabled", fake_set_server_enabled)

    client = TestClient(_build_app())
    resp = client.put("/api/system/mcp-servers/demo-mcp/status", json={"enabled": False})
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["success"] is True
    assert payload["enabled"] is False
    assert payload["data"]["enabled"] is False
    assert captured == {"name": "demo-mcp", "enabled": False, "updated_by": "admin"}


def test_update_mcp_server_status_not_found(monkeypatch):
    async def fake_set_server_enabled(db, name, enabled, updated_by=None, *, operator=None, resource_only=False):
        raise MCPServerNotFoundError(f"Server '{name}' does not exist")

    monkeypatch.setattr("server.routers.mcp_router.set_server_enabled", fake_set_server_enabled)

    client = TestClient(_build_app())
    resp = client.put("/api/system/mcp-servers/missing/status", json={"enabled": True})
    assert resp.status_code == 404, resp.text


def test_update_mcp_server_status_rejects_legacy_stdio(monkeypatch):
    async def fake_set_server_enabled(db, name, enabled, updated_by=None, *, operator=None, resource_only=False):
        raise ValueError("历史 stdio MCP 已被禁用")

    monkeypatch.setattr("server.routers.mcp_router.set_server_enabled", fake_set_server_enabled)

    client = TestClient(_build_app())
    resp = client.put("/api/system/mcp-servers/legacy-stdio/status", json={"enabled": True})
    assert resp.status_code == 400, resp.text


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/api/system/mcp-servers/global-resource/test"),
        ("get", "/api/system/mcp-servers/global-resource/tools"),
        ("post", "/api/system/mcp-servers/global-resource/tools/refresh"),
    ],
)
def test_department_admin_cannot_run_management_probe_on_global_mcp(monkeypatch, method, path):
    server = MCPServer(
        resource_id="global-resource",
        slug="global",
        name="Global MCP",
        transport="streamable_http",
        url="https://example.test/mcp",
        enabled=1,
        created_by="root",
        updated_by="root",
        share_config={
            "version": 2,
            "read_scope": {"access_level": "global", "department_ids": [], "user_uids": []},
            "manage_scope": None,
        },
    )

    async def read_server(*_args, **_kwargs):
        return server

    monkeypatch.setattr("server.routers.mcp_router.get_server_or_404", read_server)
    response = getattr(TestClient(_build_app()), method)(path)
    assert response.status_code == 403, response.text


@pytest.mark.parametrize(
    "role,operation",
    [
        (role, operation)
        for role in ["user", "admin", "superadmin"]
        for operation in ["list", "detail", "create", "update", "status"]
        if role != "user" or operation in {"list", "detail"}
    ],
)
@pytest.mark.parametrize(
    "url", ["https://example.test/mcp", "https://user:secret@example.test/mcp?token=secret#secret"]
)
def test_mcp_http_outputs_use_real_sanitized_serializer(monkeypatch, role, operation, url):
    """真实 ORM 序列化覆盖所有配置响应，写入仍能接收新凭据。"""
    server = MCPServer(
        resource_id="resource-one",
        slug="same",
        name="Test MCP",
        transport="streamable_http",
        url=url,
        command="fixture-command",
        args=["fixture-arg"],
        env={"KEY": "fixture-env"},
        headers={"Authorization": "fixture-header"},
        enabled=1,
        created_by="admin",
        updated_by="admin",
    )
    captured = {}

    async def read_all(db, **kwargs):
        return [server]

    async def read_one(*args, **kwargs):
        return server

    async def write(*args, **kwargs):
        captured.update(kwargs)
        if kwargs.get("headers") is not None:
            server.headers = kwargs["headers"]
        return server

    async def status(*args, **kwargs):
        return True, server

    monkeypatch.setattr("server.routers.mcp_router.get_all_mcp_servers", read_all)
    monkeypatch.setattr("server.routers.mcp_router.get_server_or_404", read_one)
    monkeypatch.setattr("server.routers.mcp_router.create_mcp_server", write)
    monkeypatch.setattr("server.routers.mcp_router.update_mcp_server", write)
    monkeypatch.setattr("server.routers.mcp_router.set_server_enabled", status)
    client = TestClient(_build_app(allow_admin=role != "user", superadmin=role == "superadmin"))
    base = "/api/system/mcp-servers"
    credentials = {"Authorization": "replacement-header"}
    if operation == "list":
        response = client.get(base)
    elif operation == "detail":
        response = client.get(base + "/resource-one")
    elif operation == "create":
        response = client.post(
            base,
            json={
                "slug": "same",
                "name": "Test MCP",
                "transport": "streamable_http",
                "url": "https://example.test/mcp",
                "headers": credentials,
            },
        )
    elif operation == "update":
        response = client.put(base + "/resource-one", json={"headers": credentials})
    else:
        response = client.put(base + "/resource-one/status", json={"enabled": True})
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    if operation == "list":
        data = data[0]
    assert {"headers", "env", "command", "args"}.isdisjoint(data)
    assert data["resource_id"] == "resource-one"
    assert data["credential_status"] == "configured"
    assert data["transport"] == "streamable_http"
    assert data["url_configured"] is True
    if "?" in url:
        assert "url" not in data
        assert url not in response.text
    else:
        assert data["url"] == url
    if operation in {"create", "update"}:
        assert captured["headers"] == credentials
        assert server.headers == credentials
    else:
        assert server.headers == {"Authorization": "fixture-header"}


def test_create_mcp_server_rejects_extra_config_fields():
    client = TestClient(_build_app())
    resp = client.post(
        "/api/system/mcp-servers",
        json={
            "slug": "demo-mcp",
            "name": "Demo MCP",
            "transport": "streamable_http",
            "url": "https://example.com/mcp",
            "enabled": True,
        },
    )

    assert resp.status_code == 422, resp.text


def test_mcp_unexpected_error_response_is_sanitized(monkeypatch):
    async def fail_list(*_args, **_kwargs):
        raise RuntimeError("headers={'Authorization': 'secret'} url=https://user:secret@example.test")

    monkeypatch.setattr("server.routers.mcp_router.get_all_mcp_servers", fail_list)
    response = TestClient(_build_app()).get("/api/system/mcp-servers")
    assert response.status_code == 500
    assert response.json() == {"detail": "获取 MCP 服务器失败"}
    assert "example.test" not in response.text
    assert "Authorization" not in response.text


def test_create_mcp_server_rejects_stdio_command():
    client = TestClient(_build_app())
    resp = client.post(
        "/api/system/mcp-servers",
        json={
            "slug": "unsafe-mcp",
            "name": "Unsafe MCP",
            "transport": "stdio",
            "command": "python3",
            "args": ["-c", "print('unsafe')"],
        },
    )

    assert resp.status_code == 422, resp.text


def test_update_mcp_server_rejects_extra_config_fields():
    client = TestClient(_build_app())
    resp = client.put(
        "/api/system/mcp-servers/demo-mcp",
        json={
            "name": "Demo MCP",
            "transport": "streamable_http",
            "url": "https://example.com/mcp",
            "slug": "renamed-mcp",
        },
    )

    assert resp.status_code == 422, resp.text


def test_update_builtin_mcp_server_rejects_connection_changes(monkeypatch):
    async def fake_update_mcp_server(db, slug, **kwargs):
        raise PermissionError("系统内置 MCP 的连接配置由代码管理，无法通过接口修改")

    monkeypatch.setattr("server.routers.mcp_router.update_mcp_server", fake_update_mcp_server)

    client = TestClient(_build_app())
    resp = client.put(
        "/api/system/mcp-servers/mcp-server-chart",
        json={
            "transport": "streamable_http",
            "url": "https://example.com/mcp",
        },
    )

    assert resp.status_code == 403, resp.text


def test_update_mcp_server_not_found(monkeypatch):
    async def fake_update_mcp_server(db, slug, **kwargs):
        raise MCPServerNotFoundError(f"Server '{slug}' does not exist")

    monkeypatch.setattr("server.routers.mcp_router.update_mcp_server", fake_update_mcp_server)

    client = TestClient(_build_app())
    resp = client.put(
        "/api/system/mcp-servers/missing",
        json={"name": "Missing MCP"},
    )

    assert resp.status_code == 404, resp.text
