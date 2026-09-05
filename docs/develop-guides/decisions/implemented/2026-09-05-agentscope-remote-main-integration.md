# AgentScope 基线合并远程 main 的适配边界

状态：implemented
类型：architecture
Owner：backend/server/agentscope_main.py

## 问题

本地 `main` 已将产品执行、Session/Message 投影、Run/Attempt、lease、heartbeat、manifest、事件、reconciliation、Team、Workspace 和 Artifact 统一到 AgentScope 适配层；fetch 后的 `origin/main` `bd07ab4c` 仍包含以旧 LangGraph/旧执行模型为前提的执行、持久化、附件、预览、权限、前端和工程门禁修改。按文件侧选择会恢复已废弃的旧执行事实，或丢失远程仍有用户价值的能力。

本次变更在独立 worktree `/Users/zhanglingbin/Documents/work/github/yuxi-merge-remote-main-agentscope` 的分支 `codex/merge-remote-main-agentscope-20260905` 上完成。基线为本地 `main` `d9182c061061e258a6604c17dca399e40bc7884c`，合并对象为 `git fetch --prune origin` 后的 `origin/main` `bd07ab4ac4faa2e5452507580b1c841543bbec61`。

## 决策

创建一次 `--no-ff` 合并，保留本地 AgentScope 历史和远程提交历史。冲突按语义 Owner 处理：

- AgentScope 执行、Session、Message、Run、Attempt、lease、heartbeat、manifest、事件、reconciliation、Team、Workspace 和 Artifact 继续由当前 Yuxi AgentScope/service/repository/storage 边界拥有。
- 远程仍有产品价值的配置、附件、预览、搜索、权限、安全、限速、前端展示、工程门禁、文档和 Compose 能力，迁移到当前 AgentScope Run/SSE/Workspace/Artifact/Project 投影；不机械恢复旧框架入口。
- Project/Workspace 文件树的展示权限由 `ProjectRepository` 与当前用户绑定的 Project 决定；文件读取、写入、删除和预览继续由 `Workspace` no-follow Owner 与 Artifact/Viewer service 执行。Project 选择器通过显式 `include_unbound_project_dirs` 保留尚未绑定的目录，不改变普通工作区列表的可见性。
- API router 只负责 Query/认证/响应装配，Project 查询由 service/repository 执行；不创建第二套 Run/Execution API、旧 worker、旧 LangGraph checkpoint 或静默 fallback。
- Schema、storage migration、依赖和 Compose 继续使用当前 shipping composition。AgentScope Git 依赖固定到当前产品适配版本，不切换到公开 `main`：本机可见 commit `71f492287b9e24d81fbe5813b5816bb997e5db28`，但公开 fork 当前不再 advertise 该 SHA，因此重新构建镜像无法完成；预构建镜像仅用于挂载源码验证，不作为依赖来源。

## 替代方案

- **直接保留远程冲突版本**：会重新引入旧 LangGraph/执行边界，破坏当前 AgentScope 生命周期 Owner，拒绝。
- **全部保留本地版本**：会丢失远程的安全修复、前端体验、权限限速、预览、工程门禁和文档更新，拒绝。
- **只做线性 rebase 或逐个 cherry-pick**：会改写或拆散双方已存在的提交关系，不满足保留双方历史的目标，拒绝。

## 后果

正面后果是合并结果只有一个执行事实源：AgentScope Session/Execution 与 Yuxi Run/Attempt/lease/manifest/SSE/reconciliation 保持现有绑定关系，同时保留远程用户可观察能力和安全边界。Workspace 列表现在也复用当前 no-follow Workspace Owner，避免普通树视图绕过文件边界。

Docker Workspace 需要以 root 运行 AgentScope 容器才能访问宿主 Docker socket；本次验证通过临时 Compose override 处理，未修改 shipping Compose。Worker health lease 由 ARQ 与 reconciliation worker 共同发布，API readiness 已在真实 Compose 中通过。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| worktree 从本地 `main` 基线创建，当前本地 `main` 与原工作分支不变 | 起点错误、污染当前工作树或修改远程引用 | Git refs/worktree | `git worktree list`、`git rev-parse`、最终 `git status`/`git log` | 当前 `main` SHA、原工作分支或 `origin/main` 发生变化 | `Passed`：本地 `main=d9182c06`、`origin/main=bd07ab4a`，最终 worktree clean |
| 合并提交保留双方历史 | 误用 reset/rebase 或丢失远程父提交 | Git merge commit | `git show -s --format=%P HEAD`、`git log --graph` | 任一父提交不可达或 HEAD 不是 `--no-ff` 合并提交 | `Passed`：`a2564a33` 父提交为 `7a1272f3` 与 `bd07ab4a`；后续仅增加必要修复提交 `ce1a18f0` |
| AgentScope 是唯一执行事实和运行时入口 | 恢复旧 LangGraph/checkpoint/worker，或从相邻 Run 推断结果 | `backend/server/agentscope_main.py`、`backend/package/yuxi/agentscope`、Run service/repository | AgentScope recovery/team/worker/gateway/manifest 相关 unit；完整后端 unit `1581 passed`；旧 checkpointer surface 的负向测试 | 旧 checkpoint 模块仍作为 shipping 入口暴露，或结果不绑定同一 Request/Execution/Run | `Passed`（unit/源码路径）；真实 worker/SSE E2E `Not run` |
| Workspace/Project/Artifact/preview 权限在后端闭合 | 未绑定 Project 目录泄露、symlink 路径绕过或跨 Owner 读取 | `Workspace`、`ProjectRepository`、workspace/artifact/viewer service | Workspace/Project visibility/search 45 tests；完整 backend unit `1581 passed`；目标文件 ruff check/format | 普通 `/projects` 列表暴露未绑定目录；Project picker 不能保留未绑定目录；no-follow 负向测试失败 | `Passed` |
| 远程前端 AgentScope Run/SSE、时间、草稿、Artifact/文件树能力保留 | 前端从相邻消息或旧执行状态推断，或 UI/API 边界漂移 | `web/src/apis`、stores、composables、Agent/Workspace views | `pnpm run lint:check`、`pnpm run test:unit`、`pnpm run build` | Run 失败/取消/断线状态被误报为成功，或 Project/Workspace 请求越权 | `Passed`（命令）；真实浏览器/完整服务页面 `Not run` |
| Schema、工程信任、依赖和文档契约一致 | 迁移不可重复、旧描述残留、链接失效或依赖漂移 | storage migration、Compose、docs、contract scripts | `verify_engineering_contracts.py`、61 contract tests、`docs pnpm run build`、`git diff --check` | 二次迁移失败、文档链接 dead link、旧能力被写成当前入口 | `Passed`（docs build 有 VitePress/Rolldown 警告但无 dead link） |
| 后端完整 lint/format gate 通过 | merge-wide 远程脚本和历史文件已有大量风格问题 | backend package | 全包 `ruff check package` / `ruff format package --check`；目标改动文件定向 check | 将全包历史问题误报为本次适配已通过 | `Not run`/`Blocked`：全包存在 477 个已有/merge-wide 问题；目标 6 文件定向检查 Passed |
| 合并前独立 Reviewer 检查完整需求、diff 和证据 | Reviewer 未返回或把未检查范围误报为通过 | 独立 Reviewer Agent | 独立只读 Reviewer 调度与当前 bounded static review | 未检查完整 merge diff 却宣称全部通过 | `Not run`/`Blocked`：独立 Reviewer 子 Agent 多次超时或运行时参数错误；当前 Agent 完成了不继承上下文的 bounded static review，未把它等同于独立完整 Review |
| 真实 MiniMax 模型链路通过 AgentScope 完成 | provider/base URL/credential/session 映射错误、流式事件或 usage 丢失 | Docker Compose、PostgreSQL、Redis、AgentScope、MiniMax provider | 临时独立 Compose 槽位；真实 `MiniMax-M3` Anthropic-compatible endpoint；`python -m pytest test/e2e/test_agentscope_real_model_e2e.py -m e2e -q -s` | 无非空 assistant 文本、无 reasoning、usage 为 0、Run 非 completed 或 Session 消息未持久化 | `Passed`：1 passed；真实 Docker Workspace、AgentScope `/chat/`、SSE、终态、reasoning、usage 和消息读取均完成；测试清理了临时 fixture |
| API 接流量 readiness 通过 | worker health lease 缺失导致 API 错误放行 | `readiness_service`、worker health lease | `curl http://127.0.0.1:25050/api/system/ready` | 仅 health 200 而 readiness 非 ready | `Passed`：HTTP 200，`startup/postgres/redis/worker` 均为 `ok`；ARQ health lease 与 reconciliation lease 均可回读且 TTL 有效 |

### 实际命令与环境

已实际通过：

```text
python3 scripts/verify_engineering_contracts.py
python3 -m unittest scripts.test_verify_engineering_contracts  # 61 tests OK
git diff --check
backend unit: 1581 passed, 4 warnings
workspace/visibility/agent-memory/subagent targeted tests: 32 passed
workspace service + visibility + search tests: 45 passed
web: pnpm run lint:check
web: pnpm run test:unit
web: pnpm run build
docs: pnpm run build
定向 backend ruff check/format: 6 files passed
```

backend unit 使用 `yuxi-api-merge-smoke:20260903` 挂载当前 worktree 源码运行，`LITE_MODE=false`。这证明当前源码在现有预构建运行时和测试依赖下的 assembled unit path；它不证明镜像可从网络重新构建。

未执行或受阻：

```text
docker compose exec api uv run ruff check package
docker compose exec api uv run ruff format package --check
```

当前独立 compose 配置缺少 `AGENTSCOPE_LEGACY_CUTOFF` 等 `.env` 变量，且预构建镜像没有可执行的 ruff entrypoint；主机 ruff 对全包检查得到 477 个历史/merge-wide 问题，不能把它们归因于本次 Workspace 适配。独立 Reviewer 调度未得到可用回报，因此完整独立 Review 记为 `Not run`/`Blocked`；当前 Agent 只完成了不继承上下文的范围收敛静态检查。`uv run` 在容器中尝试更新或替换 root-owned `/app/uv.lock`/site-packages，故本次真实 E2E 使用容器内已安装依赖直接执行 `python -m pytest`；该命令已通过。真实页面和完整 integration suite 仍未执行。

## 追加边界与并发验证（2026-09-05）

为覆盖合并后 Schema、lease、队列和 Docker Workspace 的真实边界，使用独立 Docker Compose 槽位重新执行了以下验证：

```text
Docker PostgreSQL/Redis/MinIO/AgentScope/worker/api 槽位：启动成功
相关 unit：158 passed
AgentRun lease + 请求队列并发 + manifest/attempt + Docker Workspace integration：27 passed
Docker Workspace 真实文件写入、读取、编辑、附件越界与大小限制：2 passed
```

并发测试夹具同步到当前 AgentScope 事实模型：Conversation 必须绑定 Project/User，非终态 Run 必须填写 `runtime_scope_id`，resume/subagent Run 必须填写当前关系字段；恢复扫描明确只重投 chat/resume 根 Run，不把 subagent child 当成独立队列头。旧 `chat_service.save_messages_from_langgraph_state` lease 测试迁移到 `AgentRunRepository` 的 `lock_output_persistence`、`set_output_message` 和 `set_terminal_status`，未恢复旧 LangGraph 执行入口。

Docker Workspace 首次失败原因为验证槽位内 AgentScope 进程以非 root 用户访问 `/run/docker.sock`，不是业务路径错误；通过仅用于验证的 Compose override 以 root 运行 AgentScope 后，真实 Workspace 测试通过。shipping Compose 未修改。

真实 MiniMax-M3 已完成独立并发会话和长 Agent 任务验证；真实多 Agent Team 协作仍有一次 `interrupted`，日志显示 `AgentCreate`/`Skill` 重复工具注册警告，尚未把该次结果宣称为通过。确定性 mock Team E2E 因缺少 API E2E 登录凭据被测试框架跳过；因此多 Agent 的真实 provider 完整闭环仍是剩余风险。
