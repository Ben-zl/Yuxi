"""AgentScope 运行时提示词路径投影测试。"""

from yuxi.agentscope.config_projection import (
    adapt_prompt_paths_for_agentscope,
    build_agentscope_system_prompt,
)


def test_adapt_prompt_paths_uses_persistent_agentscope_workspace() -> None:
    """AgentScope 不得把产出写入容器临时层。"""
    prompt = (
        "写入 /home/gem/user-data/outputs，读取 /home/gem/user-data/uploads，用户目录是 /home/gem/user-data/workspace。"
    )

    assert adapt_prompt_paths_for_agentscope(prompt) == (
        "写入 /workspace/outputs，读取 /workspace/uploads，用户目录是 /workspace/workspace。"
    )


def test_build_agentscope_system_prompt_preserves_custom_prompt_and_file_contract() -> None:
    """主、子智能体必须共享 main 分支定义的文件生命周期约束。"""
    prompt = build_agentscope_system_prompt("执行专项性能分析")

    assert "执行专项性能分析" in prompt
    assert "/workspace/outputs/tmp/：用于存放中间结果或备份内容" in prompt
    assert "必须在 `TeamSay` 中回报文件的完整绝对路径和关键标识" in prompt
    assert "主智能体必须在后续 `AgentCreate` 的 prompt 中原样传入" in prompt
    assert "/workspace/outputs/tmp 中与当前任务上下文匹配的中间文件" in prompt
    assert "读取候选文件并提取所需标识" in prompt
    assert "/workspace/uploads" in prompt
    assert "/workspace/workspace/..." in prompt
    shared_tools = ("`shared_workspace_list`", "`shared_workspace_read`", "`shared_workspace_write`")
    assert all(name in prompt for name in shared_tools)
    assert "不能用 AgentScope 原生 `Read`、`Write`、`Edit`" in prompt
    assert "确认前不得向用户宣称写入成功" in prompt
    assert "/home/gem/user-data" not in prompt
