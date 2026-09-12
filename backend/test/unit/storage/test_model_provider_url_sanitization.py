from yuxi.storage.postgres.models_business import ModelProvider


def test_provider_sanitized_read_hides_sensitive_urls_and_marks_configured_fields():
    provider = ModelProvider(
        resource_id="provider-resource",
        provider_id="provider",
        display_name="Provider",
        provider_type="openai",
        base_url="https://user:pass@example.test/v1",
        embedding_base_url="https://example.test/embeddings?token=secret",
        rerank_base_url="https://example.test/rerank#secret",
        models_endpoint="/models?api_key=secret",
        embedding_models_endpoint="/embeddings/models",
        rerank_models_endpoint="https://example.test/models#secret",
    )

    data = provider.to_dict(sanitize=True)

    for field in (
        "base_url",
        "embedding_base_url",
        "rerank_base_url",
        "models_endpoint",
        "rerank_models_endpoint",
    ):
        assert field not in data
        assert data[f"{field}_configured"] is True
    assert data["embedding_models_endpoint"] == "/embeddings/models"
    assert data["embedding_models_endpoint_configured"] is True
