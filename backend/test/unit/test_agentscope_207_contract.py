"""Yuxi 依赖的 AgentScope 2.0.7 私有兼容契约。"""

import inspect

from agentscope.app._tool import AgentCreate


def test_agent_create_constructor_and_input_schema_contract():
    """升级时显式门禁 Yuxi 唯一保留的私有 AgentCreate 依赖。"""
    parameters = list(inspect.signature(AgentCreate.__init__).parameters)
    assert parameters == [
        "self",
        "storage",
        "message_bus",
        "workspace_manager",
        "user_id",
        "session_id",
        "agent_id",
        "sub_agent_templates",
    ]
    schema = AgentCreate.input_schema
    assert set(schema["properties"]) == {"name", "description", "prompt"}
    assert set(schema["required"]) == {"name", "description", "prompt"}
