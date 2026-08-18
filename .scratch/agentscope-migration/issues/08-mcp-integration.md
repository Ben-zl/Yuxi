# 08 — MCP 接入

**What to build:** yuxi 的 MCP 服务器配置经配置投影（03）构造 agentscope 的 MCP 客户端：管理界面配置一个 MCP 服务器后，对话中智能体可调用其工具，工具事件在前端正常呈现。安全约束保留：普通用户仅可添加 sse/streamable-http 传输，stdio 仅限内置白名单。

**Blocked by:** 07 — 工具纵切：内置工具 + 知识库工具

**Status:** resolved

- [x] 管理界面配置 MCP server→对话中端到端调用其工具
- [x] 工具名冲突/禁用工具过滤语义与现有等价
- [x] 用户仅能添加 sse/streamable-http；stdio 仅装配代码注册表中的内置白名单
- [x] MCP 工具调用事件在前端呈现与迁移前一致

## Answer（2026-08-18 最终实现）

- `extra_agent_tools` 每轮从统一投影装配 MCP，不再向 workspace gateway 绑定配置。HTTP MCP 在 AgentScope 服务进程以无状态客户端执行，workspace 不获得 URL、Header、凭据或 `.mcp`，也不加入业务网络。
- 内置 stdio MCP 只接受代码注册表定义。装配阶段短连接发现 schema，返回的工具每次调用独立启动和关闭进程，避免 AnyIO client 跨请求、跨 task 关闭异常；数据库中的用户 stdio 继续拒绝。
- 配置以数据库为事实源，cache key 包含完整配置摘要；禁用列表按 MCP 远端原始工具名过滤，配置更新、删除、禁用工具与不可达错误在下一轮生效。
- 真实 E2E `test_agentscope_mcp_e2e.py` 3 passed：HTTP echo 调用和事件流、配置热更新/禁用/删除、stdio 与禁用边界均通过；内置 chart MCP 实际发现 27 个工具并成功调用 `generate_pie_chart`。
