"""简单调用必须在选择全局缓存模型之前授权。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from yuxi.services import model_call_service as service


@pytest.mark.parametrize("meta", [{"model_spec": "private:model"}, {"model": "private:model"}, None])
async def test_invisible_provider_never_selects_cached_model(monkeypatch, meta):
    """显式、旧字段与默认模型均不可绕过授权。"""
    monkeypatch.setattr(service.config, "default_model", "private:model")
    lookup = AsyncMock(return_value=None)
    select = Mock(side_effect=AssertionError("未经授权读取了模型缓存"))
    monkeypatch.setattr(service, "get_model_provider_reference", lookup)
    monkeypatch.setattr(service, "select_model", select)
    user, db = object(), object()
    with pytest.raises(HTTPException) as exc:
        await service.call_model(query="hello", meta=meta, user=user, db=db)
    assert exc.value.status_code == 404
    assert exc.value.detail == "模型不存在或无权访问"
    lookup.assert_awaited_once_with(db, "private", user=user)
    select.assert_not_called()


@pytest.mark.parametrize(
    "enabled,models",
    [(False, [{"id": "model", "type": "chat"}]), (True, []), (True, [{"id": "model", "type": "embedding"}])],
)
async def test_unavailable_database_model_never_uses_stale_cache(monkeypatch, enabled, models):
    """数据库停用或移除模型后拒绝旧缓存条目。"""
    provider = SimpleNamespace(resource_id="canonical", is_enabled=enabled, enabled_models=models)
    monkeypatch.setattr(service, "get_model_provider_reference", AsyncMock(return_value=provider))
    select = Mock(side_effect=AssertionError("使用了失效模型"))
    monkeypatch.setattr(service, "select_model", select)
    with pytest.raises(HTTPException) as exc:
        await service.call_model(query="hello", meta={"model": "old:model"}, user=object(), db=object())
    assert exc.value.status_code == 404
    assert exc.value.detail == "模型不可用"
    select.assert_not_called()


async def test_authorized_legacy_spec_invokes_canonical_model(monkeypatch):
    """成功响应保留 request_id 并使用规范资源 ID。"""
    provider = SimpleNamespace(
        resource_id="canonical", is_enabled=True, enabled_models=[{"id": "org/model", "type": "chat"}]
    )
    monkeypatch.setattr(service, "get_model_provider_reference", AsyncMock(return_value=provider))
    model = SimpleNamespace(call=AsyncMock(return_value=SimpleNamespace(content="answer")))
    select = Mock(return_value=model)
    monkeypatch.setattr(service, "select_model", select)
    result = await service.call_model(
        query="hello", meta={"model": "old:org/model", "request_id": "request"}, user=object(), db=object()
    )
    assert result == {"response": "answer", "request_id": "request"}
    select.assert_called_once_with(model_spec="canonical:org/model")
    model.call.assert_awaited_once_with("hello")
