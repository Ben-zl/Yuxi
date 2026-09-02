"""Yuxi 模型配置到原版 AgentScope Credential 扩展点的适配。"""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any, Literal

from agentscope.credential import (
    AnthropicCredential,
    CredentialFactory,
    GeminiCredential,
    OpenAICredential,
)
from agentscope.model import (
    AnthropicChatModel,
    ChatModelBase,
    GeminiChatModel,
    OpenAIChatModel,
)
from agentscope.embedding import EmbeddingResponse, EmbeddingUsage, OpenAIEmbeddingModel
from pydantic import Field


class YuxiOpenAIChatModel(OpenAIChatModel):
    """从 Yuxi Credential 读取客户端 Header 与请求体扩展。"""

    def __init__(self, credential, model: str, parameters=None, **kwargs) -> None:
        if credential.context_size:
            kwargs["context_size"] = credential.context_size
        super().__init__(
            credential=credential,
            model=model,
            parameters=parameters,
            client_kwargs={"default_headers": credential.default_headers}
            if credential.default_headers
            else None,
            extra_body=credential.request_body_overrides or None,
            **kwargs,
        )


class YuxiAnthropicChatModel(AnthropicChatModel):
    """从 Yuxi Credential 读取 Anthropic 客户端 Header。"""

    def __init__(self, credential, model: str, parameters=None, **kwargs) -> None:
        if credential.context_size:
            kwargs["context_size"] = credential.context_size
        super().__init__(
            credential=credential,
            model=model,
            parameters=parameters,
            client_kwargs={"default_headers": credential.default_headers}
            if credential.default_headers
            else None,
            **kwargs,
        )

    async def _parse_anthropic_stream_completion_response(
        self,
        start_datetime: Any,
        response: Any,
    ) -> AsyncGenerator[Any, None]:
        """合并兼容端点在 message_delta 才返回的完整用量。"""
        final_usage = None

        async def observe_events(stream: AsyncIterator[Any]):
            nonlocal final_usage
            async for event in stream:
                if event.type == "message_delta" and getattr(event, "usage", None) is not None:
                    final_usage = event.usage
                yield event

        class ObservedResponse:
            """保留 SDK 响应生命周期，同时观察进入后的事件流。"""

            async def __aenter__(self):
                stream = await response.__aenter__()
                return observe_events(stream)

            async def __aexit__(self, exc_type, exc_value, traceback):
                return await response.__aexit__(exc_type, exc_value, traceback)

        latest_usage = None
        async for item in super()._parse_anthropic_stream_completion_response(
            start_datetime,
            ObservedResponse(),
        ):
            if item.usage is not None:
                latest_usage = item.usage
            yield item

        if final_usage is None or latest_usage is None:
            return
        for target, source in (
            ("input_tokens", "input_tokens"),
            ("output_tokens", "output_tokens"),
            ("cache_creation_input_tokens", "cache_creation_input_tokens"),
            ("cache_input_tokens", "cache_read_input_tokens"),
        ):
            value = getattr(final_usage, source, None)
            if value is not None:
                setattr(latest_usage, target, max(int(value), 0))


class YuxiGeminiChatModel(GeminiChatModel):
    """从 Yuxi Credential 读取 Gemini HTTP Header。"""

    def __init__(self, credential, model: str, parameters=None, **kwargs) -> None:
        if credential.context_size:
            kwargs["context_size"] = credential.context_size
        client_kwargs = None
        if credential.default_headers:
            client_kwargs = {"http_options": {"headers": credential.default_headers}}
        super().__init__(
            credential=credential,
            model=model,
            parameters=parameters,
            client_kwargs=client_kwargs,
            **kwargs,
        )


class YuxiOpenAIEmbeddingModel(OpenAIEmbeddingModel):
    """适配 Yuxi 的 OpenAI-compatible Embedding 端点和自定义 Header。"""

    def __init__(
        self,
        credential,
        model: str,
        dimensions: int,
        *,
        embedding_base_url: str | None = None,
        parameters=None,
        **kwargs,
    ) -> None:
        import openai

        endpoint = str(embedding_base_url or credential.base_url or "").rstrip("/")
        client_base_url = endpoint[: -len("/embeddings")] if endpoint.endswith("/embeddings") else endpoint
        embedding_credential = credential.model_copy(
            update={"base_url": client_base_url or None},
        )
        super().__init__(
            credential=embedding_credential,
            model=model,
            dimensions=dimensions,
            parameters=parameters,
            pass_dimensions=False,
            **kwargs,
        )
        client_kwargs: dict[str, Any] = {}
        if client_base_url:
            client_kwargs["base_url"] = client_base_url
        if credential.default_headers:
            client_kwargs["default_headers"] = credential.default_headers
        self.client = openai.AsyncClient(
            api_key=credential.api_key.get_secret_value(),
            **client_kwargs,
        )

    def _is_xingliu_qwen3(self) -> bool:
        """判断是否需要规避星流 Embedding 的数组输入兼容问题。"""
        return self.model == "qwen3-embedding-8b" and "kspmas.ksyun.com" in str(
            getattr(self.credential, "base_url", ""),
        )

    async def _call_api(self, inputs: list[str], **kwargs: Any) -> EmbeddingResponse:
        """为星流 qwen3 逐条发送字符串输入，其余模型沿用 AgentScope 实现。"""
        if not self._is_xingliu_qwen3() or len(inputs) <= 1:
            if self._is_xingliu_qwen3() and inputs:
                # 星流稳定支持字符串，不支持单元素数组。
                response = await self.client.embeddings.create(
                    input=inputs[0],
                    model=self.model,
                    encoding_format="float",
                    **kwargs,
                )
                item = response.data[0]
                embedding = item.embedding or getattr(item, "dense_embedding", None)
                return EmbeddingResponse(
                    embeddings=[embedding],
                    usage=EmbeddingUsage(
                        tokens=getattr(response.usage, "total_tokens", None),
                        time=0,
                    ),
                )
            return await super()._call_api(inputs, **kwargs)

        results = [await self._call_api([item], **kwargs) for item in inputs]
        return self._merge_responses(results)


class YuxiOpenAICredential(OpenAICredential):
    """携带 Yuxi OpenAI-compatible 模型级运行参数的凭据。"""

    type: Literal["yuxi_openai_credential"] = "yuxi_openai_credential"
    default_headers: dict[str, str] = Field(default_factory=dict)
    request_body_overrides: dict[str, Any] = Field(default_factory=dict)
    context_size: int | None = Field(default=None, gt=0)

    @classmethod
    def get_chat_model_class(cls) -> type[ChatModelBase]:
        """返回消费 Yuxi 扩展字段的 OpenAI Adapter。"""
        return YuxiOpenAIChatModel

    @classmethod
    def get_embedding_model_class(cls):
        """返回消费 Yuxi Header 与专用端点的 Embedding Adapter。"""
        return YuxiOpenAIEmbeddingModel


class YuxiAnthropicCredential(AnthropicCredential):
    """携带 Yuxi Anthropic 模型级 Header 的凭据。"""

    type: Literal["yuxi_anthropic_credential"] = "yuxi_anthropic_credential"
    default_headers: dict[str, str] = Field(default_factory=dict)
    context_size: int | None = Field(default=None, gt=0)

    @classmethod
    def get_chat_model_class(cls) -> type[ChatModelBase]:
        """返回消费 Yuxi 扩展字段的 Anthropic Adapter。"""
        return YuxiAnthropicChatModel


class YuxiGeminiCredential(GeminiCredential):
    """携带 Yuxi Gemini 模型级 Header 的凭据。"""

    type: Literal["yuxi_gemini_credential"] = "yuxi_gemini_credential"
    default_headers: dict[str, str] = Field(default_factory=dict)
    context_size: int | None = Field(default=None, gt=0)

    @classmethod
    def get_chat_model_class(cls) -> type[ChatModelBase]:
        """返回消费 Yuxi 扩展字段的 Gemini Adapter。"""
        return YuxiGeminiChatModel


def register_yuxi_credentials() -> None:
    """在 AgentScope app 创建前注册 Yuxi Credential Adapter。"""
    for credential in (
        YuxiOpenAICredential,
        YuxiAnthropicCredential,
        YuxiGeminiCredential,
    ):
        CredentialFactory.register_credential(credential)
