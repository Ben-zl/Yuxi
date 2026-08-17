# AgentScope 中间件与运行扩展

Yuxi 已不再维护 LangChain/DeepAgents middleware 链。智能体执行由 AgentScope 承担，运行扩展分为三个边界：AgentScope middleware、逐轮工具 provider，以及 Yuxi worker/gateway。

## 1. 三类扩展边界

| 边界 | 当前职责 | 入口 |
| --- | --- | --- |
| AgentScope middleware | 模型、回复和工具 span 观测 | `extra_agent_middlewares` |
| 逐轮工具 provider | KB、HTTP MCP、网页搜索、OCR、产物工具 | `extra_agent_tools` |
| Yuxi worker/gateway | 队列、取消、审批恢复、事件转换、消息与终态落库 | `yuxi.agentscope.worker_job` / `gateway` |

当前 `extra_agent_middlewares` 每轮返回 AgentScope `TracingMiddleware`。配置 OpenTelemetry exporter 后，AgentScope 的 agent/model/tool span 会通过 OTLP 上报；未配置 exporter 时不会阻断聊天。

工具装配不是 middleware。`extra_agent_tools(uid, agent_id, session_id)` 每轮依据严格 Thread↔Session 映射读取当前配置，并返回 AgentScope `FunctionTool` 或 MCP tools。这样资源变更不需要重启服务，也不会依赖进程级快照。

## 2. HTTP MCP 的执行边界

HTTP MCP 复用 `extra_agent_tools`，由 AgentScope 服务进程中的无状态 `MCPClient` 执行：

```text
Docker workspace --tool call--> AgentScope service --HTTP MCP--> configured endpoint
```

workspace 不连接 Yuxi `app-network`，也不会获得 MCP URL、Header、凭据或 `.mcp` 配置。服务端只加载已启用的 SSE/Streamable HTTP 记录，并应用 timeout 和 `disabled_tools`。stdio 记录和已禁用记录不装配。

这条边界适合 HTTP MCP，因为调用不依赖 workspace 内的命令或文件。需要处理 workspace 文件的能力应实现为受控 AgentScope 工具，由服务端校验路径后访问 workspace API，不应把内部业务网络暴露给 workspace。

## 3. 知识库、Skills 与附件

- 知识库工具在服务进程执行，实际查询范围是用户可见集合与 Agent 配置集合的交集；知识库不挂载到 workspace。
- Skills 在创建 AgentScope session 时从已授权源目录安装到该 session workspace，由 AgentScope 的 SkillViewer 渐进披露。
- 附件由 worker 校验后上传到 `/workspace/uploads`，并把路径追加到本轮用户消息。
- `present_artifacts` 通过 AgentScope workspace API 列出 `/workspace/outputs`。

旧版 `SkillsMiddleware`、`AttachmentMiddleware`、`YuxiSubAgentMiddleware`、`YuxiSummarizationMiddleware`、`TokenUsageMiddleware` 和 LangChain 模型兼容 middleware 已删除。对应状态不得再从 checkpoint 读取。

## 4. 审批、取消和状态

文件写入与命令执行由 AgentScope permission 机制挂起。gateway 把 `REQUIRE_USER_CONFIRM` 转成前端既有审批事件，Redis 暂存待确认事件；resume 时 worker 读取当前 session 中仍处于 `asking` 的工具调用并提交逐项决定。

取消和 steer 由 Yuxi worker 调用 AgentScope session interrupt。AgentScope 事件被转换成 Run SSE，最终文本、token usage 和终态写入 Yuxi 数据库。`ThreadStateService` 从这些事实源构建 `/state`，而不是维护另一套中间件 state。

## 5. 如何增加运行能力

新增能力前先选择最小边界：

- 只需新增一个业务工具：在 `yuxi.agentscope.tools` 返回 AgentScope `FunctionTool`，并在执行时完成权限校验。
- 需要每轮按用户或 session 变化：通过 `extra_agent_tools` 或 `extra_agent_middlewares` 加载，避免启动期全局快照。
- 需要改变模型/回复/工具生命周期：使用 AgentScope `MiddlewareBase`，并在 `extra_agent_middlewares` 注册。
- 需要改变排队、持久化、SSE 或恢复语义：修改 Yuxi worker/gateway，不放进 AgentScope middleware。

新增配置字段时必须同时实现运行时消费和回归测试。仅让管理端可以保存字段、但执行层忽略它，属于无效配置。
