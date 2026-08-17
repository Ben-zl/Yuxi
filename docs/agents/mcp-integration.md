# MCP 集成

MCP（Model Context Protocol）用于把外部工具接入智能体。Yuxi 当前的 AgentScope 运行路径支持 SSE 和 Streamable HTTP MCP；MCP 客户端在 AgentScope 服务进程执行，不在 Docker workspace 中执行。

## 支持矩阵

| transport | 管理接口 | AgentScope 运行时 | 说明 |
| --- | --- | --- | --- |
| `streamable_http` | 支持 | 支持 | 推荐 |
| `sse` | 支持 | 支持 | 兼容已有 HTTP MCP |
| `stdio` | 拒绝用户新增 | 不装配 | 包括历史记录和代码内置记录 |

当前实现不会在 API、worker 或 workspace 中启动 stdio MCP 进程。已有 stdio 记录可以保留用于审计或迁移，但不会向模型暴露工具。需要继续使用的 stdio MCP 应先部署为受控 HTTP 服务，再以 SSE 或 Streamable HTTP 配置。

## 配置示例

```json
{
  "name": "custom-remote-mcp",
  "transport": "streamable_http",
  "url": "https://example.com/mcp",
  "headers": {
    "Authorization": "Bearer <secret>"
  },
  "timeout": 30,
  "disabled_tools": []
}
```

密钥只保存在服务端配置中。管理接口返回时应遵循现有脱敏规则，不要把真实 Header 写入日志、测试夹具或 Agent prompt。

## 运行链路

`server/agentscope_main.py` 的 `extra_agent_tools` 在每轮开始时：

1. 使用 `(uid, agentscope_agent_id, agentscope_session_id)` 反查严格 Thread↔Session 映射。
2. 读取当前 Agent 的 `config_json.context.mcps`。
3. 从 PostgreSQL 加载对应 MCP 记录，只接受已启用、URL 非空的 SSE/Streamable HTTP 服务。
4. 为每个服务创建 `is_stateful=False` 的 AgentScope `MCPClient`。
5. 调用 `list_tools()`，应用 `disabled_tools`，把剩余工具交给当前 Agent。

客户端不会跨轮缓存，因此服务器配置、禁用工具和删除会在下一轮生效。端点不可达或握手失败会显式使本轮工具装配失败，不静默忽略。

## Workspace 安全边界

MCP 连接发生在 AgentScope 服务进程：

```text
workspace 中的模型工具调用
  -> AgentScope service MCPClient
  -> HTTP MCP endpoint
```

Docker workspace：

- 不加入 Yuxi `app-network`；
- 不获得 MCP URL、Header 或凭据；
- 不生成 `/workspace/.mcp`；
- 不直接访问 PostgreSQL、Redis、API 或其他 workspace。

如果 MCP 需要访问 Yuxi 内部服务，应让 MCP endpoint 部署在 AgentScope 服务可达的位置，并在 MCP 自身完成最小权限控制。不要把 workspace 接入业务网络。

## Agent 配置与启用状态

MCP 管理页的“添加/移除”控制记录的 `enabled` 状态。Agent 的 `context.mcps` 决定该 Agent 允许装配哪些 MCP：

- 空列表表示不装配 MCP；
- 显式列表只装配其中仍存在且已启用的 HTTP MCP；
- 被删除或禁用的记录不会产生工具；
- `disabled_tools` 可进一步屏蔽单个工具。

## 验证

配置保存成功不是运行验收。至少验证：

1. AgentScope 服务可以真实 `list_tools`。
2. 模型发起工具调用后，MCP endpoint 收到 `call_tool`，结果进入 Run SSE。
3. `disabled_tools` 修改后下一轮消失。
4. URL 改为不可达地址时本轮显式失败。
5. 删除或禁用 MCP 后下一轮不再暴露。
6. workspace 容器元数据不包含 MCP URL/Header，且没有 `.mcp` 目录。
