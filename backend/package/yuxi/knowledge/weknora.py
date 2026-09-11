"""WeKnora 远端服务部署配置与 HTTP 客户端基座。"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx

WEKNORA_MODEL_ENV_KEYS = ("WEKNORA_EMBEDDING_MODEL_ID", "WEKNORA_SUMMARY_MODEL_ID")
WEKNORA_GRAPH_EXTRACT_ENV = "WEKNORA_GRAPH_EXTRACT_ENABLED"
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

    def with_api_key(self, api_key: str) -> WeKnoraSettings:
        """返回仅替换出站 Key 的同实例配置;用于部门 workspace 专属 Key 注入。"""

        return WeKnoraSettings(
            base_url=self.base_url,
            api_key=api_key,
            embedding_model_id=self.embedding_model_id,
            summary_model_id=self.summary_model_id,
        )

    @property
    def missing_model_fields(self) -> list[str]:
        """返回缺失的模型配置变量名,供建库等需要模型标识的用例做前置校验。"""
        return [name for name in WEKNORA_MODEL_ENV_KEYS if not getattr(self, _env_key_to_attr(name))]

    @property
    def graph_extract_enabled(self) -> bool:
        """部署是否为托管库启用远端实体图谱抽取。"""
        return os.environ.get(WEKNORA_GRAPH_EXTRACT_ENV, "").strip().lower() in {"true", "1"}


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
        if urlparse(path).scheme in {"http", "https"}:
            return path
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
        follow_redirects: bool = False,
        raw_redirects: bool = False,
        authenticated: bool = True,
    ) -> httpx.Response:
        """执行一次远端请求;传输失败与非 2xx 统一转为 WeKnoraClientError。

        请求可能已改变远端状态,调用方收到超时/传输错误时不得自动重试。
        """

        if not self._settings.ready:
            raise WeKnoraClientError("WeKnora 部署配置不完整,无法发起远端调用")
        absolute_url = urlparse(path).scheme in {"http", "https"}
        if absolute_url and authenticated:
            raise WeKnoraClientError("带认证的 WeKnora 请求不能发送到绝对 URL")
        if follow_redirects and authenticated:
            raise WeKnoraClientError("带认证的 WeKnora 请求不能自动跟随重定向")
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
                    headers=self._headers(headers) if authenticated else (headers or {}),
                    follow_redirects=follow_redirects,
                )
        except httpx.HTTPError as error:
            raise WeKnoraClientError(f"WeKnora 请求失败: {method} {path}: {type(error).__name__}") from error
        if not 200 <= response.status_code < 300 and not (raw_redirects and 300 <= response.status_code < 400):
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


# 远端 parse_status 展示映射;未知状态显式暴露,不得默认映射为完成
WEKNORA_STATUS_LABELS = {
    "pending": "排队",
    "processing": "处理中",
    "finalizing": "后处理中",
    "completed": "完成",
    "failed": "失败",
    "deleting": "删除中",
    "cancelled": "已取消",
}
WEKNORA_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})


def weknora_status_label(raw_status: str | None) -> str:
    """返回远端处理状态的展示标签;未知状态原样透出并标注。"""

    status = str(raw_status or "").strip()
    if status in WEKNORA_STATUS_LABELS:
        return WEKNORA_STATUS_LABELS[status]
    return f"未知状态({status or '缺失'})"


def build_weknora_file_ref(file_id: str, filename: str) -> str:
    """构造仅携带本地文件 ID 的登记引用;远端 ID 不进入客户端载荷。"""

    from urllib.parse import quote

    return f"weknora-local://{file_id}/{quote(filename or 'unnamed')}"


def parse_weknora_file_ref(item: str) -> tuple[str, str]:
    """解析登记引用为 (file_id, filename);拒绝客户端提供远端资源 ID。"""

    from urllib.parse import unquote

    value = str(item or "").strip()
    prefix = "weknora-local://"
    if not value.startswith(prefix):
        raise ValueError(f"非 WeKnora 文件引用: {item!r}")
    payload = value[len(prefix) :]
    file_id, _, filename = payload.partition("/")
    if not file_id:
        raise ValueError(f"WeKnora 文件引用缺少本地文件 ID: {item!r}")
    return file_id, unquote(filename or "unnamed")
