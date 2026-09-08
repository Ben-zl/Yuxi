# 恢复 AgentScope 文件面板的目录与多标签状态链路

状态：implemented
类型：bug-fix
Owner：web/src/components/AgentChatComponent.vue

## 问题

展示和预览的具体实现由 `web/src/components/AgentPanel.vue` 承接，父组件负责状态 Owner。

0.7.2 的文件面板将对话目录、个人空间、文件预览和子智能体视为同一个可切换的 Section 集合。AgentScope UI 适配后保留了文件树和预览数组，却没有把 Section 状态、激活/关闭事件及文件系统生命周期传递给 `AgentPanel`，导致多个文件只能覆盖当前预览，用户无法切回之前的文件，打开面板时的刷新和运行期轮询也不会按面板可见状态启动。

## 决策

恢复单一的 AgentPanel Section 状态链路：

- `AgentChatComponent` 持有 `FILE_TREE_SECTION`、文件 Section 和子智能体 Section；
- 打开、激活、关闭文件时同时更新 preview tabs 与 Section；
- 预览 Tab 记录创建线程，切换线程期间拒绝旧线程文件请求，个人空间文件独立于线程；
- 将文件系统可见性、轮询状态和刷新版本传给 `AgentPanel`；
- 保留当前 AgentScope 的文件读取、预览缓存和 Workspace/Workdir 权限边界，不恢复旧 LangGraph 执行路径或新增执行事实源。

## 替代方案

- 只在 `AgentPanel` 内增加独立的 preview tab 状态：拒绝，会形成与 AgentChat 的第二套事实；
- 只恢复 `.preview-tabs-bar` 样式：拒绝，该样式没有对应的模板状态和事件链路；
- 回退整个 AgentScope UI 适配提交：拒绝，会丢失当前 AgentScope 的请求、运行和恢复语义。

## 后果

文件树、文件预览、个人空间文件、子智能体面板和文件关闭后的活动项恢复重新使用同一套 Section 状态。文件夹首次展开继续通过现有异步 `loadData` 加载子节点。面板生命周期会依据真实打开、页面可见和运行状态控制刷新与轮询。

## 验证

- `node --test --test-concurrency=1 web/test/unit/messageGrouping.test.js web/test/unit/thread_draft.test.js web/test/unit/messageDebug.test.js web/test/unit/statePanelLayout.test.js web/test/unit/context_usage.test.js web/test/unit/agentPanelSections.test.js web/test/unit/agentPanelIntegration.test.js web/test/unit/fileTreeComponent.test.js web/test/unit/agentChatRecovery.test.js`：通过。
- `docker compose exec -T web pnpm run lint:check`：通过。
- `docker compose exec -T web pnpm run build`：通过；仅有既有 chunk size warning。
- `docker compose run --rm --no-deps -v backend:/app --user root api uv lock --upgrade-package transformers`：通过，锁定 `transformers 5.16.1`。
- `git diff --check`：通过。
- `docker compose exec -T api python -c "import transformers; print(transformers.__version__)"`：通过，重建后的 API 镜像为 `5.16.1`。
- 浏览器真实页面已验证：过程折叠、上下文 Ring、双预览 Tab、最大化/还原、线程切换入口；旧线程预览请求的历史 500 已在代码中以线程归属 guard 阻断，需新浏览器会话补做无历史错误复测。
- 完整前端单元测试、真实浏览器长任务/Debug/窄屏状态面板：待执行。

## 2026-09-08 收口修复

- 文件搜索继续以已迁移线程的 AgentScope Session Workspace 为 Owner；历史线程才使用 Project Workdir。
- 预览身份统一使用 `workspace:<uid>:<path>`、`artifact:<run_id>:<path>`、`thread:<thread_id>:<path>`，关闭、刷新和线程切换按结构化 `previewKey` 释放 Blob URL，避免同路径跨作用域互相覆盖。
- 线程切换在父组件关闭面板前释放旧线程预览缓存；AgentPanel 的线程 watcher 只清理旧线程，不误删新线程缓存。
- Agent 删除沿用当前 AgentTask/AgentScope Owner：本地 Agent/Task 事实先提交，之后才调用 AgentScope 运行时/记忆清理，避免外部副作用早于业务提交；未归档任务引用仍阻断删除。
- 对话 loading elapsed 使用 Run 的 `started_at`（恢复/重连优先使用持久化值；新 Run 在收到后端元数据前使用本地启动时刻作为临时显示），所有终态清理起点。

验证：29 个后端聚焦测试通过；15 个前端聚焦 Node 测试通过；host ESLint 对本轮前端文件通过；工程信任检查与 61 个测试通过；真实 Docker 首页可访问，API/worker/AgentScope/PostgreSQL/Redis healthy。完整后端 unit 未作为全绿证据：Docker 容器未挂载仓库根目录且持久化测试目录污染，导致 74 个环境/fixture 失败，1537 个通过。
