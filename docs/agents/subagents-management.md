# 子智能体与 Team

Yuxi 的多智能体执行使用 AgentScope 原生 Team 工具。旧版 `task`、`subagent_start`、`subagent_status`、`subagent_cancel`、`subagent_await` 和 child LangGraph checkpoint 已删除，不应再作为运行接口使用。

## 1. AgentScope Team

主智能体可通过以下原生工具组织协作：

| 工具 | 作用 |
| --- | --- |
| `TeamCreate` | 创建团队 |
| `AgentCreate` | 按可用模板创建 Team worker |
| `TeamInvite` | 邀请成员加入团队 |
| `TeamSay` | 向指定成员或团队派发消息 |

Team worker 使用独立的 AgentScope agent/session。成员回报会进入 leader 会话；gateway 在检测到 Team 工具后保留静默收束窗口，把异步回报驱动的续写聚合进同一个 Yuxi AgentRun。取消仍通过 AgentScope session interrupt 生效。

## 2. 管理面子智能体

Yuxi 仍使用 `agents` 表管理子智能体定义：

| 字段 | 说明 |
| --- | --- |
| `backend_id` | 子智能体使用 `SubAgentBackend` |
| `is_subagent` | 子智能体标记 |
| `config_json.context` | 模型、提示词、工具、知识库、MCP 与 Skills 配置 |
| `share_config` | 可见性和管理权限 |

子智能体沿用 `/api/agent` CRUD。普通列表默认只返回可聊天 Agent，管理页可通过 `include_subagents=true` 读取完整列表。后端会校验 `backend_id` 与 `is_subagent` 一致，子智能体不能设为默认聊天 Agent。

`ChatBotContext.subagents` 是主 Agent 的允许列表。配置投影会按当前用户权限校验这个列表，不可见、删除或错误类型的引用会显式失败。

## 3. 动态模板装配

AgentScope `create_app()` 的静态 `custom_subagent_templates` 不用于 Yuxi 管理面模板。每轮执行时，Yuxi 通过 `extra_agent_tools` 消费当前线程的统一配置投影，生成同名 `AgentCreate` 覆盖内建实例。其 schema 只包含当前用户、主 Agent 与 session 可访问的模板；管理端修改、删除或权限变化从下一轮立即生效，无需重启服务。Yuxi 会在用户配置的系统提示词后追加最小 Team worker 协议，要求 worker 完成后必须通过 `TeamSay` 向 leader 回报，避免自定义提示词覆盖 AgentScope 默认协作约束后让 Team 永久等待。

AgentScope 动态创建的 Team worker 没有独立的 Yuxi Thread 映射。运行时会先校验 worker 的 Team 关系，再通过 leader session 反查主线程投影，使 worker 继承相同的资源边界；孤立、伪造或跨用户 session 显式失败。

## 4. 文件和安全边界

每个 Team worker 的状态、消息和 workspace 生命周期由 AgentScope 管理。不要假设旧版“父线程 uploads/outputs 自动挂载给 child checkpoint”的语义仍存在；需要共享文件时，应通过明确的 workspace 工具或 Team 消息传递受控路径/内容。

当前模板接入满足：

- 模板集合按用户、主 Agent 和 session 隔离。
- `AgentCreate` 只能选择当前允许集合中的类型。
- 删除、禁用或失去权限的模板明确拒绝，不静默回退到通用模板。
- 模板、模型凭据、HTTP MCP Header 不进入 Docker workspace。
