"""WeKnora 客户端基座协议测试(传输边界注入,不触达真实远端)。"""

from __future__ import annotations

import httpx
import pytest

from yuxi.knowledge.weknora import (
    WeKnoraClient,
    WeKnoraClientError,
    WeKnoraSettings,
    load_weknora_settings,
)

pytestmark = pytest.mark.unit


def _ready_settings() -> WeKnoraSettings:
    return WeKnoraSettings(base_url="http://weknora-app:8080/api/v1/", api_key="sk-test-key")


@pytest.mark.asyncio
async def test_request_sends_api_key_header_and_joins_path() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["api_key"] = request.headers.get("x-api-key", "")
        return httpx.Response(200, json={"data": {"id": "kb-1"}})

    client = WeKnoraClient(_ready_settings(), transport=httpx.MockTransport(handler))
    response = await client.request("GET", "knowledge-bases")

    assert response.json()["data"]["id"] == "kb-1"
    assert captured["api_key"] == "sk-test-key"
    # base_url 尾部斜杠与 path 前导斜杠不能拼出双斜杠
    assert captured["url"] == "http://weknora-app:8080/api/v1/knowledge-bases"


@pytest.mark.asyncio
async def test_http_error_raises_client_error_with_status_and_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "knowledge base not found"})

    client = WeKnoraClient(_ready_settings(), transport=httpx.MockTransport(handler))

    with pytest.raises(WeKnoraClientError) as exc_info:
        await client.request("GET", "knowledge-bases/missing-id")

    assert exc_info.value.status_code == 404
    assert "knowledge base not found" in (exc_info.value.body or "")
    assert "knowledge-bases/missing-id" in str(exc_info.value)


@pytest.mark.asyncio
async def test_error_messages_never_leak_api_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal error")

    client = WeKnoraClient(_ready_settings(), transport=httpx.MockTransport(handler))

    with pytest.raises(WeKnoraClientError) as http_error:
        await client.request("POST", "knowledge-bases")
    assert "sk-test-key" not in str(http_error.value)
    assert "sk-test-key" not in (http_error.value.body or "")


@pytest.mark.asyncio
async def test_transport_failure_wrapped_as_client_error_without_retry() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        raise httpx.ConnectTimeout("timed out", request=request)

    client = WeKnoraClient(_ready_settings(), transport=httpx.MockTransport(handler))

    with pytest.raises(WeKnoraClientError, match="WeKnora 请求失败"):
        await client.request("POST", "knowledge-bases")

    # 结果不确定的写请求只发出一次,客户端基座不做自动重试
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_incomplete_settings_fail_closed_before_any_network_call() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - 不应被调用
        raise AssertionError("配置不完整时不得发起远端请求")

    client = WeKnoraClient(WeKnoraSettings(base_url="", api_key=""), transport=httpx.MockTransport(handler))

    with pytest.raises(WeKnoraClientError, match="部署配置不完整"):
        await client.request("GET", "knowledge-bases")


def test_load_weknora_settings_reads_and_strips_env(monkeypatch) -> None:
    monkeypatch.setenv("WEKNORA_BASE_URL", " http://weknora-app:8080/api/v1 ")
    monkeypatch.setenv("WEKNORA_API_KEY", " sk-local ")
    monkeypatch.setenv("WEKNORA_EMBEDDING_MODEL_ID", " embedding-1 ")
    monkeypatch.setenv("WEKNORA_SUMMARY_MODEL_ID", " summary-1 ")

    settings = load_weknora_settings()

    assert settings.base_url == "http://weknora-app:8080/api/v1"
    assert settings.api_key == "sk-local"
    assert settings.embedding_model_id == "embedding-1"
    assert settings.summary_model_id == "summary-1"
    assert settings.ready is True
    assert settings.missing_model_fields == []


def test_settings_reports_missing_model_fields(monkeypatch) -> None:
    monkeypatch.delenv("WEKNORA_EMBEDDING_MODEL_ID", raising=False)
    monkeypatch.delenv("WEKNORA_SUMMARY_MODEL_ID", raising=False)

    settings = load_weknora_settings()

    assert settings.missing_model_fields == [
        "WEKNORA_EMBEDDING_MODEL_ID",
        "WEKNORA_SUMMARY_MODEL_ID",
    ]


@pytest.mark.asyncio
async def test_raw_redirect_response_is_returned_without_following() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://objects.example/file"})

    client = WeKnoraClient(_ready_settings(), transport=httpx.MockTransport(handler))
    response = await client.request("GET", "knowledge/doc/download", raw_redirects=True)

    assert response.status_code == 302
    assert calls == ["http://weknora-app:8080/api/v1/knowledge/doc/download"]


@pytest.mark.asyncio
async def test_absolute_unauthenticated_request_sends_no_api_key() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["api_key"] = request.headers.get("x-api-key")
        return httpx.Response(200, content=b"file")

    client = WeKnoraClient(_ready_settings(), transport=httpx.MockTransport(handler))
    response = await client.request("GET", "https://objects.example/file", authenticated=False)

    assert response.content == b"file"
    assert captured == {"url": "https://objects.example/file", "api_key": None}


@pytest.mark.asyncio
async def test_absolute_authenticated_request_is_rejected_before_network() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - 不应被调用
        raise AssertionError("绝对 URL 不得携带 WeKnora 统一凭证")

    client = WeKnoraClient(_ready_settings(), transport=httpx.MockTransport(handler))

    with pytest.raises(WeKnoraClientError, match="不能发送到绝对 URL"):
        await client.request("GET", "https://objects.example/file")


@pytest.mark.asyncio
async def test_authenticated_request_cannot_enable_automatic_redirects() -> None:
    client = WeKnoraClient(_ready_settings(), transport=httpx.MockTransport(lambda request: httpx.Response(200)))

    with pytest.raises(WeKnoraClientError, match="不能自动跟随重定向"):
        await client.request("GET", "knowledge/doc/download", follow_redirects=True)
