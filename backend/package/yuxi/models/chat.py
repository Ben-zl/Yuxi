"""通用聊天模型选择与 AgentScope 调用适配。"""

from __future__ import annotations

import base64
from collections.abc import AsyncIterator
from typing import Any

from agentscope.credential import AnthropicCredential, GeminiCredential, OpenAICredential
from agentscope.message import Base64Source, DataBlock, Msg, TextBlock, URLSource
from agentscope.model import AnthropicChatModel, GeminiChatModel, OpenAIChatModel
from pydantic import SecretStr

from yuxi import config as sys_config
from yuxi.models.providers.cache import ModelInfo, model_cache
from yuxi.utils import get_docker_safe_url, logger


def resolve_chat_model_spec(model_spec: str | None, *, fallback: str | None = None) -> str:
    """按请求、fallback、系统默认值的顺序解析聊天模型。"""
    for candidate in (model_spec, fallback, sys_config.default_model):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    raise ValueError("model spec 不能为空")


class GeneralResponse:
    """保持知识库等既有调用方依赖的最小响应契约。"""

    def __init__(self, content: str):
        self.content = content
        self.is_full = False


class AgentScopeChatAdapter:
    """将 Yuxi 模型配置适配为 AgentScope ChatModel。"""

    def __init__(self, info: ModelInfo, *, model_params: dict | None = None, **kwargs: Any):
        self.info = {
            "provider_type": info.provider_type,
            "provider_id": info.provider_id,
        }
        self.model_name = info.model_id
        self.base_url = get_docker_safe_url(info.base_url)
        self._model_info = info

        options = dict(model_params or {})
        options.update(kwargs)
        if "max_completion_tokens" in options:
            options.setdefault("max_tokens", options.pop("max_completion_tokens"))

        self._constructor_kwargs = {
            key: options.pop(key) for key in ("max_retries", "retry_delay", "context_size") if key in options
        }
        self._client_kwargs = dict(options.pop("client_kwargs", {}) or {})
        for key in ("timeout", "http_client"):
            if key in options:
                self._client_kwargs[key] = options.pop(key)

        headers = dict(self._client_kwargs.pop("default_headers", {}) or {})
        headers.update(info.headers)
        if headers:
            self._client_kwargs["default_headers"] = headers

        caller_extra_body = dict(options.pop("extra_body", {}) or {})
        caller_extra_body.update(info.request_body_overrides)
        self._extra_body = caller_extra_body or None
        self._options = options

    @staticmethod
    def _normalize_messages(message: str | dict | list) -> list[Msg]:
        """把字符串、OpenAI 消息字典和 AgentScope Msg 统一为 Msg 列表。"""
        if isinstance(message, str):
            return [Msg(name="user", role="user", content=[TextBlock(text=message)])]

        raw_messages = [message] if isinstance(message, dict) else message
        if not isinstance(raw_messages, list):
            raise TypeError("message 必须是字符串、消息字典或消息列表")

        normalized = []
        for item in raw_messages:
            if isinstance(item, Msg):
                normalized.append(item)
                continue
            if not isinstance(item, dict):
                raise TypeError("消息列表元素必须是 AgentScope Msg 或字典")

            role = item.get("role") or ("user" if item.get("type") == "human" else None)
            if role not in {"user", "assistant", "system"}:
                raise ValueError(f"不支持的消息角色: {role}")
            normalized.append(
                Msg(
                    name=str(item.get("name") or role),
                    role=role,
                    content=_normalize_content(item.get("content")),
                )
            )
        return normalized

    def _build_model(self, stream: bool):
        """按供应商创建一次调用所需的 AgentScope 模型。"""
        model_class = self._model_class()
        credential: OpenAICredential | AnthropicCredential | GeminiCredential

        if model_class is AnthropicChatModel:
            credential = AnthropicCredential(
                api_key=SecretStr(self._model_info.api_key),
                base_url=self.base_url or None,
            )
        elif model_class is GeminiChatModel:
            credential = GeminiCredential(api_key=SecretStr(self._model_info.api_key))
        else:
            credential = OpenAICredential(
                api_key=SecretStr(self._model_info.api_key),
                base_url=self.base_url or None,
            )

        parameter_fields = model_class.Parameters.model_fields
        parameters = {key: value for key, value in self._options.items() if key in parameter_fields}
        model_kwargs = {
            "credential": credential,
            "model": self.model_name,
            "parameters": model_class.Parameters(**parameters),
            "stream": stream,
            "client_kwargs": self._client_kwargs,
            **self._constructor_kwargs,
        }
        if model_class is OpenAIChatModel:
            model_kwargs["extra_body"] = self._extra_body
        return model_class(**model_kwargs)

    def _model_class(self):
        """将原生 provider 映射到 AgentScope 模型，其他类型按 OpenAI 兼容处理。"""
        if self._model_info.provider_type == "anthropic":
            return AnthropicChatModel
        if self._model_info.provider_type == "gemini":
            return GeminiChatModel
        return OpenAIChatModel

    def _call_options(self) -> dict[str, Any]:
        parameter_fields = self._model_class().Parameters.model_fields
        return {key: value for key, value in self._options.items() if key not in parameter_fields}

    async def call(self, message, stream: bool = False):
        """调用模型；异常中只记录定位信息，不记录消息或凭据。"""
        messages = self._normalize_messages(message)
        try:
            model = self._build_model(stream)
            if stream:
                return self._stream_response(model, messages)
            response = await model(messages, **self._call_options())
            return GeneralResponse(_response_text(response))
        except Exception as exc:
            err = f"Error calling model: {exc}, URL: {self.base_url}, Model: {self.model_name}"
            logger.error(err)
            raise RuntimeError(err) from exc

    async def _stream_response(self, model, messages: list[Msg]) -> AsyncIterator[GeneralResponse]:
        response = await model(messages, **self._call_options())
        if not hasattr(response, "__aiter__"):
            text = _response_text(response)
            if text:
                yield GeneralResponse(text)
            return

        yielded_partial = False
        async for chunk in response:
            if getattr(chunk, "is_last", False) and yielded_partial:
                continue
            text = _response_text(chunk)
            if text:
                yielded_partial = yielded_partial or not getattr(chunk, "is_last", False)
                yield GeneralResponse(text)


def _normalize_content(content: Any) -> list:
    """把 OpenAI content parts 转为 AgentScope 内容块。"""
    if isinstance(content, str):
        return [TextBlock(text=content)]
    if not isinstance(content, list):
        raise TypeError("消息 content 必须是字符串或内容块列表")

    blocks = []
    for part in content:
        if not isinstance(part, dict):
            raise TypeError("消息内容块必须是字典")
        part_type = part.get("type")
        if part_type in {"text", "input_text"}:
            blocks.append(TextBlock(text=str(part.get("text") or "")))
            continue
        if part_type in {"image_url", "image", "input_image"}:
            blocks.append(_image_block(part))
            continue
        blocks.append(part)
    return blocks


def _image_block(part: dict) -> DataBlock:
    image_value = part.get("image_url") or part.get("image") or part.get("url")
    url = image_value.get("url") if isinstance(image_value, dict) else image_value
    if not isinstance(url, str) or not url:
        raise ValueError("图片内容块缺少 URL")

    if url.startswith("data:"):
        header, separator, data = url.partition(",")
        if not separator or ";base64" not in header:
            raise ValueError("图片 data URL 必须使用 base64 编码")
        media_type = header[5:].split(";", 1)[0] or "application/octet-stream"
        try:
            base64.b64decode(data, validate=True)
        except ValueError as exc:
            raise ValueError("图片 data URL 包含无效 base64") from exc
        source = Base64Source(data=data, media_type=media_type)
    else:
        source = URLSource(url=url, media_type=str(part.get("media_type") or "image/*"))
    return DataBlock(source=source)


def _response_text(response) -> str:
    if hasattr(response, "get_text_content"):
        return response.get_text_content() or ""
    return "".join(
        str(getattr(block, "text", ""))
        for block in getattr(response, "content", [])
        if getattr(block, "type", None) == "text"
    )


def select_model(model_spec: str, **kwargs) -> AgentScopeChatAdapter:
    """从跨进程模型缓存选择一个聊天模型。"""
    if not model_spec:
        raise ValueError("model_spec 不能为空")

    info = model_cache.get_model_info(model_spec)
    if not info:
        available = model_cache.get_all_specs("chat")
        available_ids = [item.spec for item in available[:10]]
        raise ValueError(f"未找到模型: '{model_spec}'。可用聊天模型 ({len(available)}): {available_ids}")
    if info.model_type != "chat":
        raise ValueError(f"Model {model_spec} is not a chat model (type={info.model_type})")

    logger.info(f"Selecting model: {model_spec} (provider_type={info.provider_type})")
    return AgentScopeChatAdapter(info, **kwargs)


async def test_chat_model_status_by_spec(spec: str) -> dict:
    try:
        logger.debug(f"Testing model status by spec: {spec}")
        model = select_model(model_spec=spec)
        response = await model.call([{"role": "user", "content": "Say 1"}], stream=False)
        if response and response.content:
            return {"spec": spec, "status": "available", "message": "连接正常"}
        return {"spec": spec, "status": "unavailable", "message": "响应无效"}
    except Exception as exc:
        logger.error(f"测试模型状态失败 {spec}: {exc}")
        return {"spec": spec, "status": "error", "message": str(exc)}
