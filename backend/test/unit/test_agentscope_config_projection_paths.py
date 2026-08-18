"""AgentScope 运行时提示词路径投影测试。"""

from yuxi.agentscope.config_projection import adapt_prompt_paths_for_agentscope


def test_adapt_prompt_paths_uses_persistent_agentscope_workspace() -> None:
    """AgentScope 不得把产出写入容器临时层。"""
    prompt = (
        "写入 /home/gem/user-data/outputs，读取 /home/gem/user-data/uploads，"
        "用户目录是 /home/gem/user-data/workspace。"
    )

    assert adapt_prompt_paths_for_agentscope(prompt) == (
        "写入 /workspace/outputs，读取 /workspace/uploads，用户目录是 /workspace/workspace。"
    )
