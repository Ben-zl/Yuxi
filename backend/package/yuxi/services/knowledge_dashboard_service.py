"""知识域 Dashboard 统计用例。"""

from typing import Any

from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository
from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository

FILE_TYPE_MAPPING = {
    "txt": "文本文件",
    "pdf": "PDF文档",
    "docx": "Word文档",
    "doc": "Word文档",
    "md": "Markdown",
    "html": "HTML网页",
    "htm": "HTML网页",
    "json": "JSON数据",
    "csv": "CSV表格",
    "xlsx": "Excel表格",
    "xls": "Excel表格",
    "pptx": "PowerPoint",
    "ppt": "PowerPoint",
    "png": "PNG图片",
    "jpg": "JPEG图片",
    "jpeg": "JPEG图片",
    "gif": "GIF图片",
    "svg": "SVG图片",
    "mp4": "MP4视频",
    "mp3": "MP3音频",
    "zip": "ZIP压缩包",
    "rar": "RAR压缩包",
    "7z": "7Z压缩包",
}

DATABASE_TYPE_MAPPING = {
    "faiss": "FAISS",
    "milvus": "Milvus",
    "dify": "Dify",
    "qdrant": "Qdrant",
    "elasticsearch": "Elasticsearch",
    "weknora": "WeKnora",
    "unknown": "未知类型",
}


def _visible_kb_types() -> set[str]:
    """两套后端数据不混用:统计只计入当前模式的知识库类型。"""
    from yuxi.config.runtime import KNOWLEDGE_BACKEND_WEKNORA, knowledge_backend

    if knowledge_backend() == KNOWLEDGE_BACKEND_WEKNORA:
        return {"weknora"}
    return {"milvus", "dify", "notion", "faiss", "qdrant", "elasticsearch", "unknown"}


def _backend_uses_local_chunks() -> bool:
    """weknora 模式的 Chunk 在远端,本地 chunk_count 恒 0,nodes 不反映真实数据。"""
    from yuxi.config.runtime import KNOWLEDGE_BACKEND_WEKNORA, knowledge_backend

    return knowledge_backend() != KNOWLEDGE_BACKEND_WEKNORA


async def get_knowledge_stats() -> dict[str, Any]:
    """通过单批 SQL 聚合高效汇总知识库、文件类型与存储大小统计。"""

    visible_types = _visible_kb_types()

    databases_by_type: dict[str, int] = {}
    files_by_type: dict[str, int] = {}
    total_databases = 0
    total_files = 0
    total_nodes = 0
    total_storage_size = 0

    kb_repo = KnowledgeBaseRepository()
    visible_kb_ids = await kb_repo.list_kb_ids_by_type(visible_types)

    for kb_type, count in await kb_repo.count_by_type(kb_types=visible_types):
        db_type = kb_type.lower()
        display_type = DATABASE_TYPE_MAPPING.get(db_type, kb_type or "未知类型")
        databases_by_type[display_type] = databases_by_type.get(display_type, 0) + count
        total_databases += count

    for file_type, count, size, nodes in await KnowledgeFileRepository().aggregate_dashboard_stats(
        kb_ids=visible_kb_ids
    ):
        ext = file_type.lower()
        display_name = FILE_TYPE_MAPPING.get(
            ext,
            ext.upper() + "文件" if ext and ext != "unknown" else "其他",
        )
        files_by_type[display_name] = files_by_type.get(display_name, 0) + count
        total_files += count
        total_storage_size += size
        total_nodes += nodes

    result = {
        "total_databases": total_databases,
        "total_files": total_files,
        "total_nodes": total_nodes,
        "total_storage_size": total_storage_size,
        "databases_by_type": databases_by_type,
        "file_type_distribution": files_by_type,
    }
    if not _backend_uses_local_chunks():
        # weknora 模式:本地 chunk 恒 0,不伪装为真实节点数
        result["total_nodes_unavailable"] = True
    return result
