"""WPS 协作开放平台 v7 传输适配器。"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import mimetypes
import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from email.utils import format_datetime
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from urllib.parse import quote, urlparse

import httpx
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.fernet import Fernet, InvalidToken
from pydantic import BaseModel, Field

from yuxi.utils.logging_config import logger


@dataclass(frozen=True, slots=True)
class WPSAttachment:
    """WPS 下载并完成大小校验的附件。"""

    name: str
    media_type: str
    content: bytes
    sha256: str


@dataclass(frozen=True, slots=True)
class WPSChannelEvent:
    """交给 Yuxi Run ingress 的平台消息信封。"""

    channel_id: str
    channel_user_id: str
    channel_message_id: str
    chat_id: str
    text: str
    attachments: tuple[WPSAttachment, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    received_at: str = ""


@dataclass(slots=True)
class WPSChannelStatus:
    """单个 WPS 长连接的非持久运行状态。"""

    state: str = "stopped"
    last_error: str = ""


_WS_ENDPOINT = "wss://openapi.wps.cn/v7/event/ws"
_API_BASE_URL = "https://openapi.wps.cn"
_MAX_REPLY_LENGTH = 8000
_MAX_CONNECT_BACKOFF = 60.0
_RETRY_DELAYS = (0.5, 1.5)
_DEFAULT_MAX_MEDIA_BYTES = 5 * 1024 * 1024


class GroupReplyPolicy(StrEnum):
    """How group messages are admitted."""

    MENTION_ONLY = "mention_only"
    ALL = "all"


class WPSXiezuoTransport:
    """连接 WPS 协作，并把消息交给 Yuxi ingress。"""

    channel_type = "wps_xiezuo"
    display_name = "WPS 协作"
    description = "WPS 协作开放平台 v7 长连接机器人。"
    platform_bot_id_field = "app_id"

    class Credentials(BaseModel):
        """WPS application credentials."""

        app_id: str = Field(title="App ID")
        app_secret: str = Field(
            title="Encrypted App Secret",
            description="Fernet ciphertext produced by the control plane.",
            json_schema_extra={"format": "password"},
        )

    class Config(BaseModel):
        """WPS-specific inbound behavior."""

        allow_from: list[str] = Field(
            default_factory=list,
            title="Allowed sender IDs",
        )
        group_reply_policy: GroupReplyPolicy = Field(
            default=GroupReplyPolicy.MENTION_ONLY,
            title="Group reply policy",
        )
        show_tool_process: bool = False
        show_thinking: bool = False
        max_media_bytes: int = Field(
            default=_DEFAULT_MAX_MEDIA_BYTES,
            ge=1,
            le=_DEFAULT_MAX_MEDIA_BYTES,
            title="Maximum attachment size",
        )
        ws_endpoint: str = Field(default=_WS_ENDPOINT, exclude=True)
        api_base_url: str = Field(default=_API_BASE_URL, exclude=True)

    def __init__(
        self,
        channel_id: str,
        credentials: WPSXiezuoTransport.Credentials,
        config: WPSXiezuoTransport.Config,
    ) -> None:
        """Bind validated credentials and platform options."""
        self._channel_id = channel_id
        self._app_id = credentials.app_id
        key = os.getenv("AGENTSCOPE_CHANNEL_CREDENTIAL_KEY", "")
        if not key:
            raise ValueError(
                "AGENTSCOPE_CHANNEL_CREDENTIAL_KEY is required for WPS.",
            )
        try:
            self._app_secret = (
                Fernet(key.encode())
                .decrypt(
                    credentials.app_secret.encode(),
                )
                .decode()
            )
        except (InvalidToken, ValueError) as exc:
            raise ValueError("WPS app_secret ciphertext is invalid.") from exc
        self._config = config
        self.status = WPSChannelStatus()
        self._http: httpx.AsyncClient | None = None
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._token_lock = asyncio.Lock()
        self._seen_message_ids: set[str] = set()
        self._seen_order: list[str] = []
        self._stop_reconnect = False

    @property
    def channel_id(self) -> str:
        """Return the stored channel identifier."""
        return self._channel_id

    async def aclose(self) -> None:
        """Close the lazily-created REST client."""
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    async def start_listening(
        self,
        emit: Callable[[WPSChannelEvent], Awaitable[None]],
    ) -> None:
        """Maintain the WPS WebSocket and emit validated message events."""
        import websockets

        self._emit = emit
        backoff = 1.0
        try:
            while True:
                self.status.state = "connecting"
                try:
                    headers = self._websocket_headers()
                    async with websockets.connect(
                        self._config.ws_endpoint,
                        additional_headers=headers,
                        ping_interval=30,
                        ping_timeout=90,
                        close_timeout=5,
                        max_size=8 * 1024 * 1024,
                    ) as socket:
                        self.status.state = "connected"
                        self.status.last_error = ""
                        backoff = 1.0
                        async for raw in socket:
                            ack = await self._handle_raw_message(raw)
                            if ack is not None:
                                await socket.send(json.dumps(ack))
                            if self._stop_reconnect:
                                return
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - reconnect boundary
                    self.status.state = "retrying"
                    self.status.last_error = self._safe_error(exc)
                    logger.warning(
                        "WPS channel '%s' disconnected; retrying in %.1fs: %s",
                        self._channel_id,
                        backoff,
                        self.status.last_error,
                    )
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, _MAX_CONNECT_BACKOFF)
        finally:
            self.status.state = "stopped"
            await self.aclose()

    def _websocket_headers(self) -> dict[str, str]:
        """Build KSO-1 WebSocket authentication headers."""
        parsed = urlparse(self._config.ws_endpoint)
        uri = parsed.path or "/"
        if parsed.query:
            uri = f"{uri}?{parsed.query}"
        date = format_datetime(datetime.now(UTC), usegmt=True)
        payload = f"KSO-1GET{uri}{date}"
        signature = hmac.new(
            self._app_secret.encode(),
            payload.encode(),
            hashlib.sha256,
        ).hexdigest()
        return {
            "X-Kso-Date": date,
            "X-Kso-Authorization": f"KSO-1 {self._app_id}:{signature}",
            "X-Ack-Mode": "required",
        }

    async def _handle_raw_message(self, raw: str | bytes) -> dict | None:
        """Validate, decode and enqueue one WPS frame before ACK."""
        try:
            frame = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            logger.warning("WPS channel '%s' received invalid JSON", self._channel_id)
            return None

        frame_type = frame.get("type")
        if frame_type == "goaway":
            if frame.get("reason") == "connection_replaced":
                self._stop_reconnect = True
                return {"type": "ack", "code": 200}
            raise ConnectionError("WPS server requested reconnect")
        if frame_type:
            return None
        nonce = str(frame.get("nonce") or "")
        try:
            if not self._verify_event_signature(frame):
                raise ValueError("invalid event signature")
            plaintext = self._decrypt_event_data(
                nonce,
                str(frame.get("encrypted_data") or ""),
            )
            if frame.get("topic") == "kso.app_chat.message" and frame.get("operation") == "create":
                await self._handle_message_payload(json.loads(plaintext))
            return {"type": "ack", "nonce": nonce, "code": 200}
        except Exception as exc:  # noqa: BLE001 - protocol failure ACK
            logger.warning(
                "WPS channel '%s' rejected event %s: %s",
                self._channel_id,
                nonce,
                self._safe_error(exc),
            )
            return {
                "type": "ack",
                "nonce": nonce,
                "code": 500,
                "msg": self._safe_error(exc)[:256],
            }

    def _verify_event_signature(self, frame: dict[str, Any]) -> bool:
        """Verify the HMAC-SHA256 URL-safe event signature."""
        content = ":".join(
            [
                self._app_id,
                str(frame.get("topic") or ""),
                str(frame.get("nonce") or ""),
                str(frame.get("time") or 0),
                str(frame.get("encrypted_data") or ""),
            ],
        )
        expected = (
            base64.urlsafe_b64encode(
                hmac.new(
                    self._app_secret.encode(),
                    content.encode(),
                    hashlib.sha256,
                ).digest(),
            )
            .decode()
            .rstrip("=")
        )
        return hmac.compare_digest(str(frame.get("signature") or ""), expected)

    def _decrypt_event_data(self, nonce: str, encrypted_data: str) -> bytes:
        """Decrypt WPS AES-256-CBC event data and validate PKCS7 padding."""
        key = hashlib.md5(self._app_secret.encode()).hexdigest().encode()  # noqa: S324 - WPS protocol
        iv = nonce.encode()[:16].ljust(16, b"\0")
        ciphertext = base64.b64decode(encrypted_data, validate=True)
        if not ciphertext or len(ciphertext) % 16:
            raise ValueError("ciphertext length is invalid")
        decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
        padded = decryptor.update(ciphertext) + decryptor.finalize()
        padding = padded[-1]
        if padding < 1 or padding > 16 or padded[-padding:] != bytes([padding]) * padding:
            raise ValueError("PKCS7 padding is invalid")
        return padded[:-padding]

    async def _handle_message_payload(self, payload: dict[str, Any]) -> None:
        """Filter, download and emit one decrypted WPS message."""
        message = payload.get("message") or {}
        sender = payload.get("sender") or {}
        chat = payload.get("chat") or {}
        message_id = str(message.get("id") or "")
        sender_id = str(sender.get("id") or "")
        chat_id = str(chat.get("id") or "")
        chat_type = str(chat.get("type") or "")
        if not message_id or not sender_id or not chat_id:
            return
        message_type = str(message.get("type") or "").lower()
        if message_type not in {"text", "image", "file"}:
            return
        if sender_id == self._app_id or sender.get("type") in {"app", "bot"}:
            return
        if self._config.allow_from and sender_id not in self._config.allow_from:
            return
        if self._is_duplicate(message_id):
            return

        content = message.get("content") or {}
        is_direct = chat_type in {"p2p", "single", "direct"}
        mentions = message.get("mentions") or payload.get("mentions") or []
        if (
            not is_direct
            and self._config.group_reply_policy == GroupReplyPolicy.MENTION_ONLY
            and not self._mentions_app(mentions)
        ):
            return

        text = ""
        attachments: list[WPSAttachment] = []
        if message_type == "text":
            text_value = content.get("text") or {}
            text = (
                str(text_value.get("content") or "").strip()
                if isinstance(text_value, dict)
                else str(text_value).strip()
            )
            if not text:
                return

        else:
            media = content.get(message_type) or {}
            if not isinstance(media, dict):
                return
            local = media.get("local") or {}
            if not isinstance(local, dict):
                local = {}
            storage_key = str(
                media.get("storage_key") or media.get("file_id") or local.get("storage_key") or "",
            )
            if not storage_key:
                return
            file_name = self._safe_file_name(
                str(media.get("name") or local.get("name") or message_type),
            )
            declared_size = media.get("size", local.get("size"))
            attachment = await self._download_attachment(
                chat_id=chat_id,
                message_id=message_id,
                storage_key=storage_key,
                file_name=file_name,
                declared_size=declared_size,
                message_type=message_type,
            )
            attachments.append(attachment)
        received_at = datetime.now(UTC).isoformat()
        metadata = {
            "source": "channel",
            "channel_id": self._channel_id,
            "channel_type": self.channel_type,
            "channel_message_id": message_id,
            "chat_id": chat_id,
            "chat_type": chat_type,
            "company_id": str(payload.get("company_id") or ""),
            "sender_id": sender_id,
            "channel_user_id": sender_id,
            "received_at": received_at,
        }
        if self._emit is not None:
            await self._emit(
                WPSChannelEvent(
                    channel_id=self._channel_id,
                    channel_user_id=sender_id,
                    channel_message_id=message_id,
                    chat_id=chat_id,
                    text=text,
                    attachments=tuple(attachments),
                    metadata=metadata,
                    received_at=received_at,
                ),
            )
            self._remember_message_id(message_id)

    def _mentions_app(self, mentions: list[Any]) -> bool:
        """Return whether structured mentions address this app or everyone."""
        for mention in mentions:
            if not isinstance(mention, dict):
                continue
            mention_id = str(
                mention.get("id") or mention.get("user_id") or mention.get("app_id") or "",
            )
            mention_type = str(mention.get("type") or "").lower()
            if mention_id in {self._app_id, "-1"} or mention_type in {"all", "everyone"}:
                return True
        return False

    def _is_duplicate(self, message_id: str) -> bool:
        """Return whether a successfully emitted message was seen."""
        return message_id in self._seen_message_ids

    def _remember_message_id(self, message_id: str) -> None:
        """Remember an id only after the gateway accepted the event."""
        self._seen_message_ids.add(message_id)
        self._seen_order.append(message_id)
        if len(self._seen_order) > 4096:
            self._seen_message_ids.discard(self._seen_order.pop(0))

    async def _download_attachment(
        self,
        *,
        chat_id: str,
        message_id: str,
        storage_key: str,
        file_name: str,
        declared_size: Any,
        message_type: str,
    ) -> WPSAttachment:
        """Download and verify one WPS message resource."""
        expected_size: int | None = None
        if declared_size is not None:
            if isinstance(declared_size, bool) or not isinstance(declared_size, int) or declared_size < 0:
                raise ValueError("WPS attachment size is invalid")
            expected_size = declared_size
            if expected_size > self._config.max_media_bytes:
                raise ValueError("WPS attachment exceeds the size limit")

        path = (
            f"/v7/chats/{quote(chat_id, safe='')}/messages/"
            f"{quote(message_id, safe='')}/resources/"
            f"{quote(storage_key, safe='')}/download"
            f"?file_name={quote(file_name, safe='')}"
        )
        response_data = await self._api_json("GET", path)
        download_url = str(response_data.get("url") or "")
        if not download_url:
            raise ValueError("WPS attachment response has no download URL")

        chunks: list[bytes] = []
        transferred = 0
        media_type = ""
        verified_size = expected_size
        async with self._client().stream("GET", download_url) as response:
            response.raise_for_status()
            raw_length = response.headers.get("Content-Length")
            if raw_length:
                try:
                    content_length = int(raw_length)
                except ValueError as exc:
                    raise ValueError("WPS attachment Content-Length is invalid") from exc
                if content_length > self._config.max_media_bytes:
                    raise ValueError("WPS attachment exceeds the size limit")
                verified_size = content_length
            media_type = response.headers.get("Content-Type", "").split(";", 1)[0]
            async for chunk in response.aiter_bytes():
                transferred += len(chunk)
                if transferred > self._config.max_media_bytes:
                    raise ValueError("WPS attachment exceeds the size limit")
                chunks.append(chunk)
        if verified_size is not None and transferred != verified_size:
            raise ValueError("WPS attachment size does not match the download")

        data = b"".join(chunks)
        if not media_type or media_type == "application/octet-stream":
            media_type = mimetypes.guess_type(file_name)[0] or (
                "image/jpeg" if message_type == "image" else "application/octet-stream"
            )
        digest = hashlib.sha256(data).hexdigest()
        return WPSAttachment(
            name=file_name,
            media_type=media_type,
            content=data,
            sha256=digest,
        )

    async def send_markdown(self, chat_id: str, text: str) -> None:
        """把已持久化的 Yuxi Run 文本结果发送到 WPS。"""
        rendered = self._wps_line_breaks(text)
        for chunk in self._split_markdown(rendered):
            await self._send_text(chat_id, chunk)

    def _split_markdown(self, text: str) -> list[str]:
        """Split near paragraph boundaries while preserving fenced blocks."""
        if len(text) <= _MAX_REPLY_LENGTH:
            return [text]
        chunks: list[str] = []
        remaining = text
        while remaining:
            if len(remaining) <= _MAX_REPLY_LENGTH:
                chunks.append(remaining)
                break
            source = remaining
            boundary = remaining.rfind("\n\n", 0, _MAX_REPLY_LENGTH + 1)
            if boundary >= _MAX_REPLY_LENGTH // 2:
                boundary = min(boundary + 2, _MAX_REPLY_LENGTH)
            else:
                boundary = remaining.rfind("\n", 0, _MAX_REPLY_LENGTH + 1)
                if boundary >= _MAX_REPLY_LENGTH // 2:
                    boundary = min(boundary + 1, _MAX_REPLY_LENGTH)
            if boundary < _MAX_REPLY_LENGTH // 2:
                boundary = _MAX_REPLY_LENGTH
            chunk, remaining = source[:boundary], source[boundary:]
            if chunk.count("```") % 2:
                if len(chunk) > _MAX_REPLY_LENGTH - 4:
                    boundary -= 4
                    chunk, remaining = source[:boundary], source[boundary:]
                if chunk.count("```") % 2:
                    chunk += "\n```"
                    remaining = "```\n" + remaining
            chunks.append(chunk)
        return chunks

    @staticmethod
    def _wps_line_breaks(text: str) -> str:
        """Make single newlines render as hard breaks in WPS Markdown."""
        lines = text.split("\n")
        return "\n".join(line if not line or line.endswith("  ") else f"{line}  " for line in lines)

    async def _send_text(self, chat_id: str, content: str) -> None:
        """Send text with bounded retry and one forced token refresh."""
        payload = {
            "type": "text",
            "receiver": {"type": "chat", "receiver_id": chat_id},
            "content": {"text": {"content": content, "type": "markdown"}},
        }
        await self._send_message(payload)

    async def _send_message(self, payload: dict[str, Any]) -> None:
        """Create one WPS message with bounded retry."""
        auth_refreshed = False
        force_refresh = False
        transient_attempt = 0
        for attempt in range(3):
            token = await self._get_token(force=force_refresh)
            force_refresh = False
            body = json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode()
            try:
                response = await self._client().post(
                    "/v7/messages/create",
                    headers=self._signed_headers(
                        "POST",
                        "/v7/messages/create",
                        body,
                        token,
                    ),
                    content=body,
                )
            except httpx.HTTPError as exc:
                if attempt == 2:
                    self.status.last_error = self._safe_error(exc)
                    raise
                await asyncio.sleep(_RETRY_DELAYS[transient_attempt])
                transient_attempt = min(transient_attempt + 1, len(_RETRY_DELAYS) - 1)
                continue
            if response.status_code == 401 and not auth_refreshed and attempt < 2:
                self._token = None
                auth_refreshed = True
                force_refresh = True
                continue
            if response.status_code == 429 or response.status_code >= 500:
                if attempt == 2:
                    response.raise_for_status()
                retry_after = response.headers.get("Retry-After")
                delay = _RETRY_DELAYS[transient_attempt]
                if retry_after:
                    try:
                        delay = min(float(retry_after), 10.0)
                    except ValueError:
                        pass
                transient_attempt = min(transient_attempt + 1, len(_RETRY_DELAYS) - 1)
                await asyncio.sleep(delay)
                continue
            response.raise_for_status()
            self.status.last_error = ""
            return
        raise RuntimeError("WPS message send retry exhausted")

    async def _api_json(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Call a signed WPS resource API with one token refresh."""
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode() if payload is not None else b""
        for force in (False, True):
            token = await self._get_token(force=force)
            response = await self._client().request(
                method,
                path,
                headers=self._signed_headers(method, path, body, token),
                content=body or None,
            )
            if response.status_code == 401 and not force:
                self._token = None
                continue
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError("WPS API response is not an object")
            if result.get("code") not in {None, 0}:
                raise ValueError(f"WPS API rejected request with code {result.get('code')}")
            data = result.get("data", result)
            if not isinstance(data, dict):
                raise ValueError("WPS API data is not an object")
            return data
        raise RuntimeError("WPS API authorization retry exhausted")

    def _signed_headers(
        self,
        method: str,
        path: str,
        body: bytes,
        token: str,
    ) -> dict[str, str]:
        """Build KSO-1 headers for one REST request."""
        parsed = urlparse(path)
        request_uri = parsed.path or "/"
        if parsed.query:
            request_uri = f"{request_uri}?{parsed.query}"
        content_type = "application/json"
        date = format_datetime(datetime.now(UTC), usegmt=True)
        body_hash = hashlib.sha256(body).hexdigest() if body else ""
        signed = f"KSO-1{method.upper()}{request_uri}{content_type}{date}{body_hash}"
        signature = hmac.new(
            self._app_secret.encode(),
            signed.encode(),
            hashlib.sha256,
        ).hexdigest()
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": content_type,
            "X-Kso-Date": date,
            "X-Kso-Authorization": f"KSO-1 {self._app_id}:{signature}",
        }

    @staticmethod
    def _safe_file_name(file_name: str) -> str:
        """Strip path and control characters from a platform filename."""
        value = file_name.replace("\\", "/").split("/")[-1]
        value = "".join(char for char in value if ord(char) >= 32).strip()
        return (value or "file")[:255]

    async def _get_token(self, *, force: bool = False) -> str:
        """Return a cached OAuth token, refreshing shortly before expiry."""
        loop = asyncio.get_running_loop()
        if not force and self._token and loop.time() < self._token_expires_at - 60:
            return self._token
        async with self._token_lock:
            if not force and self._token and loop.time() < self._token_expires_at - 60:
                return self._token
            response = await self._client().post(
                "/oauth2/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._app_id,
                    "client_secret": self._app_secret,
                },
            )
            if response.status_code == 404:
                response = await self._client().post(
                    "/openapi/oauth2/token",
                    data={
                        "grant_type": "client_credentials",
                        "client_id": self._app_id,
                        "client_secret": self._app_secret,
                    },
                )
            response.raise_for_status()
            data = response.json()
            token = str(data.get("access_token") or "")
            if not token:
                raise ValueError("WPS token response has no access_token")
            self._token = token
            self._token_expires_at = loop.time() + max(int(data.get("expires_in") or 7200), 60)
            return token

    def _client(self) -> httpx.AsyncClient:
        """Return the shared REST client."""
        if self._http is None:
            self._http = httpx.AsyncClient(
                base_url=self._config.api_base_url,
                timeout=httpx.Timeout(30),
                follow_redirects=True,
            )
        return self._http

    @staticmethod
    def _safe_error(exc: BaseException) -> str:
        """Return a bounded error string that never includes request bodies."""
        return f"{type(exc).__name__}: {str(exc)[:180]}"
