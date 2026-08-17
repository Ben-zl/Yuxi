"""跨入口规范化并持久化用户输入消息。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from typing import Any


@dataclass(frozen=True)
class AgentRunInputMessage:
    content: str
    message_type: str
    image_content: str | None
    content_parts: str | list[dict[str, Any]] | None = None
    extra_metadata: dict[str, Any] = field(default_factory=dict)

    def raw_message(self) -> dict[str, Any] | None:
        """返回可写入 Message.extra_metadata 的稳定用户消息 DTO。"""
        if self.content_parts is None:
            return None
        return {"type": "human", "role": "user", "content": self.content_parts}

    def with_metadata(self, metadata: dict[str, Any]) -> AgentRunInputMessage:
        return replace(self, extra_metadata=dict(metadata))


def build_chat_input_message(query: str, image_content: str | None = None) -> AgentRunInputMessage:
    if image_content:
        content_parts: str | list[dict[str, Any]] = [
            {"type": "text", "text": query},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_content}"}},
        ]
        message_type = "multimodal_image"
    else:
        content_parts = query
        message_type = "text"

    return AgentRunInputMessage(
        content=query,
        message_type=message_type,
        image_content=image_content,
        content_parts=content_parts,
    )


def build_chat_input_message_from_openai_content(content: str | list[dict[str, Any]]) -> AgentRunInputMessage:
    if isinstance(content, str):
        if not content:
            raise ValueError("user message content 必须是非空字符串或多模态数组")
        return build_chat_input_message(content)

    if not isinstance(content, list) or not content:
        raise ValueError("user message content 必须是非空字符串或多模态数组")

    parts: list[dict[str, Any]] = []
    text_segments: list[str] = []
    first_image_content: str | None = None
    has_image = False

    for part in content:
        if not isinstance(part, dict):
            raise ValueError("user message content 多模态数组元素必须是对象")

        part_type = part.get("type")
        if part_type == "text":
            text = part.get("text")
            if not isinstance(text, str):
                raise ValueError("text content part 必须包含字符串 text")
            if text:
                text_segments.append(text)
                parts.append({"type": "text", "text": text})
            continue

        if part_type == "image_url":
            image_url = _normalize_openai_image_url_part(part.get("image_url"))
            has_image = True
            if first_image_content is None:
                first_image_content = _extract_data_url_base64(image_url["url"])
            parts.append({"type": "image_url", "image_url": image_url})
            continue

        raise ValueError(f"不支持的多模态 content part 类型: {part_type}")

    if not text_segments and not has_image:
        raise ValueError("user message content 必须包含非空文本或图片")

    query = "\n".join(text_segments)
    if not has_image:
        return build_chat_input_message(query)

    return AgentRunInputMessage(
        content=query,
        message_type="multimodal_image",
        image_content=first_image_content,
        content_parts=parts,
    )


def _normalize_openai_image_url_part(image_url: object) -> dict[str, Any]:
    if isinstance(image_url, str):
        url = image_url
        normalized: dict[str, Any] = {"url": url}
    elif isinstance(image_url, dict):
        url = image_url.get("url")
        normalized = dict(image_url)
    else:
        raise ValueError("image_url content part 必须包含 image_url.url")

    if not isinstance(url, str) or not url:
        raise ValueError("image_url content part 必须包含 image_url.url")
    normalized["url"] = url
    return normalized


def _extract_data_url_base64(url: str) -> str | None:
    marker = ";base64,"
    if not url.startswith("data:image/") or marker not in url:
        return None
    return url.split(marker, 1)[1]


def build_resume_input_message(resume: object) -> AgentRunInputMessage:
    return AgentRunInputMessage(
        content=json.dumps(resume, ensure_ascii=False),
        message_type="resume",
        image_content=None,
    )


def restore_chat_input_message(*, content: str, image_content: str | None, metadata: dict) -> AgentRunInputMessage:
    raw_message = metadata.get("raw_message")
    if isinstance(raw_message, dict):
        raw_content = raw_message.get("content")
        if not isinstance(raw_content, str | list):
            raise ValueError("invalid raw_message for chat input message")
        message_type = "multimodal_image" if image_content or _has_image_url_content_part(raw_content) else "text"
        return AgentRunInputMessage(
            content=content,
            message_type=message_type,
            image_content=image_content,
            content_parts=raw_content,
            extra_metadata=dict(metadata),
        )

    return build_chat_input_message(content, image_content)


def _has_image_url_content_part(content: object) -> bool:
    return isinstance(content, list) and any(
        isinstance(part, dict) and part.get("type") == "image_url" for part in content
    )
