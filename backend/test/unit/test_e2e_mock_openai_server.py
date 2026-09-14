"""E2E OpenAI mock 状态机的回归测试。"""

from test.e2e.mock_openai_server import _matched_tool_trigger


def _tool_call(name: str, arguments: str = "{}") -> dict:
    """构造 OpenAI assistant 工具调用消息。"""

    return {
        "role": "assistant",
        "tool_calls": [
            {
                "id": f"call-{name}",
                "type": "function",
                "function": {"name": name, "arguments": arguments},
            }
        ],
    }


def _subagent_parent_query(project_root: str) -> str:
    """构造要求 leader 先准备共享输入的子智能体任务。"""

    return (
        "请严格依次完成："
        "1）你先用 execute 执行 `printf '%s' 'runtime-shared-test' > '/tmp/yuxi-execution-tree-test'`；"
        f"2）用 write_file 创建 {project_root}/outputs/parent-input.txt，"
        "内容只有一行“由这个子智能体创建”；"
        "3）通过 task 调用子智能体；"
        f"4）task 返回后用 read_file 读取 {project_root}/outputs/subagents.txt；"
        f"5）最后调用 present_artifacts 展示 {project_root}/outputs/subagents.txt。"
    )


def test_failed_mcp_echo_uses_registered_resource_scoped_tool_name() -> None:
    """失败回声必须调用请求中实际注册的 resource_id 工具名。"""

    tool_name = "mcp__e2e-echo-mcp-resource__echo"
    body = {
        "messages": [{"role": "user", "content": "请调用失败回声"}],
        "tools": [
            {
                "type": "function",
                "function": {"name": tool_name, "parameters": {"type": "object"}},
            }
        ],
    }

    assert _matched_tool_trigger(body) == (
        tool_name,
        {"text": "raise-sensitive"},
    )


def test_subagent_leader_prepares_runtime_marker_before_creating_team() -> None:
    """leader 首个工具必须创建运行时标记，不能直接跳到 TeamCreate。"""

    body = {
        "messages": [
            {
                "role": "user",
                "content": _subagent_parent_query("/workspace"),
            }
        ],
        "tools": [
            {"type": "function", "function": {"name": "Bash", "parameters": {"type": "object"}}},
            {"type": "function", "function": {"name": "Write", "parameters": {"type": "object"}}},
            {"type": "function", "function": {"name": "TeamCreate", "parameters": {"type": "object"}}},
            {"type": "function", "function": {"name": "AgentCreate", "parameters": {"type": "object"}}},
        ],
    }

    assert _matched_tool_trigger(body) == (
        "Bash",
        {"command": "printf '%s' 'runtime-shared-test' > '/tmp/yuxi-execution-tree-test'"},
    )


def test_subagent_leader_writes_parent_input_before_creating_team() -> None:
    """运行时标记完成后必须写父输入文件，再进入 TeamCreate。"""

    project_root = "/workspace"
    body = {
        "messages": [
            {"role": "user", "content": _subagent_parent_query(project_root)},
            _tool_call("Bash"),
            {"role": "tool", "tool_call_id": "call-Bash", "content": "Command completed"},
        ],
        "tools": [
            {"type": "function", "function": {"name": "Bash", "parameters": {"type": "object"}}},
            {"type": "function", "function": {"name": "Write", "parameters": {"type": "object"}}},
            {"type": "function", "function": {"name": "TeamCreate", "parameters": {"type": "object"}}},
            {"type": "function", "function": {"name": "AgentCreate", "parameters": {"type": "object"}}},
        ],
    }

    assert _matched_tool_trigger(body) == (
        "Write",
        {
            "file_path": f"{project_root}/outputs/parent-input.txt",
            "content": "由这个子智能体创建\n",
        },
    )


def test_subagent_worker_reports_to_leader_after_finishing_file_operations() -> None:
    """文件操作完成后必须进入 TeamSay，不能提前结束 worker 回合。"""

    project_root = "/home/gem/user-data/projects/test-project"
    body = {
        "messages": [
            {
                "role": "user",
                "content": (
                    '<team-message from="leader">\n'
                    "先用 execute 执行 `cat '/tmp/yuxi-execution-tree-test'`，确认内容后，"
                    f"再用 read_file 读取 {project_root}/outputs/parent-input.txt，"
                    f"并用 write_file 将完全相同的内容写入 {project_root}/outputs/subagents.txt。"
                    "写入内容必须是“由这个子智能体创建”。\n"
                    "</team-message>"
                ),
            },
            _tool_call("Bash"),
            {"role": "tool", "tool_call_id": "call-Bash", "content": "runtime-shared-test"},
            _tool_call("Read"),
            {"role": "tool", "tool_call_id": "call-Read", "content": "由这个子智能体创建"},
            _tool_call("Write"),
            {"role": "tool", "tool_call_id": "call-Write", "content": "File written"},
        ],
        "tools": [
            {
                "type": "function",
                "function": {"name": "TeamSay", "parameters": {"type": "object"}},
            }
        ],
    }

    trigger = _matched_tool_trigger(body)

    assert trigger is not None
    assert trigger[0] == "TeamSay"
    assert trigger[1]["to"] is None


def test_subagent_leader_does_not_replay_worker_file_operations_after_report() -> None:
    """leader 收到 worker 回报后应结束 Team，不能把历史任务重新执行一遍。"""

    project_root = "/workspace"
    body = {
        "messages": [
            {
                "role": "user",
                "content": _subagent_parent_query(project_root),
            },
            _tool_call("TeamCreate"),
            {"role": "tool", "tool_call_id": "call-TeamCreate", "content": "Team created"},
            _tool_call("AgentCreate"),
            {"role": "tool", "tool_call_id": "call-AgentCreate", "content": "Worker created"},
            {
                "role": "user",
                "content": (
                    '<team-message from="worker-1">\n已完成运行时校验、父文件读取和子产物写入。\n</team-message>'
                ),
            },
        ],
        "tools": [
            {"type": "function", "function": {"name": "AgentCreate", "parameters": {"type": "object"}}},
            {"type": "function", "function": {"name": "TeamDelete", "parameters": {"type": "object"}}},
            {"type": "function", "function": {"name": "TeamSay", "parameters": {"type": "object"}}},
        ],
    }

    assert _matched_tool_trigger(body) == ("TeamDelete", {})


def test_subagent_leader_reads_worker_output_after_deleting_team() -> None:
    """Team 结束后 leader 必须读取 worker 产物。"""

    project_root = "/workspace"
    body = {
        "messages": [
            {"role": "user", "content": _subagent_parent_query(project_root)},
            _tool_call("Bash"),
            {"role": "tool", "tool_call_id": "call-Bash", "content": "Command completed"},
            _tool_call("Write"),
            {"role": "tool", "tool_call_id": "call-Write", "content": "File written"},
            _tool_call("TeamCreate"),
            {"role": "tool", "tool_call_id": "call-TeamCreate", "content": "Team created"},
            _tool_call("AgentCreate"),
            {"role": "tool", "tool_call_id": "call-AgentCreate", "content": "Worker created"},
            {
                "role": "user",
                "content": '<team-message from="worker-1">\n已完成文件写入。\n</team-message>',
            },
            _tool_call("TeamDelete"),
            {"role": "tool", "tool_call_id": "call-TeamDelete", "content": "Team deleted"},
        ],
        "tools": [
            {"type": "function", "function": {"name": "AgentCreate", "parameters": {"type": "object"}}},
            {"type": "function", "function": {"name": "Read", "parameters": {"type": "object"}}},
            {"type": "function", "function": {"name": "present_artifacts", "parameters": {"type": "object"}}},
        ],
    }

    assert _matched_tool_trigger(body) == (
        "Read",
        {"file_path": "/workspace/outputs/subagents.txt"},
    )


def test_subagent_leader_presents_worker_output_after_reading_it() -> None:
    """leader 回读成功后必须登记产物，再结束回答。"""

    body = {
        "messages": [
            {"role": "user", "content": _subagent_parent_query("/workspace")},
            _tool_call("Bash"),
            {"role": "tool", "tool_call_id": "call-Bash", "content": "Command completed"},
            _tool_call("Write"),
            {"role": "tool", "tool_call_id": "call-Write", "content": "File written"},
            _tool_call("TeamCreate"),
            {"role": "tool", "tool_call_id": "call-TeamCreate", "content": "Team created"},
            _tool_call("AgentCreate"),
            {"role": "tool", "tool_call_id": "call-AgentCreate", "content": "Worker created"},
            {
                "role": "user",
                "content": '<team-message from="worker-1">\n已完成文件写入。\n</team-message>',
            },
            _tool_call("TeamDelete"),
            {"role": "tool", "tool_call_id": "call-TeamDelete", "content": "Team deleted"},
            _tool_call("Read"),
            {"role": "tool", "tool_call_id": "call-Read", "content": "由这个子智能体创建"},
        ],
        "tools": [
            {"type": "function", "function": {"name": "AgentCreate", "parameters": {"type": "object"}}},
            {"type": "function", "function": {"name": "Read", "parameters": {"type": "object"}}},
            {"type": "function", "function": {"name": "present_artifacts", "parameters": {"type": "object"}}},
        ],
    }

    assert _matched_tool_trigger(body) == (
        "present_artifacts",
        {"filepaths": ["/workspace/outputs/subagents.txt"]},
    )
