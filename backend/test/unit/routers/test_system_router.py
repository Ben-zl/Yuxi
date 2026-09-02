from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.routers.system_router import load_info_config, system

pytestmark = pytest.mark.unit


async def test_default_info_config_uses_current_platform_name(monkeypatch):
    template = Path(__file__).parents[3] / "package/yuxi/config/static/info.template.yaml"
    monkeypatch.setenv("YUXI_BRAND_FILE_PATH", str(template))
    monkeypatch.setattr("server.routers.system_router.get_version", lambda: "0.7.2")

    config = await load_info_config()

    assert config["organization"]["name"] == "Agent智能体平台"
    assert config["footer"]["copyright"] == "© Agent智能体平台 2026 v0.7.2"


def test_discovery_endpoint_is_public(monkeypatch):
    monkeypatch.setattr("server.routers.system_router.get_version", lambda: "0.7.1.dev0")

    app = FastAPI()
    app.include_router(system, prefix="/api")
    response = TestClient(app).get("/api/system/discovery")

    assert response.status_code == 200
    payload = response.json()
    assert payload["name"] == "Yuxi"
    assert payload["version"] == "0.7.1.dev0"
    assert payload["api_prefix"] == "/api"
    assert payload["capabilities"]["cli"]["browser_login"] is True
    assert payload["capabilities"]["cli"]["api_key_auth"] is True
    assert payload["capabilities"]["cli"]["kb_upload"] is True
    assert payload["endpoints"]["cli_auth_sessions"] == "/api/auth/cli/sessions"
