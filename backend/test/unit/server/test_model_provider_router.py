from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from server.routers import model_provider_router
from server.routers.model_provider_router import ModelProviderPayload


def test_model_provider_payload_accepts_embedding_and_rerank_urls():
    payload = ModelProviderPayload(
        provider_id="mixed-provider",
        display_name="Mixed Provider",
        base_url="https://api.example.com/v1",
        embedding_base_url="https://api.example.com/v1/embeddings",
        rerank_base_url="https://api.example.com/v1/rerank",
        capabilities=["chat", "embedding", "rerank"],
    )

    data = payload.model_dump(exclude_none=True)

    assert data["embedding_base_url"] == "https://api.example.com/v1/embeddings"
    assert data["rerank_base_url"] == "https://api.example.com/v1/rerank"


def test_model_provider_payload_rejects_builtin_flag():
    with pytest.raises(ValueError, match="is_builtin"):
        ModelProviderPayload.model_validate(
            {
                "provider_id": "shadow-builtin",
                "display_name": "Shadow Builtin",
                "base_url": "https://api.example.com/v1",
                "is_builtin": True,
            }
        )


@pytest.mark.asyncio
async def test_update_provider_commits_before_refreshing_cache(monkeypatch):
    calls = []

    class Db:
        async def commit(self):
            calls.append("commit")

    class User:
        username = "admin"
        role = "admin"

    class Provider:
        def to_dict(self, *, sanitize=False):
            return {"provider_id": "alibaba"}

    async def fake_update_provider_config(db, provider_id, data, username, *, operator=None):
        calls.append("update")
        return Provider()

    async def fake_refresh_model_cache():
        calls.append("refresh")

    monkeypatch.setattr(model_provider_router, "update_provider_config", fake_update_provider_config)
    monkeypatch.setattr(model_provider_router, "_refresh_model_cache", fake_refresh_model_cache)

    result = await model_provider_router.update_provider(
        "alibaba",
        ModelProviderPayload(enabled_models=[]),
        current_user=User(),
        db=Db(),
    )

    assert result == {"success": True, "data": {"provider_id": "alibaba"}}
    assert calls == ["update", "commit", "refresh"]


@pytest.mark.asyncio
async def test_department_admin_cannot_run_management_probe_on_global_provider(monkeypatch):
    provider = SimpleNamespace(
        created_by="root",
        share_config={
            "version": 2,
            "read_scope": {"access_level": "global", "department_ids": [], "user_uids": []},
            "manage_scope": None,
        },
    )

    async def read_provider(*_args, **_kwargs):
        return provider

    monkeypatch.setattr(model_provider_router, "get_model_provider_by_id", read_provider)
    admin = SimpleNamespace(uid="dept-admin", role="admin", department_id=10)
    with pytest.raises(HTTPException) as exc_info:
        await model_provider_router._get_manageable_provider(object(), "global-provider", admin)
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_model_status_error_does_not_expose_provider_endpoint(monkeypatch):
    from yuxi.models.providers import cache, service

    monkeypatch.setattr(
        cache.model_cache,
        "get_model_info",
        lambda _spec: SimpleNamespace(model_type="chat"),
    )

    class FailingModel:
        async def call(self, *_args, **_kwargs):
            raise RuntimeError("URL: https://user:secret@example.test/v1?token=secret")

    monkeypatch.setattr("yuxi.models.chat.select_model", lambda **_kwargs: FailingModel())
    result = await service.test_model_status_by_spec("resource:model")
    assert result["message"] == "模型连接检查失败"
    assert "example.test" not in str(result)


@pytest.mark.asyncio
async def test_model_status_router_unexpected_error_is_sanitized(monkeypatch):
    async def manageable(*_args, **_kwargs):
        return SimpleNamespace()

    async def fail_status(_spec):
        raise RuntimeError("URL: https://user:secret@example.test/v1?token=secret")

    monkeypatch.setattr(model_provider_router, "_get_manageable_provider", manageable)
    monkeypatch.setattr(model_provider_router, "test_model_status_by_spec", fail_status)
    monkeypatch.setattr(
        "yuxi.models.providers.cache.model_cache.canonicalize_spec",
        lambda spec: spec,
    )
    result = await model_provider_router.get_model_status_by_spec(
        "resource:model",
        current_user=SimpleNamespace(uid="root", role="superadmin"),
        db=object(),
    )
    assert result == {
        "success": False,
        "data": {"spec": "resource:model", "status": "error", "message": "模型连接检查失败"},
    }
