"""知识库 Dashboard 聚合统计单元测试。"""

from types import SimpleNamespace

import pytest

from yuxi.services import knowledge_dashboard_service

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


async def test_knowledge_dashboard_uses_repository_aggregates(monkeypatch):
    """知识统计只编排 repository 聚合结果，不逐库加载文件。"""
    monkeypatch.setenv("KNOWLEDGE_BACKEND", "builtin")
    base_repository = SimpleNamespace()
    file_repository = SimpleNamespace()

    async def count_by_type(kb_types=None):
        return [("milvus", 2), ("custom", 1)]

    async def aggregate_dashboard_stats(kb_ids=None):
        return [("pdf", 3, 1200, 24), ("unknown", 1, 100, 0)]

    async def list_kb_ids_by_type(kb_types):
        return {"kb_1", "kb_2", "kb_3"}

    base_repository.count_by_type = count_by_type
    base_repository.list_kb_ids_by_type = list_kb_ids_by_type
    file_repository.aggregate_dashboard_stats = aggregate_dashboard_stats
    monkeypatch.setattr(knowledge_dashboard_service, "KnowledgeBaseRepository", lambda: base_repository)
    monkeypatch.setattr(knowledge_dashboard_service, "KnowledgeFileRepository", lambda: file_repository)

    stats = await knowledge_dashboard_service.get_knowledge_stats()

    assert stats == {
        "total_databases": 3,
        "total_files": 4,
        "total_nodes": 24,
        "total_storage_size": 1300,
        "databases_by_type": {"Milvus": 2, "custom": 1},
        "file_type_distribution": {"PDF文档": 3, "其他": 1},
    }
