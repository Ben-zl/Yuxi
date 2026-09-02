from yuxi.agents.buildin.chatbot.prompt import PROMPT


def test_chatbot_prompt_does_not_duplicate_html_preview_skill_instructions():
    assert "html:preview" not in PROMPT


def test_chatbot_prompt_uses_current_platform_name():
    assert '你是一个交互式智能体“Agent智能体平台”。' in PROMPT


def test_chatbot_prompt_requires_registering_final_artifacts():
    assert "必须调用 `present_artifacts`" in PROMPT
    assert "仅写入 /home/gem/user-data/outputs 不等于完成交付" in PROMPT
    assert "中间 JSON、临时脚本、缓存、调试文件" in PROMPT
    assert "子智能体生成的文件由主智能体统一判断" in PROMPT


def test_chatbot_prompt_requires_team_intermediate_file_handoff():
    """顺序子任务必须显式交接共享中间文件，不能只传摘要。"""
    assert "必须在 `TeamSay` 中回报文件的完整绝对路径和关键标识" in PROMPT
    assert "AgentCreate" in PROMPT
    assert "/home/gem/user-data/outputs/tmp" in PROMPT
    assert "判定前置数据缺失前" in PROMPT
    assert "读取候选文件并提取所需标识" in PROMPT
