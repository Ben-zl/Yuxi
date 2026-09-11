# Agent 共享工作区文件工具路由

状态：implemented
类型：bug-fix
Owner：backend/package/yuxi/agentscope/middleware.py

## 问题

页面工作区的 `/agents/MEMORY.md` 属于用户级共享 UserWorkspace，在 AgentScope 运行时对应 `/workspace/workspace/agents/MEMORY.md`。AgentScope 原生 `Read`、`Write` 和 `Edit` 操作每个 Session 的 Docker workspace；Yuxi 另行提供 `shared_workspace_list/read/write` 访问 UserWorkspace。当前平台提示词和工具说明没有表达这个工具边界，模型可以把共享路径交给原生文件工具，工具在错误文件系统返回成功，而页面文件没有变化。

用户截图证明助手宣称已写入 `/workspace/workspace/agents/MEMORY.md`，而页面 `/agents/MEMORY.md` 仍是默认内容。截图没有展示真实 tool call，因此不能仅凭截图断言模型实际调用了哪个工具；修复同时关闭错误工具路径并增强正确工具契约。

## 决策

平台提示词规定 `/workspace/workspace/...` 只能通过 `shared_workspace_list/read/write` 访问，并说明原生 `Read`、`Write`、`Edit` 只操作 Session workspace。修改共享工作区已有文件时，Agent 先读取完整原文，把原内容与变更合并后写回，最后再次读取确认目标内容已经持久化，确认前不得宣称完成。

`SharedWorkspaceToolBoundaryMiddleware` 先按原生工具的 POSIX 解析语义规范化绝对路径和相对 `/workspace` 路径，再在权限边界拒绝 `/workspace/workspace` 及其子路径，并返回正确工具提示。`./`、`../`、重复根斜杠和相对等价路径不能绕过；相邻前缀与 `/workspace/outputs` 等 Session 路径继续走原生工具。共享工具直接使用 `yuxi.workspace.paths.global_user_data_dir`，与页面工作区共享同一 canonical UserWorkspace Owner；服务端 resolver 继续执行路径与 symlink 边界校验。

## 替代方案

- 把用户共享目录直接挂载进 Session Docker workspace：会扩大沙盒可见范围，并让 Bash 与原生文件工具绕过 Yuxi 的共享工作区边界。
- 在 AgentScope 原生工具执行后同步文件：无法可靠区分用户意图，还会复制两个文件系统的生命周期事实。
- 仅修改助手完成文案：不能改变工具选择，也不能证明文件真实写入。

## 验收标准

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 原生文件工具不能操作页面共享路径 | Session workspace 生成同名文件并返回成功 | `SharedWorkspaceToolBoundaryMiddleware` | middleware unit | `Read`、`Write`、`Edit` 指向 `/workspace/workspace/agents/MEMORY.md` | Passed |
| 等价 POSIX 路径不能绕过共享边界 | 原始字符串前缀比较漏判 | path normalization | middleware unit | `./`、`../`、双根斜杠、相对路径 | Passed |
| Session 路径和相邻前缀不被误拦截 | 合法产出文件无法读写 | AgentScope 原生文件工具 | middleware unit | `/workspace/outputs/report.md`、`/workspace/workspace-other/report.md` | Passed |
| 共享工具与页面读取同一 `MEMORY.md` | 工具写入后页面仍是默认内容 | `Workspace` / UserWorkspace | shared workspace tool unit + canonical `Workspace` 回读 | 从默认内容写入唯一文本后读取 `/agents/MEMORY.md` | Passed |
| 运行时提示词要求正确工具、先读后写并回读 | 模型仍可把成功自述当作持久化证据 | runtime prompt projection | prompt unit | 缺少共享工具名或回读要求时测试失败 | Passed |
| 真实 Agent 对话写入后页面立即可见 | 模型和完整服务装配回归 | AgentScope service + Workspace API | 本地 Docker Agent Run、tool history、页面/API 回读 | 只看助手文本 | Not run：本机缺少 Compose 必需的 `AGENTSCOPE_CHANNEL_CREDENTIAL_KEY` |

## 后果

Agent 对页面共享工作区的文件操作由 Yuxi 服务端工具拥有；原生文件工具不能再对共享路径产生误导性的成功结果。修改 `MEMORY.md` 等已有文件时，平台提示要求保留原文并回读。Session workspace 与 UserWorkspace 继续隔离，现有 `/workspace/outputs`、`/workspace/uploads` 行为不变。

## 风险

模型仍可能首次选择原生文件工具，但权限边界会返回明确拒绝并引导重试；最终成功必须由共享工具回读确认。此次只拦截原生 `Read`、`Write`、`Edit` 的共享路径，不改变 Bash、Skill 或 MCP 的能力边界。真实模型是否按提示完成重试需要在可启动的 Compose 环境补验。

## 验证

- 共享工作区相关 AgentScope 测试：路径规范化补充后全部通过；2026-09-11 合并验证包含在 209 项后端定向测试中。
- `python3 -m ruff check`（本次 Python 文件）：通过。
- `git diff --check`：通过。
- Docker Compose / 真实 Agent Run：Not run，本机缺少必需的 `AGENTSCOPE_CHANNEL_CREDENTIAL_KEY`，Compose 配置无法展开。
