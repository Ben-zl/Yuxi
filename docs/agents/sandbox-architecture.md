# AgentScope Workspace 隔离架构

Yuxi 的对话智能体使用 AgentScope workspace 执行文件和命令工具。workspace 的目标是把模型可执行代码与 Yuxi 业务网络、数据库和凭据隔离，同时为同一会话提供可复用的 `/workspace` 文件空间。

仓库中的 `sandbox-provisioner` 仍然存在，但它不再承载对话 Agent 的工具执行。它只服务远程 Skill 安装等一次性管理任务。排查问题时必须先区分这两条链路。

## 1. 当前运行拓扑

默认开发环境设置：

```env
AGENTSCOPE_WORKSPACE_BACKEND=docker
AGENTSCOPE_WORKSPACE_BASEDIR=<宿主机可被 Docker daemon 访问的绝对路径>
```

`server/agentscope_main.py` 创建 `DockerWorkspaceManager`，隔离策略为 `PER_SESSION`。Yuxi Thread 与 AgentScope Session 一一映射，因此实际隔离单位也是对话线程；同一线程跨轮复用 workspace，不同 session 使用不同 workspace/container。

```text
Web/API
  -> ARQ worker
  -> AgentScope service
       -> per-session Docker workspace: 文件、命令、Skills
       -> service-process tools: KB、HTTP MCP、Web Search、OCR
```

AgentScope 服务容器需要 Docker socket，以便动态创建 workspace 容器。`AGENTSCOPE_WORKSPACE_BASEDIR` 必须使用 Docker daemon 视角下的宿主机路径；Compose 将同一路径挂载给 worker/AgentScope，避免 Docker Desktop 下容器路径无法再次 bind mount。

本地调试可使用 `LocalWorkspaceManager`，但它没有 Docker 容器隔离，不是默认生产边界。

## 2. 网络隔离

workspace 容器不加入 Yuxi 的 `app-network`，因此不能直接解析或访问 PostgreSQL、Redis、API、MinIO、Neo4j 及其他业务服务，也不能访问其他 workspace。AgentScope 服务本身位于 `app-network`，负责执行需要业务访问的受控工具。

这条边界的核心规则是：

- workspace 只获得执行命令和读写本 session 文件所需的环境。
- 模型供应商凭据、MCP URL/Header 和 Yuxi 数据库凭据不注入 workspace。
- 不通过“把 workspace 接入业务网络”解决内部服务可达性。
- 需要访问 Yuxi 资源的能力必须由服务进程工具校验身份和资源范围。

## 3. HTTP MCP 为什么不在 workspace 执行

HTTP MCP 通过 AgentScope `extra_agent_tools` 在服务进程逐轮装配。`build_mcp_tools()` 从 Yuxi PostgreSQL 读取当前 Agent 配置的 MCP 记录，为已启用的 SSE/Streamable HTTP 服务创建无状态 `MCPClient`，调用 `list_tools()` 后把工具对象交给当前 Agent。

```text
模型发起 MCP tool call
  -> AgentScope 服务进程中的 MCPClient
  -> 配置的 HTTP MCP endpoint
  -> 工具结果进入 AgentScope 事件流
```

因此 workspace 中不会出现：

- MCP 的真实 URL 或 Header；
- `.mcp` 配置目录；
- 内部 MCP 凭据；
- 为访问 MCP 而加入的业务网络。

MCP 的 `disabled_tools`、URL、Header、timeout、启用状态和删除会在下一轮工具装配时重新读取。不可达 MCP 会显式使工具装配失败，不留下 workspace 绑定。stdio MCP 不进入当前运行路径。

## 4. 文件语义

AgentScope 对模型暴露 `/workspace`。Yuxi 约定：

| 路径 | 用途 |
| --- | --- |
| `/workspace/uploads` | 当前请求上传并授权给 Agent 的附件 |
| `/workspace/outputs` | Agent 生成、可由 `present_artifacts` 展示的交付物 |
| workspace 中的 Skill 目录 | 当前 session 已安装的授权 Skills，由 AgentScope 管理 |

附件链路由 Yuxi worker 控制：先校验附件属于当前用户/对话、单次不超过 10 个，再通过 AgentScope 服务扩展端点写入 `/workspace/uploads`。扩展端点只接受该目录的直接子文件，并限制单文件 25 MB；路径越界会返回 422。

知识库不挂载为文件树。模型通过 `list_kbs`、`query_kb`、`open_kb_document`、`find_kb_document` 和 `download_kb_file` 等服务端工具访问授权内容。

## 5. 工具审批

读文件、列目录等只读工具可直接执行。写文件、编辑文件和执行命令由 AgentScope permission 机制按会话模式决定是否挂起：

- 默认审批：产生 `REQUIRE_USER_CONFIRM`，Yuxi 转换成前端审批卡；
- 完全信任：按会话 permission mode 自动执行；
- 拒绝：resume 把逐项决定送回 AgentScope，未批准工具不执行。

审批信息保存在 AgentScope session 与 Yuxi Redis 挂起记录中，不依赖 workspace 内状态。

## 6. 与 sandbox-provisioner 的边界

`sandbox-provisioner` 的 `SANDBOX_*`、`PROVISIONER_BACKEND`、`DOCKER_*` 和 `K8S_*` 配置只影响仍使用 `ProvisionerSandboxBackend` 的管理任务，当前主要是远程 Skill 安装。它不会改变 AgentScope 对话 workspace 的 backend。

| 场景 | 执行器 | 关键配置 |
| --- | --- | --- |
| 对话 Agent 文件/命令工具 | AgentScope workspace manager | `AGENTSCOPE_WORKSPACE_BACKEND`、`AGENTSCOPE_WORKSPACE_BASEDIR` |
| HTTP MCP/KB/Web Search/OCR | AgentScope 服务进程 | Yuxi 数据库和对应服务配置 |
| 远程 Skill 安装 | sandbox-provisioner | `SANDBOX_PROVIDER`、`SANDBOX_PROVISIONER_*` |

不要用 `sandbox-provisioner` 的健康状态推断 AgentScope workspace 正常，也不要通过修改 `SANDBOX_PROVISIONER_BACKEND` 尝试切换对话 Agent workspace。

## 7. 验证与排障

开发环境先检查：

```bash
docker ps
docker logs agentscope-dev --tail 200
docker logs worker-dev --tail 200
```

workspace 隔离验收至少包括：

1. 模型使用写文件或命令工具后，文件只出现在对应 session 的 `/workspace`。
2. 另一个 session 无法读取该文件。
3. workspace 容器的网络列表不包含 Yuxi `app-network`。
4. workspace 容器配置中不存在 MCP URL/Header、模型凭据和数据库凭据。
5. `/workspace/.mcp` 不存在。
6. 附件越界路径和超过 25 MB 的文件被拒绝。
7. session 删除后，其 workspace 生命周期由 AgentScope workspace manager 清理。

HTTP MCP 还应独立验证真实 `list_tools`/`call_tool`、`disabled_tools`、不可达端点、配置更新和删除。仅确认管理接口保存成功不能证明运行链路可用。
