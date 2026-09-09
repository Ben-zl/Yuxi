"""WeKnora 远端服务部署配置与 HTTP 客户端基座。"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from typing import Any

import httpx

WEKNORA_MODEL_ENV_KEYS = ("WEKNORA_EMBEDDING_MODEL_ID", "WEKNORA_SUMMARY_MODEL_ID")
DEFAULT_TIMEOUT_SECONDS = 30.0

# 托管绑定状态:confirmed = 双侧确认;pending_review = 远端结果不确定,保留核对
BINDING_CONFIRMED = "confirmed"
BINDING_PENDING_REVIEW = "pending_review"


@dataclass(frozen=True)
class WeKnoraSettings:
    """服务端持有的 WeKnora 部署配置;API Key 仅用于出站请求,不进入日志与响应。"""

    base_url: str
    api_key: str = field(repr=False, default="")
    embedding_model_id: str = ""
    summary_model_id: str = ""

    @property
    def ready(self) -> bool:
        return bool(self.base_url and self.api_key)

    @property
    def missing_model_fields(self) -> list[str]:
        """返回缺失的模型配置变量名,供建库等需要模型标识的用例做前置校验。"""
        return [name for name in WEKNORA_MODEL_ENV_KEYS if not getattr(self, _env_key_to_attr(name))]


def _env_key_to_attr(name: str) -> str:
    return name.removeprefix("WEKNORA_").lower()


def load_weknora_settings() -> WeKnoraSettings:
    """从进程环境读取 WeKnora 部署配置并统一去空白。"""

    return WeKnoraSettings(
        base_url=os.environ.get("WEKNORA_BASE_URL", "").strip(),
        api_key=os.environ.get("WEKNORA_API_KEY", "").strip(),
        embedding_model_id=os.environ.get("WEKNORA_EMBEDDING_MODEL_ID", "").strip(),
        summary_model_id=os.environ.get("WEKNORA_SUMMARY_MODEL_ID", "").strip(),
    )


def weknora_instance_fingerprint(settings: WeKnoraSettings | None = None) -> str:
    """以规范化部署地址哈希作为远端实例指纹。

    绑定校验用:变更服务地址后旧绑定立即失效,API Key 轮换(地址不变)不影响绑定。
    """

    effective = settings or load_weknora_settings()
    normalized = effective.base_url.strip().rstrip("/").lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


class WeKnoraClientError(Exception):
    """WeKnora 调用失败;message 只含方法、路径、状态与响应摘要,不含 API Key。"""

    def __init__(self, message: str, *, status_code: int | None = None, body: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class WeKnoraClient:
    """WeKnora OpenAPI 客户端基座,统一认证头、超时与错误透传。"""

    def __init__(
        self,
        settings: WeKnoraSettings,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._settings = settings
        self._timeout = timeout
        self._transport = transport

    @property
    def settings(self) -> WeKnoraSettings:
        return self._settings

    def _endpoint(self, path: str) -> str:
        return f"{self._settings.base_url.rstrip('/')}/{path.lstrip('/')}"

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {"X-API-Key": self._settings.api_key}
        if extra:
            headers.update(extra)
        return headers

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: Any | None = None,
        params: dict[str, Any] | None = None,
        content: bytes | None = None,
        files: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        """执行一次远端请求;传输失败与非 2xx 统一转为 WeKnoraClientError。

        请求可能已改变远端状态,调用方收到超时/传输错误时不得自动重试。
        """

        if not self._settings.ready:
            raise WeKnoraClientError("WeKnora 部署配置不完整,无法发起远端调用")
        url = self._endpoint(path)
        try:
            async with httpx.AsyncClient(timeout=timeout or self._timeout, transport=self._transport) as client:
                response = await client.request(
                    method,
                    url,
                    json=json,
                    params=params,
                    content=content,
                    files=files,
                    data=data,
                    headers=self._headers(headers),
                )
        except httpx.HTTPError as error:
            raise WeKnoraClientError(f"WeKnora 请求失败: {method} {path}: {type(error).__name__}") from error
        # 非 2xx 一律视为错误:不跟随重定向,3xx 会以含混的解析错误泄漏到下游
        if not 200 <= response.status_code < 300:
            raise WeKnoraClientError(
                f"WeKnora HTTP 错误: status={response.status_code}, {method} {path}",
                status_code=response.status_code,
                body=self._sanitize_body(response.text[:1000]),
            )
        return response

    def _sanitize_body(self, body: str) -> str:
        """远端错误体若回显 API Key 则就地脱敏,防止密钥进入日志。"""

        api_key = self._settings.api_key
        if api_key and api_key in body:
            return body.replace(api_key, "***")
        return body
