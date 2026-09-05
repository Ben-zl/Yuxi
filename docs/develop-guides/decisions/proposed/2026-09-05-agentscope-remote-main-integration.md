# AgentScope 基线合并远程 main 的适配边界

状态：proposed
类型：architecture
Owner：backend/server/agentscope_main.py

## 问题

本地 `main` 已将产品执行、Session/Message 投影、Run/Attempt、lease、heartbeat、manifest、事件、reconciliation、Team、Workspace 和 Artifact 统一到 AgentScope 适配层；`origin/main` 在共同内容基线之后继续包含旧 LangGraph/旧执行模型重构、运行事实、附件预览、权限、安全、前端和工程门禁修改。直接按文件侧选择会恢复已废弃的旧执行事实，或丢失远程仍有价值的用户能力。

本次合并基于本地 `main` `d9182c061061e258a6604c17dca399e40bc7884c`，目标远程为 fetch 后的 `origin/main` `bd07ab4ac4faa2e5452507580b1c841543bbec61`。

## 提案

在独立分支执行一次 `--no-ff` 合并并保留双方提交历史。冲突按语义 Owner 处理：AgentScope 执行、Session、Message、Run、Attempt、lease、heartbeat、manifest、事件、reconciliation、Workspace 和 Artifact 继续由本地实现拥有；远程用户可观察能力、安全修复、前端体验、文档和工程门禁在不恢复 LangGraph 或第二套执行事实的前提下，迁移到当前 AgentScope service/repository/API/SSE 投影。

已由本地实现等价覆盖的远程能力不重复实现；远程依赖旧 checkpoint、旧 worker、旧 middleware 或旧路由的能力，改为当前 AgentScope 的等价路径。Schema 变更继续由唯一的 storage-migrator Owner 负责，API 不直接拼装持久化事实。

## 替代方案

- 直接保留远程冲突版本：会重新引入旧 LangGraph/执行边界，破坏当前 AgentScope 生命周期 Owner，拒绝。
- 全部保留本地版本：会丢失远程的安全修复、前端体验、权限限速、预览、工程门禁和文档更新，拒绝。
- 只做线性 rebase 或逐个 cherry-pick：会改写或拆散双方已存在的提交关系，不符合本次保留双方历史的目标，拒绝。

## 验收标准

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 新分支从本地 `main` 创建且本地 `main` 不变 | 起点错误或污染当前 worktree | Git refs/worktree | `git worktree list`、`git rev-parse`、`git status` | 当前 `main` SHA 或当前工作分支发生变化 | `Not run` |
| 合并提交保留本地和远程两个父提交 | 误用 reset、rebase 或丢失一侧历史 | Git merge commit | `git show -s --format=%P HEAD`、`git log --graph` | 合并后任一父提交不可达 | `Not run` |
| AgentScope 仍是唯一执行事实和运行时入口 | 远程旧 LangGraph/worker/checkpoint 被恢复 | `backend/server/agentscope_main.py`、`backend/package/yuxi/agentscope` | shipping 源码/依赖/Compose 搜索与 AgentScope unit/E2E | 恢复旧执行入口或从相邻 Run 推断结果 | `Not run` |
| 远程安全、权限、附件/预览和前端能力在当前边界生效 | 只保留文案或前端限制，后端事实缺失 | 对应 service/repository/router 与前端 API | 真实 HTTP、PostgreSQL、文件/Artifact、Web unit/build/页面检查 | 未授权读取、错误 Run 归属、旧框架状态泄漏 | `Not run` |
| Schema、依赖、文档和工程门禁一致 | 锁文件漂移、迁移不可重复或文档误述 | storage-migrator、pyproject/lock、文档 Owner | contract tests、ruff、docs build、真实迁移回读 | 二次迁移失败或文档宣称未实现能力 | `Not run` |

## 风险

- 远程 116 个提交与本地 AgentScope 代码存在大量后端、测试、文档和前端冲突，预计不能依赖自动合并。
- 远程部分提交的功能描述仍以 LangGraph/旧 worker 为前提，需要逐项确认用户可观察结果和当前 AgentScope 等价路径。
- 依赖、Schema、Compose 和文档变化可能影响启动和 readiness；必须在独立 Compose 槽位通过真实验证。
- 外部模型、Langfuse 或其他可选服务缺少凭证时，相关验证只能记录为 `Not run`，不能宣称完成。
