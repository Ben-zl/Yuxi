# Yuxi Agent 开发约定

Yuxi 是基于 AgentScope、FastAPI、Vue 和多种持久化服务构建的知识库与多智能体平台。Docker Compose 是开发拓扑的事实来源；修改陌生模块前先读 [ARCHITECTURE.md](ARCHITECTURE.md)，再用符号搜索确认真实实现。

## 任务边界

- 围绕用户目标工作，先写可验证目标、非目标、假设和风险；多步任务先给出带验证方式的简短计划。
- 区分已验证事实、推断和未知项。缺少关键决策且会改变数据、安全或外部状态时才询问，其余基于保守假设继续。
- 只改验收所需范围，不顺手重构、格式化或添加兼容层、fallback、配置和依赖。
- 非平凡变更先在 `docs/develop-guides/decisions/proposed/` 建立 tracked decision；完成后移到 `implemented/` 并改写为现在时。
- `docs/vibe/` 仅用于本地临时计划，不作为组织记忆或完成事实。

## 当前架构事实

- HTTP 路由只做解析、认证和响应装配；用例属于 `yuxi.services`，持久化查询属于 `yuxi.repositories`。
- 所有 Web、Channel、任务和内部调用通过 `RunSubmissionCommand` 提交；PostgreSQL 持有 ExecutionRequest、AgentRun、Attempt、lease、heartbeat、manifest、事件和终态事实。
- AgentScope 是唯一执行事实 Owner：Session、Message、Run、Team/SubAgent、HITL、取消、恢复、SSE、reconciliation 和 Workspace 均沿当前 `yuxi.agentscope` 链路实现。
- PostgreSQL 持有业务事实；Redis 只承载投递、短期事件、取消信号和缓存。完成状态、输出、事件、Artifact 和错误必须绑定同一 Request/Execution/Run。
- 普通请求按用户、Agent、Session 串行调度；非终态 Run 必须有明确 Owner、lease/heartbeat 和 reconciliation 结局。
- `/api/system/health` 只表达 liveness；接流量条件由 `/api/system/ready` 证明。权限最终在后端依赖和 repository 可见性查询处执行。
- AgentScope Session、Execution、Message 只使用 PostgreSQL Storage；API 与 AgentScope 服务不提供本地后端选择或静默降级。
- LITE 启动、路由注册和能力发现不得导入知识库、图谱、评估重运行时；解析器只在真实解析动作发生时惰性加载。
- Workspace 虚拟路径、对象 URL 和宿主路径不可混用；用户路径必须在 owning filesystem boundary 校验，并拒绝 symlink 越界。

## 实现规则

- 沿 producer → registration → consumer → persistence/publication → user-visible result 追踪主链路，在真实语义 Owner 处闭合主张。
- 复用现有类型、依赖、repository、Workspace、Artifact 和 AgentScope projection；已有能力足够时不重复实现。
- 输入只在 parser、配置、模型/tool JSON、持久化、worker、process、wire 和用户路径等真实边界校验；副作用 executor/repository fail-closed。
- 新函数/类使用简洁中文 docstring；注释只解释非显然约束、时序、Owner 和安全用法。
- 不提交 `.env`、账号、Token、用户数据、运行目录和构建产物；文件以换行结尾，提交前运行 `git diff --check`。

## 验证与证据

测试职责见 [docs/develop-guides/testing-guidelines.md](docs/develop-guides/testing-guidelines.md)。先跑最小相关集合，再按风险升级；unit 不能替代真实 HTTP、PostgreSQL、worker、SSE、文件、对象或浏览器验证。每个新 guard 都要有能恢复目标缺陷并在正确原因上失败的负向测试。

提交前在仓库根目录至少运行：

```bash
python3 scripts/verify_engineering_contracts.py
python3 -m unittest scripts.test_verify_engineering_contracts
docker compose exec api uv run --group test pytest test/unit -m "not slow"
git diff --check
```

涉及范围时补充：

```bash
docker compose exec api uv run ruff check package
docker compose exec api uv run ruff format package --check
docker compose exec api uv run --group test pytest test/integration
docker compose exec api uv run --group test pytest test/e2e -m e2e
docker compose exec web pnpm run lint:check
docker compose exec web pnpm run test:unit
docker compose exec web pnpm run build
cd docs && pnpm run build
```

实际命令、结果、数据库/文件/对象回读、未运行原因和剩余风险必须如实记录；`Passed` 只表示命令真实成功且结果已核对，缺少凭证或服务写 `Not run`。

## Git 与 Review

- 默认分支前缀为 `codex/`。并行 worktree 使用独立 Compose project、端口和 `../.yuxi/slots/<slot>` 数据目录；不删除现有 volume、`.env` 或生产数据。
- 未经当前请求明确授权，不执行 push、PR、Issue、Release、远端标签或评论等远端写操作。
- 提交信息使用中文 Conventional Commit。代码变更在 commit 前必须由不继承开发上下文的全新 Reviewer 审查完整需求、decision、diff、测试和未验证范围；先修复功能、边界、证据或认知负担问题，再提交。
