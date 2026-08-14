# 08 — MCP 接入

**What to build:** yuxi 的 MCP 服务器配置经配置投影（03）构造 agentscope 的 MCP 客户端：管理界面配置一个 MCP 服务器后，对话中智能体可调用其工具，工具事件在前端正常呈现。安全约束保留：普通用户仅可添加 sse/streamable-http 传输，stdio 仅限内置白名单。

**Blocked by:** 07 — 工具纵切：内置工具 + 知识库工具

**Status:** ready-for-agent

- [ ] 管理界面配置 MCP server→对话中端到端调用其工具
- [ ] 工具名冲突/禁用工具过滤语义与现有等价
- [ ] 用户仅能添加 sse/streamable-http，stdio 白名单约束保留
- [ ] MCP 工具调用事件在前端呈现与迁移前一致
