"""WeKnora 托管文档的上传、状态同步与重新解析用例。

远端持有内容与处理状态;本地仅保存目录位置与 remote_knowledge_id 绑定。
状态同步失败时保留本地旧值,不把远端故障伪装成文档终态。
"""

from __future__ import annotations

import os
import uuid

from typing import Any

from yuxi.knowledge.base import KBOperationError
from yuxi.knowledge.weknora import WeKnoraClient, WeKnoraClientError, build_weknora_file_ref, load_weknora_settings
from yuxi.services.weknora_kb_service import _extract_data, load_confirmed_binding
from yuxi.utils import logger

# 状态同步挂在列表读路径上,用短超时避免远端故障阻塞列表请求
STATUS_SYNC_TIMEOUT_SECONDS = 5.0
DOCUMENT_PENDING_REVIEW = "pending_review"


def _client_for_kb(*, timeout: float = 30.0) -> WeKnoraClient:
    settings = load_weknora_settings()
    if not settings.ready:
        raise KBOperationError("WeKnora 部署配置不完整,无法执行文档用例")
    return WeKnoraClient(settings, timeout=timeout)


async def _create_document_intent(
    kb_id: str,
    *,
    filename: str,
    operator_id: str,
    content_hash: str | None = None,
    file_size: int | None = None,
    content_type: str | None = None,
    parent_id: str | None = None,
) -> tuple[str, str]:
    """在远端非幂等写之前持久化可核对的本地文档意向。"""

    from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository

    file_id = f"file_{uuid.uuid4().hex}"
    ref = build_weknora_file_ref(file_id, filename)
    await KnowledgeFileRepository().upsert(
        file_id,
        {
            "kb_id": kb_id,
            "parent_id": parent_id,
            "filename": filename,
            "original_filename": filename,
            "file_type": filename.rsplit(".", 1)[-1].lower() if "." in filename else "",
            "path": ref,
            "status": DOCUMENT_PENDING_REVIEW,
            "content_hash": content_hash,
            "file_size": file_size,
            "content_type": content_type,
            "is_folder": False,
            "error_message": "远端写入结果待核对",
            "created_by": operator_id,
        },
    )
    return file_id, ref


async def _confirm_document_intent(
    kb_id: str,
    file_id: str,
    *,
    remote_knowledge_id: str,
    status: str,
    filename: str | None = None,
    file_size: int | None = None,
    content_hash: str | None = None,
) -> None:
    """把远端已确认资源绑定到预先登记的本地文档意向。"""

    from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository

    updates: dict[str, Any] = {
        "remote_knowledge_id": remote_knowledge_id,
        "status": status,
        "error_message": None,
    }
    if filename:
        updates["filename"] = filename
    if file_size is not None:
        updates["file_size"] = file_size
    if content_hash:
        updates["content_hash"] = content_hash
    updated = await KnowledgeFileRepository().update_fields(file_id=file_id, data=updates, kb_id=kb_id)
    if updated is None:
        raise KBOperationError(f"本地文档意向 {file_id} 不存在,远端资源 {remote_knowledge_id} 需人工核对")


async def _mark_document_intent_error(kb_id: str, file_id: str, message: str, *, definitive: bool) -> None:
    """记录远端写失败;传输结果不确定时保留 pending_review。"""

    from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository

    await KnowledgeFileRepository().update_fields(
        file_id=file_id,
        kb_id=kb_id,
        data={"status": "failed" if definitive else DOCUMENT_PENDING_REVIEW, "error_message": message},
    )


async def upload_weknora_file(
    kb_id: str,
    *,
    filename: str,
    content: bytes,
    content_type: str | None = None,
    operator_id: str,
    content_hash: str | None = None,
    parent_id: str | None = None,
    client: WeKnoraClient | None = None,
) -> dict:
    """把文件直接上传到远端托管库,返回仅含本地 ID 的登记引用与远端初始状态。

    上传为非幂等写:超时/传输失败直接报错,不自动重试;远端可能已创建资源,
    由调用方提示待核对。远端 409 表示内容重复,作为查重结果透出。
    """

    _, binding, settings = await load_confirmed_binding(kb_id, action="上传文档")
    file_id, ref = await _create_document_intent(
        kb_id,
        filename=filename,
        operator_id=operator_id,
        content_hash=content_hash,
        file_size=len(content),
        content_type=content_type,
        parent_id=parent_id,
    )
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
        definitive = error.status_code is not None and 400 <= error.status_code < 500
        await _mark_document_intent_error(kb_id, file_id, str(error), definitive=definitive)
        if error.status_code == 409:
            raise KBOperationError("远端已存在相同内容文件") from error
        outcome = "失败" if definitive else "结果不确定"
        raise KBOperationError(f"WeKnora 上传{outcome},本地文档 {file_id} 已保留供核对: {error}") from error

    data = _extract_data(response)
    remote_knowledge_id = str(data.get("id") or data.get("knowledge_id") or "")
    if not remote_knowledge_id:
        await _mark_document_intent_error(kb_id, file_id, "远端响应缺少文档 ID", definitive=False)
        raise KBOperationError(f"WeKnora 上传结果不确定:响应缺少文档 ID,本地文档 {file_id} 保留待核对")
    remote_status = str(data.get("parse_status") or "pending")
    await _confirm_document_intent(
        kb_id,
        file_id,
        remote_knowledge_id=remote_knowledge_id,
        status=remote_status,
        file_size=len(content),
        content_hash=content_hash,
    )
    return {
        "file_id": file_id,
        "remote_knowledge_id": remote_knowledge_id,
        "ref": ref,
        "remote_status": remote_status,
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


async def import_weknora_url(
    kb_id: str,
    *,
    url: str,
    operator_id: str,
    parent_id: str | None = None,
    client: WeKnoraClient | None = None,
) -> dict:
    """URL 导入交由远端执行(SSRF 校验在远端),返回 weknora 引用。"""

    from yuxi.services.weknora_kb_service import load_confirmed_binding

    _, binding, settings = await load_confirmed_binding(kb_id, action="导入 URL")
    file_id, ref = await _create_document_intent(
        kb_id,
        filename=url,
        operator_id=operator_id,
        parent_id=parent_id,
    )
    remote_client = client or WeKnoraClient(settings)
    try:
        response = await remote_client.request(
            "POST",
            f"knowledge-bases/{binding['remote_kb_id']}/knowledge/url",
            json={"url": url},
        )
    except WeKnoraClientError as error:
        definitive = error.status_code is not None and 400 <= error.status_code < 500
        await _mark_document_intent_error(kb_id, file_id, str(error), definitive=definitive)
        outcome = "失败" if definitive else "结果不确定"
        raise KBOperationError(f"WeKnora URL 导入{outcome},本地文档 {file_id} 已保留供核对: {error}") from error

    data = _extract_data(response)
    remote_id = str(data.get("id") or "")
    if not remote_id:
        await _mark_document_intent_error(kb_id, file_id, "远端响应缺少文档 ID", definitive=False)
        raise KBOperationError(f"WeKnora URL 导入响应缺少文档 ID,本地文档 {file_id} 保留待核对")
    title = str(data.get("title") or url)
    remote_status = str(data.get("parse_status") or "pending")
    await _confirm_document_intent(
        kb_id,
        file_id,
        remote_knowledge_id=remote_id,
        status=remote_status,
        filename=title,
        file_size=int(data.get("file_size") or 0),
        content_hash=data.get("file_hash"),
    )
    return {
        "file_id": file_id,
        "remote_knowledge_id": remote_id,
        "ref": ref,
        "remote_status": remote_status,
        "filename": title,
        "size": int(data.get("file_size") or 0),
        "content_hash": data.get("file_hash"),
    }


async def create_weknora_manual(
    kb_id: str,
    *,
    title: str,
    markdown: str,
    operator_id: str,
    parent_id: str | None = None,
    client: WeKnoraClient | None = None,
) -> dict:
    """创建手工 Markdown 文档,返回 weknora 引用供本地登记。"""

    from yuxi.services.weknora_kb_service import load_confirmed_binding

    _, binding, settings = await load_confirmed_binding(kb_id, action="创建手工文档")
    file_id, ref = await _create_document_intent(
        kb_id,
        filename=title,
        operator_id=operator_id,
        content_type="text/markdown",
        parent_id=parent_id,
    )
    remote_client = client or WeKnoraClient(settings)
    try:
        response = await remote_client.request(
            "POST",
            f"knowledge-bases/{binding['remote_kb_id']}/knowledge/manual",
            json={"title": title, "content": markdown, "status": "publish"},
        )
    except WeKnoraClientError as error:
        definitive = error.status_code is not None and 400 <= error.status_code < 500
        await _mark_document_intent_error(kb_id, file_id, str(error), definitive=definitive)
        outcome = "失败" if definitive else "结果不确定"
        raise KBOperationError(f"WeKnora 手工文档创建{outcome},本地文档 {file_id} 已保留供核对: {error}") from error

    data = _extract_data(response)
    remote_id = str(data.get("id") or "")
    if not remote_id:
        await _mark_document_intent_error(kb_id, file_id, "远端响应缺少文档 ID", definitive=False)
        raise KBOperationError(f"WeKnora 手工文档响应缺少文档 ID,本地文档 {file_id} 保留待核对")
    remote_status = str(data.get("parse_status") or "pending")
    await _confirm_document_intent(
        kb_id,
        file_id,
        remote_knowledge_id=remote_id,
        status=remote_status,
        content_hash=data.get("file_hash"),
    )
    return {
        "file_id": file_id,
        "remote_knowledge_id": remote_id,
        "ref": ref,
        "remote_status": remote_status,
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
            manual_payload: dict[str, Any] = {"content": markdown, "status": "publish"}
            if title:
                manual_payload["title"] = title
            await remote_client.request("PUT", f"knowledge/manual/{remote_knowledge_id}", json=manual_payload)
    except WeKnoraClientError as error:
        raise KBOperationError(f"WeKnora 文档更新失败,本地未变更: {error}") from error

    if title and title != record.filename:
        await repo.update_fields(file_id=file_id, data={"filename": title}, kb_id=kb_id)
    return {"file_id": file_id, "title": title or record.filename, "markdown_updated": markdown is not None}


async def download_weknora_document_stream(
    kb_id: str,
    file_id: str,
    *,
    variant: str = "original",
    client: WeKnoraClient | None = None,
):
    """代理远端文档字节流:统一 Key 不出 WeKnora 同源边界。

    返回 (字节异步迭代器, 文件名, media_type)。远端 3xx 时仅允许同源
    (或部署显式配置的下载域) Location,且第二跳不携带 API Key。
    """

    import mimetypes
    from urllib.parse import urljoin, urlparse

    from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository
    from yuxi.services.weknora_kb_service import load_confirmed_binding

    if variant not in {"original", "parsed"}:
        raise KBOperationError("不支持的下载变体")

    repo = KnowledgeFileRepository()
    record = await repo.get_by_file_id(file_id)
    if record is None or record.kb_id != kb_id:
        raise KBOperationError(f"文档 {file_id} 不属于知识库 {kb_id}")
    remote_knowledge_id = getattr(record, "remote_knowledge_id", None)
    if not remote_knowledge_id:
        raise KBOperationError(f"文档 {file_id} 缺少远端绑定")

    filename = record.filename or file_id
    if variant == "parsed":
        content = await load_remote_parsed_content(kb_id, file_id)

        async def _parsed_bytes():
            yield content.encode("utf-8")

        return _parsed_bytes(), f"{filename}.parsed.md", "text/markdown; charset=utf-8"

    _, _, settings = await load_confirmed_binding(kb_id, action="下载文档")
    remote_client = client or WeKnoraClient(settings, timeout=120.0)
    base_host = urlparse(settings.base_url).hostname or ""
    allowed_hosts = {base_host}
    for extra in filter(None, (os.environ.get("WEKNORA_DOWNLOAD_HOSTS") or "").split(",")):
        allowed_hosts.add(extra.strip())

    download_path = f"knowledge/{remote_knowledge_id}/download"
    try:
        first = await remote_client.request("GET", download_path, raw_redirects=True)
        if 300 <= first.status_code < 400:
            location = first.headers.get("location") or ""
            if not location:
                raise KBOperationError("远端下载重定向缺少 Location")
            target = urljoin(settings.base_url, location)
            target_host = urlparse(target).hostname or ""
            if target_host not in allowed_hosts:
                raise KBOperationError(f"远端下载重定向目标不在允许域(拒绝越界): {target_host}")
            # 第二跳为对象存储签名地址,不携带统一 API Key
            second = await remote_client.request("GET", target, authenticated=False)
            content_bytes = second.content
        else:
            content_bytes = first.content
    except WeKnoraClientError as error:
        raise KBOperationError(f"WeKnora 文档下载失败: {error}") from error

    media_type = record.content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"

    async def _bytes():
        yield content_bytes

    return _bytes(), filename, media_type


async def load_remote_parsed_content(kb_id: str, file_id: str, *, client: WeKnoraClient | None = None) -> str:
    """读取手工 Markdown 原文或完整分页后的远端分块内容。"""

    from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository

    record = await KnowledgeFileRepository().get_by_file_id(file_id)
    if record is None or record.kb_id != kb_id:
        raise KBOperationError(f"文档 {file_id} 不属于知识库 {kb_id}")
    remote_knowledge_id = getattr(record, "remote_knowledge_id", None)
    if not remote_knowledge_id:
        raise KBOperationError(f"文档 {file_id} 缺少远端绑定")
    remote_client = client or _client_for_kb()
    try:
        response = await remote_client.request("GET", f"knowledge/{remote_knowledge_id}")
    except WeKnoraClientError as error:
        raise KBOperationError(f"远端文档读取失败: {error}") from error
    data = response.json().get("data") or {}
    if str(data.get("type") or "").lower() == "manual":
        metadata = data.get("metadata") or {}
        if isinstance(metadata, dict) and isinstance(metadata.get("content"), str):
            return metadata["content"]
    return await _concat_remote_chunks(remote_client, remote_knowledge_id)


async def _concat_remote_chunks(client: WeKnoraClient, remote_knowledge_id: str) -> str:
    """按远端顺序拼接分块内容,作为解析文本回退。"""

    page = 1
    page_size = 100
    chunks: list[dict] = []
    while True:
        try:
            response = await client.request(
                "GET",
                f"chunks/{remote_knowledge_id}",
                params={"page": page, "page_size": page_size},
            )
        except WeKnoraClientError as error:
            logger.warning(f"WeKnora chunks fallback failed: remote={remote_knowledge_id}: {error}")
            return ""
        payload = response.json()
        raw = payload.get("data") or []
        if isinstance(raw, dict):
            raw = raw.get("items") or []
        page_items = [item for item in raw if isinstance(item, dict)]
        chunks.extend(page_items)
        total = int(payload.get("total") or len(chunks))
        if not page_items or len(chunks) >= total or len(page_items) < page_size:
            break
        page += 1

    def _order(item: dict) -> tuple:
        seq = item.get("seq")
        index = item.get("chunk_index")
        return (int(seq) if isinstance(seq, (int, float)) else 0, int(index) if isinstance(index, (int, float)) else 0)

    chunks.sort(key=_order)
    return "\n\n".join(str(item.get("content") or "") for item in chunks).strip()


async def query_weknora_graph(
    kb_id: str,
    *,
    keyword: str = "*",
    max_nodes: int = 100,
    offset: int = 0,
    client: WeKnoraClient | None = None,
) -> dict:
    """查询托管知识库的远端实体图谱,映射为内置图谱接口的节点/边形状。"""

    from yuxi.services.weknora_kb_service import load_confirmed_binding

    _, binding, settings = await load_confirmed_binding(kb_id, action="查询图谱")
    remote_client = client or WeKnoraClient(settings)
    remote_keyword = "" if keyword in ("*", "", "all") else keyword
    try:
        response = await remote_client.request(
            "GET",
            f"knowledge-bases/{binding['remote_kb_id']}/graph",
            params={"keyword": remote_keyword, "limit": max_nodes, "offset": offset},
        )
    except WeKnoraClientError as error:
        raise KBOperationError(f"WeKnora 图谱查询失败: {error}") from error

    data = response.json().get("data") or {}
    nodes = []
    for item in data.get("nodes") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("id") or "")
        if not name:
            continue
        props = item.get("properties") or {}
        nodes.append(
            {
                "id": name,
                "name": name,
                "original_id": name,
                "type": str(props.get("type") or "Entity"),
                "labels": [str(label) for label in item.get("labels") or []],
                "properties": props,
                "normalized": {"name": name, "type": "Entity", "source": "weknora"},
                "graph_type": "weknora",
            }
        )
    node_ids = {node["id"] for node in nodes}
    edges = []
    for item in data.get("edges") or []:
        if not isinstance(item, dict):
            continue
        source = str(item.get("source") or "")
        target = str(item.get("target") or "")
        if source not in node_ids or target not in node_ids:
            continue
        edges.append(
            {
                "id": str(item.get("id") or f"{source}->{target}"),
                "source_id": source,
                "target_id": target,
                "type": str(item.get("type") or "RELATED"),
                "properties": item.get("properties") or {},
                "normalized": {"type": str(item.get("type") or "RELATED"), "direction": "directed"},
            }
        )
    return {
        "nodes": nodes,
        "edges": edges,
        "total_nodes": int(data.get("total_nodes") or len(nodes)),
        "total_edges": int(data.get("total_edges") or len(edges)),
    }


async def get_weknora_graph_labels(kb_id: str, *, client: WeKnoraClient | None = None) -> list[str]:
    """远端实体图谱标签:按节点 type 属性聚合。"""

    graph = await query_weknora_graph(kb_id, keyword="", max_nodes=1000, client=client)
    labels = sorted({node["type"] for node in graph["nodes"] if node.get("type")})
    return labels


async def get_weknora_graph_stats(kb_id: str, *, client: WeKnoraClient | None = None) -> dict:
    """远端实体图谱统计(内置 stats 契约:total_nodes/total_edges/entity_types)。"""

    graph = await query_weknora_graph(kb_id, keyword="", max_nodes=1, client=client)
    labels = await get_weknora_graph_labels(kb_id, client=client)
    return {
        "total_nodes": graph["total_nodes"],
        "total_edges": graph["total_edges"],
        "entity_types": labels,
    }
