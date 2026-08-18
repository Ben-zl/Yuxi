"""Yuxi 模型配置到原版 AgentScope Credential 扩展点的适配。"""

from __future__ import annotations

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
from pydantic import Field


class YuxiOpenAIChatModel(OpenAIChatModel):
    """从 Yuxi Credential 读取客户端 Header 与请求体扩展。"""

    def __init__(self, credential, model: str, parameters=None, **kwargs) -> None:
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
        super().__init__(
            credential=credential,
            model=model,
            parameters=parameters,
            client_kwargs={"default_headers": credential.default_headers}
            if credential.default_headers
            else None,
            **kwargs,
        )


class YuxiGeminiChatModel(GeminiChatModel):
    """从 Yuxi Credential 读取 Gemini HTTP Header。"""

    def __init__(self, credential, model: str, parameters=None, **kwargs) -> None:
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


class YuxiOpenAICredential(OpenAICredential):
    """携带 Yuxi OpenAI-compatible 模型级运行参数的凭据。"""

    type: Literal["yuxi_openai_credential"] = "yuxi_openai_credential"
    default_headers: dict[str, str] = Field(default_factory=dict)
    request_body_overrides: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def get_chat_model_class(cls) -> type[ChatModelBase]:
        """返回消费 Yuxi 扩展字段的 OpenAI Adapter。"""
        return YuxiOpenAIChatModel


class YuxiAnthropicCredential(AnthropicCredential):
    """携带 Yuxi Anthropic 模型级 Header 的凭据。"""

    type: Literal["yuxi_anthropic_credential"] = "yuxi_anthropic_credential"
    default_headers: dict[str, str] = Field(default_factory=dict)

    @classmethod
    def get_chat_model_class(cls) -> type[ChatModelBase]:
        """返回消费 Yuxi 扩展字段的 Anthropic Adapter。"""
        return YuxiAnthropicChatModel


class YuxiGeminiCredential(GeminiCredential):
    """携带 Yuxi Gemini 模型级 Header 的凭据。"""

    type: Literal["yuxi_gemini_credential"] = "yuxi_gemini_credential"
    default_headers: dict[str, str] = Field(default_factory=dict)

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
