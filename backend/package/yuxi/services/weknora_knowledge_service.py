"""WeKnora 托管文档的上传、状态同步与重新解析用例。

远端持有内容与处理状态;本地仅保存目录位置与 remote_knowledge_id 绑定。
状态同步失败时保留本地旧值,不把远端故障伪装成文档终态。
"""

from __future__ import annotations

from typing import Any

from yuxi.knowledge.base import KBOperationError
from yuxi.knowledge.weknora import WeKnoraClient, WeKnoraClientError, build_weknora_file_ref, load_weknora_settings
from yuxi.services.weknora_kb_service import _extract_data, load_confirmed_binding
from yuxi.utils import logger

# 状态同步挂在列表读路径上,用短超时避免远端故障阻塞列表请求
STATUS_SYNC_TIMEOUT_SECONDS = 5.0


def _client_for_kb(*, timeout: float = 30.0) -> WeKnoraClient:
    settings = load_weknora_settings()
    if not settings.ready:
        raise KBOperationError("WeKnora 部署配置不完整,无法执行文档用例")
    return WeKnoraClient(settings, timeout=timeout)


async def upload_weknora_file(
    kb_id: str,
    *,
    filename: str,
    content: bytes,
    content_type: str | None = None,
    client: WeKnoraClient | None = None,
) -> dict:
    """把文件直接上传到远端托管库,返回 weknora:// 引用与远端初始状态。

    上传为非幂等写:超时/传输失败直接报错,不自动重试;远端可能已创建资源,
    由调用方提示待核对。远端 409 表示内容重复,作为查重结果透出。
    """

    _, binding, settings = await load_confirmed_binding(kb_id, action="上传文档")
    remote_client = client or WeKnoraClient(settings)
    files = {"file": (filename, content, content_type or "application/octet-stream")}
    try:
        response = await remote_client.request(
            "POST",
            f"knowledge-bases/{binding['remote_kb_id']}/knowledge/file",
            files=files,
            data={"fileName": filename},
        )
    except WeKnoraClientError as error:
        if error.status_code == 409:
            raise KBOperationError("远端已存在相同内容文件") from error
        raise KBOperationError(f"WeKnora 上传失败: {error}") from error

    data = _extract_data(response)
    remote_knowledge_id = str(data.get("id") or data.get("knowledge_id") or "")
    if not remote_knowledge_id:
        raise KBOperationError("WeKnora 上传响应缺少文档 ID,结果不确定,请核对远端后处理")
    return {
        "remote_knowledge_id": remote_knowledge_id,
        "ref": build_weknora_file_ref(remote_knowledge_id, filename),
        "remote_status": str(data.get("parse_status") or "pending"),
        "filename": filename,
        "size": len(content),
    }


async def sync_weknora_file_statuses(
    kb_id: str, metas: list[dict], *, client: WeKnoraClient | None = None
) -> list[dict]:
    """按远端最新 parse_status 刷新文件元数据;终态不再重复拉取。

    同步失败保留本地旧值并标注远端不可达,不把故障映射为完成。
    """

    from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository
    from yuxi.knowledge.weknora import WEKNORA_TERMINAL_STATUSES

    pending_ids = {
        str(meta.get("remote_knowledge_id")): meta
        for meta in metas
        if meta.get("remote_knowledge_id")
        and not meta.get("is_folder")
        and str(meta.get("status")) not in WEKNORA_TERMINAL_STATUSES
    }
    if not pending_ids:
        return metas

    remote_client = client or _client_for_kb(timeout=STATUS_SYNC_TIMEOUT_SECONDS)
    try:
        response = await remote_client.request(
            "GET",
            "knowledge/batch",
            params=[("ids", remote_id) for remote_id in pending_ids],
        )
        items = response.json().get("data") or []
    except (WeKnoraClientError, ValueError, AttributeError) as error:
        logger.warning(f"WeKnora 状态同步失败,保留本地状态: kb_id={kb_id}: {error}")
        return metas

    remote_status_by_id = {str(item.get("id")): item for item in items if isinstance(item, dict) and item.get("id")}
    repo = KnowledgeFileRepository()
    for remote_id, meta in pending_ids.items():
        remote_item = remote_status_by_id.get(remote_id)
        if remote_item is None:
            continue
        remote_status = str(remote_item.get("parse_status") or "").strip()
        if not remote_status or remote_status == meta.get("status"):
            continue
        # error_message 恒置,避免 failed→completed 后本地残留旧错误文本
        updates: dict[str, Any] = {
            "status": remote_status,
            "error_message": str(remote_item.get("error_message") or "") or None,
        }
        await repo.update_fields(file_id=str(meta["file_id"]), data=updates, kb_id=kb_id)
        meta["status"] = remote_status
    return metas


async def reparse_weknora_file(kb_id: str, file_id: str, *, client: WeKnoraClient | None = None) -> dict:
    """重新解析并重建索引:旧分块与向量由远端重建。"""

    from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository

    record = await KnowledgeFileRepository().get_by_file_id(file_id)
    if record is None or record.kb_id != kb_id:
        raise KBOperationError(f"文档 {file_id} 不属于知识库 {kb_id}")
    remote_knowledge_id = getattr(record, "remote_knowledge_id", None)
    if not remote_knowledge_id:
        raise KBOperationError(f"文档 {file_id} 缺少远端绑定")

    _, binding, settings = await load_confirmed_binding(kb_id, action="重新解析")
    remote_client = client or WeKnoraClient(settings)
    try:
        response = await remote_client.request("POST", f"knowledge/{remote_knowledge_id}/reparse")
    except WeKnoraClientError as error:
        raise KBOperationError(f"WeKnora 重新解析失败: {error}") from error

    remote_status = str(_extract_data(response).get("parse_status") or "pending")
    await KnowledgeFileRepository().update_fields(
        file_id=file_id, data={"status": remote_status, "error_message": None}, kb_id=kb_id
    )
    return {"file_id": file_id, "status": remote_status}


async def delete_weknora_file(
    kb_id: str,
    file_id: str,
    *,
    record=None,
    client: WeKnoraClient | None = None,
) -> dict:
    """删除托管文档:远端确认删除(404 视为已删);失败保留本地行并显式报错。"""

    from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository
    from yuxi.services.weknora_kb_service import load_confirmed_binding

    repo = KnowledgeFileRepository()
    if record is None:
        record = await repo.get_by_file_id(file_id)
    if record is None or record.kb_id != kb_id:
        raise KBOperationError(f"文档 {file_id} 不属于知识库 {kb_id}")
    remote_knowledge_id = getattr(record, "remote_knowledge_id", None)
    if not remote_knowledge_id:
        raise KBOperationError(f"文档 {file_id} 缺少远端绑定")

    _, binding, settings = await load_confirmed_binding(kb_id, action="删除文档")
    remote_client = client or WeKnoraClient(settings)
    try:
        await remote_client.request("DELETE", f"knowledge/{remote_knowledge_id}")
    except WeKnoraClientError as error:
        if error.status_code != 404:
            raise KBOperationError(f"WeKnora 删除未确认,文档 {file_id} 保留: {error}") from error
        logger.warning(f"WeKnora knowledge already gone: file_id={file_id}, remote={remote_knowledge_id}")
    return {"file_id": file_id, "remote_knowledge_id": remote_knowledge_id}


async def import_weknora_url(kb_id: str, *, url: str, client: WeKnoraClient | None = None) -> dict:
    """URL 导入交由远端执行(SSRF 校验在远端),返回 weknora 引用。"""

    from yuxi.services.weknora_kb_service import load_confirmed_binding

    _, binding, settings = await load_confirmed_binding(kb_id, action="导入 URL")
    remote_client = client or WeKnoraClient(settings)
    try:
        response = await remote_client.request(
            "POST",
            f"knowledge-bases/{binding['remote_kb_id']}/knowledge/url",
            json={"url": url},
        )
    except WeKnoraClientError as error:
        raise KBOperationError(f"WeKnora URL 导入失败: {error}") from error

    data = _extract_data(response)
    remote_id = str(data.get("id") or "")
    if not remote_id:
        raise KBOperationError("WeKnora URL 导入响应缺少文档 ID,结果不确定,请核对远端后处理")
    title = str(data.get("title") or url)
    return {
        "remote_knowledge_id": remote_id,
        "ref": build_weknora_file_ref(remote_id, title),
        "remote_status": str(data.get("parse_status") or "pending"),
        "filename": title,
        "size": int(data.get("file_size") or 0),
        "content_hash": data.get("file_hash"),
    }


async def create_weknora_manual(
    kb_id: str,
    *,
    title: str,
    markdown: str,
    client: WeKnoraClient | None = None,
) -> dict:
    """创建手工 Markdown 文档,返回 weknora 引用供本地登记。"""

    from yuxi.services.weknora_kb_service import load_confirmed_binding

    _, binding, settings = await load_confirmed_binding(kb_id, action="创建手工文档")
    remote_client = client or WeKnoraClient(settings)
    try:
        response = await remote_client.request(
            "POST",
            f"knowledge-bases/{binding['remote_kb_id']}/knowledge/manual",
            json={"title": title, "content": markdown},
        )
    except WeKnoraClientError as error:
        raise KBOperationError(f"WeKnora 手工文档创建失败: {error}") from error

    data = _extract_data(response)
    remote_id = str(data.get("id") or "")
    if not remote_id:
        raise KBOperationError("WeKnora 手工文档响应缺少文档 ID,结果不确定,请核对远端后处理")
    return {
        "remote_knowledge_id": remote_id,
        "ref": build_weknora_file_ref(remote_id, title),
        "remote_status": str(data.get("parse_status") or "pending"),
        "filename": title,
        "content_hash": data.get("file_hash"),
    }


async def update_weknora_document(
    kb_id: str,
    file_id: str,
    *,
    title: str | None = None,
    description: str | None = None,
    markdown: str | None = None,
    client: WeKnoraClient | None = None,
) -> dict:
    """更新托管文档:标题/描述同步远端元信息,Markdown 正文走手工文档接口。

    更新幂等,失败时本地不落任何变更。
    """

    from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository
    from yuxi.services.weknora_kb_service import load_confirmed_binding

    repo = KnowledgeFileRepository()
    record = await repo.get_by_file_id(file_id)
    if record is None or record.kb_id != kb_id:
        raise KBOperationError(f"文档 {file_id} 不属于知识库 {kb_id}")
    remote_knowledge_id = getattr(record, "remote_knowledge_id", None)
    if not remote_knowledge_id:
        raise KBOperationError(f"文档 {file_id} 缺少远端绑定")

    _, binding, settings = await load_confirmed_binding(kb_id, action="更新文档")
    remote_client = client or WeKnoraClient(settings)
    try:
        if title or description:
            payload: dict[str, Any] = {}
            if title:
                payload["title"] = title
            if description:
                payload["description"] = description
            await remote_client.request("PUT", f"knowledge/{remote_knowledge_id}", json=payload)
        if markdown is not None:
            manual_payload: dict[str, Any] = {"content": markdown}
            if title:
                manual_payload["title"] = title
            await remote_client.request("PUT", f"knowledge/manual/{remote_knowledge_id}", json=manual_payload)
    except WeKnoraClientError as error:
        raise KBOperationError(f"WeKnora 文档更新失败,本地未变更: {error}") from error

    if title and title != record.filename:
        await repo.update_fields(file_id=file_id, data={"filename": title}, kb_id=kb_id)
    return {"file_id": file_id, "title": title or record.filename, "markdown_updated": markdown is not None}
