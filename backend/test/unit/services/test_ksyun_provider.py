"""星流模型发现的协议边界测试。"""

import httpx
import pytest

from yuxi.models.providers.builtin import BUILTIN_PROVIDERS
from yuxi.models.providers.service import _normalize_ksyun_model, _normalize_payload, fetch_remote_models
from yuxi.storage.postgres.models_business import ModelProvider


@pytest.mark.asyncio
async def test_ksyun_discovery_filters_non_chat_and_converts_metadata(httpx_mock):
    """混合模型清单只暴露文字生成模型，并保留原始元数据。"""
    template = next(item for item in BUILTIN_PROVIDERS if item["provider_id"] == "ksyun")
    provider = ModelProvider(**_normalize_payload({**template, "api_key": "test-ksyun-key"}))
    raw = {
        "id": "qwen-flash",
        "context_length": "1024k",
        "max_completion_tokens": "32k",
        "architecture": {"input_modalities": ["文字", "图片"], "output_modalities": ["文字"]},
    }
    httpx_mock.add_response(
        url="https://kspmas.ksyun.com/v1/models",
        match_headers={"Authorization": "Bearer test-ksyun-key"},
        json={
            "data": [
                raw,
                *[
                    {"id": f"other-{i}", "architecture": {"output_modalities": outputs}}
                    for i, outputs in enumerate((["视频"], ["图片"], ["5"], [], ["文件"]))
                ],
                {"id": "no-metadata"},
            ]
        },
    )
    models = await fetch_remote_models(provider)
    assert [model["id"] for model in models] == ["qwen-flash"]
    assert models[0]["context_length"] == 1048576
    assert models[0]["max_completion_tokens"] == 32768
    assert models[0]["input_modalities"] == ["text", "image"]
    assert models[0]["raw_metadata"] == raw
    assert provider.provider_type == "openai"


@pytest.mark.asyncio
async def test_ksyun_invalid_key_propagates_authentication_error(httpx_mock):
    """凭证拒绝不会伪装为成功的空模型清单。"""
    template = next(item for item in BUILTIN_PROVIDERS if item["provider_id"] == "ksyun")
    provider = ModelProvider(**_normalize_payload({**template, "api_key": "invalid-test-key"}))
    httpx_mock.add_response(url=template["models_endpoint"], status_code=401)
    with pytest.raises(httpx.HTTPStatusError) as error:
        await fetch_remote_models(provider)
    assert error.value.response.status_code == 401


@pytest.mark.parametrize(
    "value, expected",
    [
        ("1m", 1048576),
        ("1.5k", 1536),
        (2048, 2048),
        ("bad", None),
        ("0", None),
        ("-1", None),
        ("1.1", None),
        (None, None),
    ],
)
def test_ksyun_token_limits_preserve_only_positive_integer_counts(value, expected):
    """非法长度不进入运行字段，原始响应仍可检查。"""
    raw = {
        "id": "chat",
        "architecture": {"output_modalities": ["text"]},
        "context_length": value,
        "max_completion_tokens": value,
    }
    model = _normalize_ksyun_model(raw)
    assert model.get("context_length") == expected
    assert model.get("max_completion_tokens") == expected
    assert model["raw_metadata"] == raw


@pytest.mark.asyncio
async def test_ksyun_malformed_modalities_do_not_hide_valid_models(httpx_mock):
    """错误模态条目不会使同批有效模型发现失败。"""
    template = next(item for item in BUILTIN_PROVIDERS if item["provider_id"] == "ksyun")
    provider = ModelProvider(**_normalize_payload(template))
    httpx_mock.add_response(
        url=template["models_endpoint"],
        json={
            "data": [
                {"id": "invalid-output", "architecture": {"output_modalities": 5}},
                {"id": "invalid-input", "architecture": {"output_modalities": ["text"], "input_modalities": [{}]}},
                {"id": "string-output", "architecture": {"output_modalities": "text"}},
                {"id": "valid", "architecture": {"output_modalities": ["text"]}},
            ]
        },
    )
    assert [model["id"] for model in await fetch_remote_models(provider)] == ["valid"]


@pytest.mark.asyncio
async def test_ksyun_text_modalities_do_not_turn_retrieval_models_into_chat(httpx_mock):
    """星流三个文字输出模型按真实用途分类，并只请求一次混合清单。"""
    template = next(item for item in BUILTIN_PROVIDERS if item["provider_id"] == "ksyun")
    provider = ModelProvider(**_normalize_payload(template))
    httpx_mock.add_response(
        url=template["models_endpoint"],
        json={
            "data": [
                {
                    "id": model_id,
                    "context_length": "32k",
                    "architecture": {"input_modalities": ["文字"], "output_modalities": ["文字"]},
                }
                for model_id in ("qwen3-embedding-8b", "qwen3-reranker-8b", "qwen-flash")
            ]
        },
    )
    models = await fetch_remote_models(provider)
    assert [(m["id"], m["type"]) for m in models] == [
        ("qwen3-embedding-8b", "embedding"),
        ("qwen3-reranker-8b", "rerank"),
        ("qwen-flash", "chat"),
    ]
    assert models[0]["dimension"] == 4096
    assert set(provider.capabilities) == {"chat", "embedding", "rerank"}
    assert provider.embedding_base_url == "https://kspmas.ksyun.com/v1/embeddings"
    assert provider.rerank_base_url == "https://kspmas.ksyun.com/v1/rerank"
