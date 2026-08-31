"""Agent 配置到 AgentScope 原生上下文配置的投影测试。"""

from types import SimpleNamespace

import pytest

from yuxi.agentscope.projection import (
    project_agent_request,
    project_embedding_model,
    project_subagent_template,
)


def test_project_agent_request_uses_native_context_config():
    agent = SimpleNamespace(
        name="分析助手",
        config_json={
            "context": {
                "system_prompt": "分析问题",
                "max_execution_steps": 42,
                "summary_trigger_ratio": 0.75,
                "summary_reserve_ratio": 0.2,
                "summary_prompt": "生成可继续执行的摘要",
                "summary_tool_result_token_limit": 12000,
            }
        },
    )

    request = project_agent_request(agent)

    assert request["react_config"] == {"max_iters": 42}
    assert request["context_config"] == {
        "trigger_ratio": 0.75,
        "reserve_ratio": 0.2,
        "compression_prompt": "生成可继续执行的摘要",
        "tool_result_limit": 12000,
    }


def test_project_subagent_template_preserves_execution_limit():
    agent = SimpleNamespace(
        slug="researcher",
        name="Researcher",
        description="research",
        config_json={"context": {"system_prompt": "prompt", "max_execution_steps": 42}},
    )

    template = project_subagent_template(agent)

    assert template["react_config"] == {"max_iters": 42}


@pytest.mark.parametrize(
    ("context", "message"),
    [
        ({"summary_trigger_ratio": 0.9}, "触发比例"),
        ({"summary_trigger_ratio": 0.5, "summary_reserve_ratio": 0.5}, "保留比例"),
        ({"summary_tool_result_token_limit": 0}, "Token 上限"),
    ],
)
def test_project_agent_request_rejects_invalid_context_config(context, message):
    agent = SimpleNamespace(name="助手", config_json={"context": context})

    with pytest.raises(ValueError, match=message):
        project_agent_request(agent)


def test_project_embedding_model_uses_embedding_endpoint_headers_and_dimension():
    """Memory Embedding 必须消费 provider 的专用端点与模型维度。"""
    provider = SimpleNamespace(
        provider_id="openai-compatible",
        provider_type="openai",
        api_key="secret",
        api_key_env=None,
        embedding_base_url="https://example.test/v1/embeddings",
        base_url="https://example.test/v1",
        headers_json={"X-Tenant": "alpha"},
        enabled_models=[{"id": "embed-v1", "type": "embedding", "dimension": 1024}],
    )

    projected = project_embedding_model(provider, "embed-v1")

    assert projected == {
        "credential_data": {
            "type": "yuxi_openai_credential",
            "api_key": "secret",
            "name": "yuxi:openai-compatible",
            "base_url": "https://example.test/v1",
            "default_headers": {"X-Tenant": "alpha"},
        },
        "model": "embed-v1",
        "dimensions": 1024,
        "base_url": "https://example.test/v1/embeddings",
    }
