# 08 — MCP 接入

**What to build:** yuxi 的 MCP 服务器配置经配置投影（03）构造 agentscope 的 MCP 客户端：管理界面配置一个 MCP 服务器后，对话中智能体可调用其工具，工具事件在前端正常呈现。安全约束保留：普通用户仅可添加 sse/streamable-http 传输，stdio 仅限内置白名单。

**Blocked by:** 07 — 工具纵切：内置工具 + 知识库工具

**Status:** resolved

- [x] 管理界面配置 MCP server→对话中端到端调用其工具
- [x] 工具名冲突/禁用工具过滤语义与现有等价
- [x] 用户仅能添加 sse/streamable-http，stdio 白名单约束保留
- [x] MCP 工具调用事件在前端呈现与迁移前一致

## Answer（2026-08-14 验证记录）

- 实现：`tools.py::bind_thread_mcps`——yuxi `mcp_servers` 行投影为 agentscope MCPClient（name=slug、is_stateful=False、HttpMCPConfig{url/headers/timeout}、disable_tools 携带禁用清单），经 `POST /workspace/mcp` 绑定到会话 workspace（runner 在会话创建期调用一次，409 重名视为已绑定）。工具命名 `mcp__{slug}__{tool}` 与事件呈现复用工单 07 的 ToolEventConverter。
- 约束保留：投影仅接受 `enabled=1` 且 `transport ∈ {sse, streamable_http}`（e2e guard 用例验证 stdio 与禁用行绑定数为 0）；stdio 用户禁用约束在 yuxi 源头服务不变（迁移不改管理面）。
- e2e（`test_agentscope_mcp_e2e.py` 2 passed，全量回归 **30 passed**）：mock MCP 服务器（FastMCP streamable-http，echo 工具）→ yuxi 行投影绑定（服务日志 201、MCP mock 收到 initialize/ListTools）→ 模型发起 `mcp__e2e-echo-mcp__echo` 调用 → 经 MCP 协议真实执行 → tool_call 增量与 tool-finished（含 echo 结果）chunk 落 Run 事件流；guard 用例断言 stdio/禁用不绑定。
- 环境事实（切换工单接线项）：MCP 连接发生在 **workspace 容器内的 gateway**（网络隔离设计）；workspace 容器在 docker 默认 bridge（无嵌入式 DNS），e2e 以 bridge IP 直连 mock；生产部署需 fork 给 DockerWorkspaceManager 增加网络参数（加入 app 网络）或经可达地址发布 MCP——记入切换门禁。
- MCP 工具默认触发审批 park（awaiting_permission，已两次实测）——审批交互链路在工单 10 验证，e2e 以 bypass 权限模式放行。
