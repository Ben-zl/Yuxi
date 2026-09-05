"""把中立 Workspace preview 结果装配为 HTTP 响应。"""

from __future__ import annotations

import io
from urllib.parse import quote

from fastapi.responses import StreamingResponse
from yuxi.utils.filepreview import (
    MAX_BINARY_PREVIEW_SIZE_BYTES,
    OfficePreviewConversionError,
    PreviewResult,
    convert_office_to_pdf,
    detect_media_type,
    detect_preview_type,
    is_binary_preview_type,
    is_office_pdf_preview_file,
    preview_too_large,
    render_preview,
)
from yuxi.workspace.preview import preview_workspace_file


def render_preview_too_large_payload() -> dict:
    """返回统一的超限预览响应。"""
    return preview_too_large().payload()


def render_preview_payload(path: str, raw_content: bytes) -> dict:
    """把通用预览结果转换为历史 HTTP payload。"""
    result = render_preview(path, raw_content)
    return result.payload()


async def render_file_preview(
    path: str,
    raw_content: bytes,
    *,
    office_cache_key: str,
) -> dict | StreamingResponse:
    """把持久文件预览结果转换为 Workspace/Viewer HTTP 响应。"""
    result = await preview_workspace_file(path, raw_content, office_cache_key=office_cache_key)
    return _preview_response(result)


def _preview_response(result: PreviewResult) -> dict | StreamingResponse:
    if not isinstance(result.content, bytes):
        return result.payload()
    filename = result.filename or "preview"
    return StreamingResponse(
        io.BytesIO(result.content),
        media_type=result.media_type or "application/octet-stream",
        headers={
            "Content-Disposition": f"inline; filename*=UTF-8''{quote(filename)}",
            "X-Yuxi-Preview-Type": result.preview_type,
            "X-Yuxi-Preview-Filename": quote(filename),
        },
    )
