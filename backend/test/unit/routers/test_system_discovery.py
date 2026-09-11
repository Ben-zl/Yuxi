"""能力发现端点的知识库后端字段测试。"""

from __future__ import annotations

import pytest

from server.routers.system_router import discovery

pytestmark = pytest.mark.unit
CREDENTIAL_KEY = "uAIxxMDb8OeoGaM5SsqnFfr6QLFLtmYzXVaOYRHj2vU="


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LITE_MODE", raising=False)
    monkeypatch.delenv("KNOWLEDGE_BACKEND", raising=False)
    for name in ("WEKNORA_BASE_URL", "WEKNORA_API_KEY"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.asyncio
async def test_discovery_reports_builtin_backend_by_default(monkeypatch) -> None:
    _clear_env(monkeypatch)

    payload = await discovery()
    features = payload["capabilities"]["features"]

    assert features["knowledge"] is True
    assert features["knowledge_backend"] == "builtin"
    assert features["knowledge_backend_ready"] is True
    assert "knowledge_backend_config_error" not in features
    assert payload["capabilities"]["cli"]["kb_list"] is True


@pytest.mark.asyncio
async def test_discovery_reports_weknora_backend_when_ready(monkeypatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("KNOWLEDGE_BACKEND", "weknora")
    monkeypatch.setenv("WEKNORA_BASE_URL", "http://weknora-app:8080/api/v1")
    monkeypatch.setenv("WEKNORA_API_KEY", "sk-local")
    monkeypatch.setenv("WEKNORA_WORKSPACE_CREDENTIAL_KEY", CREDENTIAL_KEY)

    payload = await discovery()
    features = payload["capabilities"]["features"]

    assert features["knowledge"] is True
    assert features["knowledge_backend"] == "weknora"
    assert features["knowledge_backend_ready"] is True


@pytest.mark.asyncio
async def test_discovery_reports_knowledge_disabled_with_missing_config_reason(monkeypatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("KNOWLEDGE_BACKEND", "weknora")
    # 只缺 API Key;已配置的值不得出现在错误说明中
    monkeypatch.setenv("WEKNORA_BASE_URL", "http://secret-weknora-host:8080/api/v1")

    payload = await discovery()
    features = payload["capabilities"]["features"]

    assert features["knowledge"] is False
    assert features["knowledge_backend"] == "weknora"
    assert features["knowledge_backend_ready"] is False
    # 错误说明只包含缺失变量名,不包含任何配置值
    assert "WEKNORA_API_KEY" in features["knowledge_backend_config_error"]
    assert "WEKNORA_BASE_URL" not in features["knowledge_backend_config_error"]
    assert "secret-weknora-host" not in features["knowledge_backend_config_error"]
    assert payload["capabilities"]["cli"]["kb_query"] is False


@pytest.mark.asyncio
async def test_discovery_keeps_lite_mode_semantics(monkeypatch) -> None:
    monkeypatch.setenv("LITE_MODE", "true")
    monkeypatch.setenv("KNOWLEDGE_BACKEND", "weknora")
    monkeypatch.setenv("WEKNORA_BASE_URL", "http://weknora-app:8080/api/v1")
    monkeypatch.setenv("WEKNORA_API_KEY", "sk-local")
    monkeypatch.setenv("WEKNORA_WORKSPACE_CREDENTIAL_KEY", CREDENTIAL_KEY)

    payload = await discovery()
    features = payload["capabilities"]["features"]

    assert features["knowledge"] is False
    # LITE 关闭能力,但后端配置完整时不产生误导性的配置错误提示
    assert "knowledge_backend_config_error" not in features
