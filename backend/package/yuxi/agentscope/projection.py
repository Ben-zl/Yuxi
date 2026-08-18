"""yuxi 配置 → agentscope 运行时对象的投影（纯函数集合）。

模型供应商投影为 credential 数据与 ChatModelConfig；Agent 行投影为
agent 创建请求与子智能体模板载荷。统一入口见 config_projection.py，
本模块只做无副作用的形状转换。"""

import os

from yuxi.storage.postgres.models_business import Agent, ModelProvider

# yuxi provider_type → agentscope credential 判别值
_CREDENTIAL_TYPE_BY_PROVIDER = {
    "openai": "yuxi_openai_credential",
    "anthropic": "yuxi_anthropic_credential",
    "gemini": "yuxi_gemini_credential",
}


def is_lite_mode() -> bool:
    """是否处于 LITE 模式（无知识库/图谱依赖的轻量部署）。"""
    return os.getenv("LITE_MODE", "false").lower() in {"true", "1"}


def agent_context(agent: Agent) -> dict:
    """读取 Agent 行的运行时上下文配置（BaseContext 字段的 JSON 形态）。"""
    return (agent.config_json or {}).get("context") or {}


# yuxi 旧栈 recursion_limit 默认值（BaseContext.max_execution_steps）
DEFAULT_MAX_EXECUTION_STEPS = 300


def project_agent_request(agent: Agent) -> dict:
    """投影为 agentscope 的 agent 创建请求（含 ReAct 迭代上限对齐）。"""
    context = agent_context(agent)
    max_steps = int(context.get("max_execution_steps") or DEFAULT_MAX_EXECUTION_STEPS)
    trigger_ratio = float(context.get("summary_trigger_ratio", 0.8))
    reserve_ratio = float(context.get("summary_reserve_ratio", 0.1))
    tool_result_limit = int(context.get("summary_tool_result_token_limit", 50000))
    if not 0 < trigger_ratio < 0.9:
        raise ValueError("上下文压缩触发比例必须大于 0 且小于 0.9")
    if not 0 < reserve_ratio < trigger_ratio:
        raise ValueError("压缩后保留比例必须大于 0 且小于触发比例")
    if tool_result_limit <= 0:
        raise ValueError("工具结果 Token 上限必须大于 0")
    context_config = {
        "trigger_ratio": trigger_ratio,
        "reserve_ratio": reserve_ratio,
        "tool_result_limit": tool_result_limit,
    }
    if str(context.get("summary_prompt") or "").strip():
        context_config["compression_prompt"] = str(context["summary_prompt"])
    return {
        "name": agent.name,
        "system_prompt": context.get("system_prompt") or "You are a helpful assistant.",
        "react_config": {"max_iters": max_steps},
        "context_config": context_config,
    }


def project_subagent_template(agent: Agent) -> dict:
    """投影管理员配置的子智能体为 Team worker 模板载荷（装配在 Team 工单）。"""
    context = agent_context(agent)
    return {
        "type": agent.slug,
        "description": agent.description or agent.name,
        "system_prompt_template": context.get("system_prompt") or "You are a helpful assistant.",
    }


def _resolve_api_key(provider: ModelProvider) -> str:
    """解析供应商 API Key：优先直接配置，其次环境变量；缺失即显式失败。"""
    if provider.api_key:
        return provider.api_key
    if provider.api_key_env and os.getenv(provider.api_key_env):
        return os.getenv(provider.api_key_env)
    raise ValueError(
        f"模型供应商 {provider.provider_id} 未配置 API Key"
        f"（api_key 为空且环境变量 {provider.api_key_env or '<未指定>'} 未设置）"
    )


def project_chat_model(provider: ModelProvider, model_id: str) -> tuple[dict, dict]:
    """投影为 (credential_data, chat_model_config)。

    credential_data 的 type 是 agentscope credential 联合的判别键；
    chat_model_config 的 credential_id 由调用方在创建凭据后回填。
    """
    credential_type = _CREDENTIAL_TYPE_BY_PROVIDER.get(provider.provider_type)
    if credential_type is None:
        raise ValueError(f"模型供应商 {provider.provider_id} 的类型 {provider.provider_type} 暂不支持投影到 agentscope")

    model = next(
        (item for item in provider.enabled_models or [] if item.get("id") == model_id),
        None,
    )
    if model is None or model.get("type", "chat") != "chat":
        raise ValueError(f"模型供应商 {provider.provider_id} 未启用聊天模型 {model_id}")

    credential_data = {
        "type": credential_type,
        "api_key": _resolve_api_key(provider),
        "name": f"yuxi:{provider.provider_id}",
    }
    base_url = model.get("base_url_override") or provider.base_url
    if base_url:
        credential_data["base_url"] = base_url
    if provider.headers_json:
        credential_data["default_headers"] = dict(provider.headers_json)
    if model.get("context_length"):
        credential_data["context_size"] = int(model["context_length"])

    provider_extra = dict(provider.extra_json or {})
    parameters = dict(provider_extra.pop("parameters", {}) or {})
    request_overrides = dict(model.get("request_body_overrides") or {})
    if credential_type == "yuxi_anthropic_credential":
        aliases = {
            "enable_thinking": "thinking_enable",
            "thinking_budget": "thinking_budget",
            "reasoning_effort": "reasoning_effort",
        }
        unsupported = [key for key in request_overrides if key not in aliases]
        if unsupported:
            raise ValueError(
                f"Anthropic 模型请求体覆盖包含不支持字段: {', '.join(unsupported)}"
            )
        for key, value in request_overrides.items():
            parameters[aliases[key]] = value
        request_overrides = {}
    elif request_overrides and credential_type != "yuxi_openai_credential":
        raise ValueError("只有 OpenAI-compatible 模型支持通用请求体覆盖")

    chat_model_config = {
        "type": credential_type,
        "credential_id": None,
        "model": model_id,
        "parameters": parameters,
    }
    if request_overrides:
        credential_data["request_body_overrides"] = request_overrides
    return credential_data, chat_model_config


def permission_mode_for(tool_approval_mode: str | None) -> str:
    """yuxi 审批模式 → agentscope 会话权限模式。

    always_trust（完全信任）映射 bypass（跳过全部人工确认），
    其余（含 None）按 default（逐次审批）。
    """
    if tool_approval_mode == "always_trust":
        return "bypass"
    return "default"


def split_model_spec(model_spec: str) -> tuple[str, str]:
    """拆分 provider_id:model_id（model_id 允许包含斜杠）。"""
    provider_id, _, model_id = model_spec.partition(":")
    if not provider_id or not model_id:
        raise ValueError(f"模型标识格式应为 provider_id:model_id，收到：{model_spec}")
    return provider_id, model_id
