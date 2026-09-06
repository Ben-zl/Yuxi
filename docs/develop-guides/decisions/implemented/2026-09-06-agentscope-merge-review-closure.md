# AgentScope 远端合并审查闭环

状态：implemented
类型：bug-fix
Owner：backend/package/yuxi/agentscope/worker_job.py

## 问题

远端 `main` 合并后，AgentScope shipping 链路仍存在几个会破坏统一 Run 事实的边界：执行前 manifest 的 write-once 指纹未被严格校验；Team child Run 可能复用过期 lease；heartbeat 在任务取消时可能继续续租；删除 Team runtime 时未停止 child heartbeat；WPS ingress 的空白 sender 白名单可能让未授权发送者继承 binding owner 权限。

## 决策

- 顶层和 Team child Run 均在 AgentScope 执行前固化 manifest，并校验持久化 fingerprint；write-once 冲突直接失败，不覆盖历史事实。
- `mark_running` 对已过期的 running lease 原子创建新 Attempt 并取得新 owner；Team child 恢复路径通过该入口重新取得 lease。
- worker 执行使用 `finally` 停止 heartbeat；Team child 在终态、异常和 runtime 删除时停止 heartbeat。
- WPS ingress 要求非空 `allow_from`，且事件 sender 必须命中白名单；WPS 执行继续使用 `RunSubmissionCommand`，不新增执行事实源。
- 保持当前 AgentScope 执行、Session、Run/Attempt、Workspace、Artifact 和权限 Owner；不恢复旧 LangGraph、旧 worker、旧 checkpoint 或 AgentScope 框架源码修改。

## 替代方案

- 仅延长 lease 或沿用旧 `worker_id`：不能可靠处理重启、过期和 fencing，拒绝。
- 让 Channel 以 owner 身份默认放行：会绕过 sender 授权，拒绝。
- 恢复旧 LangGraph worker/checkpoint 或新增兼容执行层：会产生第二套执行事实源，拒绝。

## 后果

Run 的运行资产、Attempt、lease、heartbeat、终态和输出继续由 Yuxi PostgreSQL 与当前 AgentScope 适配层统一拥有。过期 owner 会被新 Attempt/fencing 收束；WPS 未授权消息在进入 Run 之前被拒绝。heartbeat 是进程内活性机制，进程退出仍由 lease 过期和 reconciliation 收束。多 Agent 长任务、真实 provider 和浏览器页面仍需在具备对应 fixture、凭据和服务的环境中验证。

## 验证

| 验收主张 | 语义 Owner | 直接证据 | 当前结果 |
|---|---|---|---|
| manifest write-once 指纹拒绝覆盖 | worker、manifest service、AgentRun repository | Docker unit/integration；manifest mismatch 负向测试 | Passed |
| 过期 child lease 使用新 Attempt/owner | Team lifecycle、AgentRun repository | Docker `test_agent_run_lease.py` 与 worker/queue integration | Passed |
| heartbeat 在取消和删除路径停止 | run lease、worker、conversation service | Docker unit；删除 child runtime 负向顺序测试 | Passed |
| WPS sender 权限在后端最终执行 | WPS ingress、binding repository | Docker WPS unit/integration；非白名单 sender 负向测试 | Passed |
| 合并只保留一个 AgentScope 执行事实源 | AgentScope/Yuxi execution owners | engineering contracts、源码审查、依赖与锁文件检查 | Passed |
| 完整 integration 回归 | backend integration owners | `109 passed, 176 skipped` | Passed（跳过项不计入通过） |
| 完整 E2E 回归 | backend E2E owners | 独占 Docker 运行：`13 passed, 5 failed, 20 skipped` | 部分验证；失败为未启动 MCP/确定性 mock、Skill fixture 未注入及现有 Team fixture 不匹配 |
| clean AgentScope build | `backend/pyproject.toml`、`backend/uv.lock` | `git ls-remote`、`uv lock --check` | 部分验证；SHA/lock Passed，clean build Not run |

未验证项不作为通过证据：MiniMax 真实模型、多 Agent 长任务、浏览器页面和外部 Langfuse。用户提供的模型凭据未写入命令、日志、文件或提交。
