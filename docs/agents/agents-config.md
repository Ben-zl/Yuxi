# 智能体配置

Yuxi 的智能体执行统一由 AgentScope 承担。`yuxi.agents` 现在是管理面：它定义可配置字段、资源选项和前端能力元数据；`yuxi.agentscope` 是执行适配层，负责把数据库配置投影为 AgentScope agent/session/model，并把 AgentScope 事件转换成 Yuxi 的 Run SSE。

## 1. 管理面对象

智能体配置由以下对象组成：

- `Agent`：`agents` 表中的实例，保存 slug、展示信息、共享权限、backend 和 `config_json.context`。
- `BaseAgent`：backend 的静态元数据，只描述名称、能力和 `context_schema`，不创建执行图。
- `BaseContext`：配置 Schema，也是管理端 `configurable_items` 的来源。
- `ChatbotAgent`：普通聊天智能体的管理面 backend，额外允许选择子智能体。
- `SubAgentBackend`：子智能体的管理面 backend，不在聊天页直接出现。

`BaseAgent.get_info()` 会根据当前用户加载可见的工具、知识库、MCP、Skills 和子智能体选项。前端根据返回的 `configurable_items` 渲染表单，保存值仍写回 `Agent.config_json.context`。

```text
context_schema
  -> BaseAgent.get_info()
  -> Agent detail API configurable_items
  -> AgentRuntimeConfigForm
  -> Agent.config_json.context
```

## 2. Context 字段

`BaseContext` 当前公开的主要字段如下：

| 字段 | 作用 |
| --- | --- |
| `system_prompt` | AgentScope agent 的系统提示词 |
| `model` | 模型配置，留空时使用系统默认模型 |
| `tool_approval_mode` | 敏感 workspace 工具的审批模式 |
| `tools` | 管理端内置工具选择 |
| `knowledges` | 当前 Agent 可使用的知识库范围 |
| `mcps` | 每轮在服务进程装配的 HTTP MCP |
| `skills` | 创建会话时安装到 workspace 的 Skills |
| `max_execution_steps` | AgentScope `react_config.max_iters`，默认 300 |
| `thread_id` / `uid` / `run_id` / `request_id` | 运行标识，不作为表单字段展示 |

`ChatBotContext` 增加 `subagents`。`SubAgentContext` 保留父线程等隐藏字段供管理接口识别，但旧 LangGraph 子任务执行体已经移除。

旧版 `summary_threshold`、`summary_keep_messages`、`summary_prompt`、`summary_tool_result_token_limit`、`summary_l2_trigger_ratio` 和 `model_retry_times` 已删除。AgentScope 当前不消费这些配置，继续展示会形成“能保存但不生效”的错误契约。

资源字段的空值语义需要区分：

- `tools`、`knowledges`、`mcps`、`skills` 为 `null` 时，管理端归一化为当前用户可访问的默认集合。
- 显式数组表示允许列表；不可见或已删除的 Skill/子智能体会在配置投影阶段显式失败。
- HTTP MCP 的实际启用状态、transport、URL、Header、超时和禁用工具以每轮读取的数据库记录为准。

## 3. 一次运行如何消费配置

### 3.1 创建或复用会话

`worker_job` 根据 AgentRun 恢复用户、线程、Agent 和请求级模型选择，然后调用 `ensure_thread_session()`：

1. 校验存量线程是否允许进入 AgentScope。
2. 通过 `project_runtime()` 校验用户、Agent、模型供应商和资源可见性。
3. 首次运行时创建 AgentScope credential、agent 和 session，并持久化 Thread↔Session 映射。
4. 将已授权 Skill 安装到该 session 的 workspace。
5. 后续运行复用映射；请求切换模型时更新 AgentScope session model 后再提交本地映射。

AgentScope agent 创建请求包含 `system_prompt` 和 `react_config.max_iters`。因此修改这类创建期配置后，已有 session 不会自动重建；新 session 会使用最新值。请求级模型选择是例外，已有 session 会被显式更新。

### 3.2 每轮动态装配工具

`server/agentscope_main.py` 注册 `extra_agent_tools`。每轮开始时，它使用 `(uid, agentscope_agent_id, agentscope_session_id)` 反查严格线程映射，再读取当前 Agent 的知识库和 MCP 配置。

- 知识库工具在 AgentScope 服务进程执行，并在调用时再次校验用户可见性。
- HTTP MCP 使用 AgentScope 无状态 `MCPClient` 在服务进程执行；URL、Header 和凭据不写入 Docker workspace。
- `disabled_tools`、MCP 更新和删除会在下一轮工具构建时生效；不可达 MCP 会显式使本轮失败。
- stdio MCP 不进入当前运行路径。
- `present_artifacts` 读取 AgentScope workspace 的 `/workspace/outputs`；OCR 和网页搜索只在对应服务或凭据已配置时装配。

### 3.3 附件、事件和状态

附件先由 Yuxi 校验归属、数量和大小，再上传到当前 AgentScope session 的 `/workspace/uploads`。模型只收到 workspace 路径，不会把整份文件作为长文本内联。

AgentScope 事件经 gateway 转换为既有 Run SSE；助手消息、Run 终态和 token usage 回写 Yuxi PostgreSQL。线程状态由 `ThreadStateService` 聚合 Conversation/Message、AgentRun、Redis 挂起事件和 workspace 产物，不读取旧 checkpoint。

## 4. 自定义 backend 和运行能力

如果只是增加表单字段，可以继承 `BaseContext`，并在字段 `metadata` 中给出名称、描述、类型、选项和权限。新增函数或类需要保持管理面含义明确；把字段加入 Schema 并不意味着 AgentScope 会自动消费它。

```python
from dataclasses import dataclass, field

from yuxi.agents import BaseAgent, BaseContext


@dataclass(kw_only=True)
class MyAgentContext(BaseContext):
    response_style: str = field(
        default="concise",
        metadata={
            "name": "回答风格",
            "description": "控制回答风格。",
            "options": ["concise", "detailed"],
        },
    )


class MyAgent(BaseAgent):
    """示例管理面 backend。"""

    name = "我的智能体"
    context_schema = MyAgentContext
```

要让 `response_style` 真正影响执行，还必须在 `yuxi.agentscope.projection`、`config_projection`、`extra_agent_tools` 或 AgentScope middleware 中建立明确映射。不要恢复独立 LangGraph graph，也不要在路由中直接拼运行逻辑。

## 5. `capabilities`

`capabilities` 只控制前端静态入口：

| capability | 说明 |
| --- | --- |
| `file_upload` | 显示上传入口 |
| `files` | 显示文件面板 |

运行态 `todos`、`artifacts`、`subagent_runs` 和 `token_usage` 由线程状态服务返回，不属于 capability。

## 6. 相关主题

- [工具系统](./tools-system.md)
- [AgentScope 中间件与运行扩展](./middleware.md)
- [沙盒架构与设计](./sandbox-architecture.md)
- [MCP 集成](./mcp-integration.md)
- [Skills 管理](./skills-management.md)
- [子智能体](./subagents-management.md)
- [Langfuse 集成](../advanced/langfuse-integration.md)
