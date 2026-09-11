from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from server.routers import knowledge_router


@pytest.mark.asyncio
async def test_query_test_maps_runtime_failure_to_http_error(monkeypatch):
    async def fail_query(*args, **kwargs):
        raise RuntimeError("milvus unavailable")

    monkeypatch.setattr(knowledge_router.knowledge_base, "aquery", fail_query)

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_router.query_test(
            "kb_1",
            query="测试问题",
            meta={},
            current_user=SimpleNamespace(uid="user_1"),
        )

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "知识库检索失败"
