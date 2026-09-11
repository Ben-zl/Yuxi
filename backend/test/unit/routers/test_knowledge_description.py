from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import yuxi.models
from server.routers import knowledge_router


@pytest.mark.asyncio
async def test_generate_description_uses_fast_model(monkeypatch):
    selected = {}

    class FakeModel:
        async def call(self, prompt, stream=False):
            selected["prompt"] = prompt
            selected["stream"] = stream
            return SimpleNamespace(content="适合查询产品功能和使用方法。")

    async def get_options(self, db):
        return {
            "default_model": "provider:default-chat",
            "fast_model": "provider:fast-chat",
        }

    def select_model(*, model_spec):
        selected["model_spec"] = model_spec
        return FakeModel()

    monkeypatch.setattr(type(knowledge_router.system_options), "get", get_options)
    monkeypatch.setattr(yuxi.models, "select_model", select_model)

    result = await knowledge_router.generate_description(
        name="产品手册",
        current_description="产品资料",
        file_list=["manual.md"],
        current_user=SimpleNamespace(uid="admin"),
        db=object(),
    )

    assert selected["model_spec"] == "provider:fast-chat"
    assert selected["stream"] is False
    assert "manual.md" in selected["prompt"]
    assert result == {
        "description": "适合查询产品功能和使用方法。",
        "status": "success",
    }


@pytest.mark.asyncio
async def test_generate_description_rejects_empty_model_response(monkeypatch):
    class FakeModel:
        async def call(self, prompt, stream=False):
            return SimpleNamespace(content="   ")

    async def get_options(self, db):
        return {"default_model": "provider:default-chat", "fast_model": "provider:fast-chat"}

    monkeypatch.setattr(type(knowledge_router.system_options), "get", get_options)
    monkeypatch.setattr(yuxi.models, "select_model", lambda *, model_spec: FakeModel())

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_router.generate_description(
            name="产品手册",
            current_description="产品资料",
            file_list=[],
            current_user=SimpleNamespace(uid="admin"),
            db=object(),
        )

    assert exc_info.value.status_code == 502
    assert exc_info.value.detail == "模型未返回有效描述"


@pytest.mark.asyncio
async def test_generate_description_hides_model_error_details(monkeypatch):
    class FailingModel:
        async def call(self, prompt, stream=False):
            raise RuntimeError("provider credential leaked detail")

    async def get_options(self, db):
        return {"fast_model": "provider:fast-chat"}

    monkeypatch.setattr(type(knowledge_router.system_options), "get", get_options)
    monkeypatch.setattr(yuxi.models, "select_model", lambda *, model_spec: FailingModel())

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_router.generate_description(
            name="产品手册",
            current_description="产品资料",
            file_list=[],
            current_user=SimpleNamespace(uid="admin"),
            db=object(),
        )

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "生成描述失败"
