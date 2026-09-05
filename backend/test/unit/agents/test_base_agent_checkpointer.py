from yuxi.agents.base import BaseAgent


def test_base_agent_has_no_legacy_langgraph_checkpointer_surface() -> None:
    """管理面 Agent 不应重新暴露已由 AgentScope 接管的 checkpoint 入口。"""
    assert not hasattr(BaseAgent, "checkpointer")
    assert not hasattr(BaseAgent, "_get_checkpointer")
