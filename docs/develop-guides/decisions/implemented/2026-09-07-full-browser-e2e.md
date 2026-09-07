# 全功能浏览器 E2E 验收方案

状态：implemented
类型：testing
Owner：web/src/components/AgentChatComponent.vue

## 问题

当前 Compose 槽位已经启动，但缺少一份覆盖用户可见主入口的浏览器验收范围。仅运行前端单元测试或后端 E2E，不能证明登录、路由守卫、页面交互、真实 API、worker/SSE 和持久化结果在浏览器中一致。

本轮目标是在 `http://localhost:35173/` 使用真实浏览器执行关键功能链路。测试数据只创建在当前隔离 Compose 数据库和用户工作区中，并在结束后清理；不修改远程仓库，不使用生产数据。

## 决策

使用 Playwright CLI 对 `http://localhost:35173/` 执行稳定的浏览器主链路验收，并把页面 DOM、网络结果、刷新后的状态、截图和后端回读作为证据。测试数据仅创建在隔离 Compose 槽位，结束后按逆序清理。AgentScope Run、SSE、Session/Execution、Project、Workspace 和 Artifact 的事实边界继续由现有后端实现拥有；浏览器测试不引入第二套执行事实源。

## 替代方案

- 只运行 Web unit：不能证明真实登录、路由、API、SSE、worker 和刷新恢复链路，拒绝。
- 只运行后端 E2E：不能证明浏览器页面状态、响应式布局和用户可观察错误，拒绝。
- 使用远程 MiniMax 或外部 Langfuse 作为本地必需前置：会使页面验收不可重复，改为单独专项验证。

## 后果

本记录覆盖首页、登录、权限、对话、项目、Agent 管理、Workspace、Skills、MCP、工具、知识库、任务中心、Dashboard、搜索、主题、响应式和清理共 18 个目标。Mock AgentScope 链路可证明本地页面到 Run/SSE/终态的闭环，但不替代真实外部模型、Langfuse、解析器或普通用户越权证据。

## 验证

| 验收主张 | 语义 Owner | 直接证据 | 当前结果 |
|---|---|---|---|
| Compose 服务与业务 readiness 可用 | Docker Compose、`/api/system/ready` | `docker compose ps`；`curl -fsS http://localhost:35050/api/system/ready` | Passed；状态为 `ready`，PostgreSQL、Redis、worker 等 required checks 为 `ok` |
| 浏览器主导航、登录、超级管理员权限和页面可达 | `web/src/router/index.js`、`web/src/layouts/AppLayout.vue` | Playwright CLI DOM snapshots、页面 URL 和截图 | Passed |
| AgentScope mock 对话、项目选择、Run/SSE/终态/刷新恢复 | `web/src/components/AgentChatComponent.vue`、Run/SSE service | 浏览器消息终态、刷新后历史、网络/DOM 证据 | Passed |
| 未选项目创建独立对话 | `web/src/components/AgentChatComponent.vue` | 浏览器发送“不使用项目”消息后成功进入线程并完成；`project_id` 不发送 `__auto__` | Passed；并新增 `web/test/unit/projectThreadCreation.test.js` |
| 标题生成使用当前对话模型 | `web/src/components/AgentChatComponent.vue` | 浏览器 mock 对话复测；源码回归测试 | Passed；并新增 `web/test/unit/titleGeneration.test.js` |
| Dashboard 会话状态语义准确 | `web/src/components/dashboard/ThreadStatsComponent.vue`、`ThreadDetailDrawer.vue` | 页面显示“活跃”；新增 dashboard 状态回归测试 | Passed |
| Workspace 文件夹、CSV 上传、预览和空态 | Workspace API/service、页面组件 | Playwright DOM、文件树和预览证据 | Passed；临时文件已清理 |
| Skills、MCP、工具、知识库、任务中心、Dashboard、搜索和主题入口可用 | 对应 web 页面/API owners | 页面 DOM、空态、筛选、主题和窄视口截图 | Passed（空态/入口级） |
| 前端单元测试 | Web test owner | `docker compose exec -T web pnpm test:unit` | Passed：168 passed, 0 failed |
| 前端构建 | Vite/Web build | `docker compose exec -T web pnpm run build` | Passed；仅有既有 chunk size 与 compiler warning |
| 工程信任检查 | `scripts/verify_engineering_contracts.py` | `python3 scripts/verify_engineering_contracts.py` | Not run after this record relocation；需下一轮执行并修正任何既有 gate 问题 |
| 临时数据隔离与清理 | Project/Workspace repositories | API 回读和删除响应 | Passed；项目、对话、Workspace 文件夹和 CSV 已删除 |

## 未验证边界

真实 MiniMax、外部 Langfuse、知识库完整解析、普通用户越权写入、任务中心已有后台任务详情、浏览器断网恢复、真实取消/长任务以及多 Agent 页面链路未在本轮完成。当前使用隔离环境的 mock 模型或空态；这些边界保持 `Not run`，不将页面成功或 mock 结果写成外部服务通过。
