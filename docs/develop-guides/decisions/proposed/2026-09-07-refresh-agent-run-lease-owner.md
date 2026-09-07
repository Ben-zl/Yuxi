# 长 Agent Run 持久化前刷新 lease owner 事实

状态：proposed
类型：bug-fix
Owner：backend/package/yuxi/repositories/agent_run_repository.py

## 问题

长时间 AgentScope Run 的 heartbeat 在独立数据库会话续租。执行会话使用 `expire_on_commit=False` 并长期持有同一 `AgentRun` ORM 实例；后续 `SELECT ... FOR UPDATE` 可能从 identity map 返回启动时的旧 `lease_expires_at`。真实 MiniMax 三子智能体运行中，数据库 lease 在 08:42:24 已续到 08:44:24，但 08:42:31 输出持久化仍错误拒绝当前 owner，父 Run 失败而三个 child Run 已完成。

## 提案

所有 `AgentRunRepository._lock_run` 加锁读取强制 `populate_existing`，让 PostgreSQL 当前行覆盖长事务 identity map 中的陈旧 lease、状态和 owner。现有 fencing 条件不放宽，最终事实仍由加锁后的 PostgreSQL 行拥有。

## 替代方案

- 放宽或删除 lease 到期校验：会允许失联/旧 attempt 写入，拒绝。
- heartbeat 同时修改执行会话中的 ORM 对象：跨会话、跨进程不可行且建立第二套事实，拒绝。
- 只在 `set_output_message` 特判：manifest、终态、取消等其他加锁状态转换仍会读到陈旧行，拒绝。

## 验收标准

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 长事务在独立 heartbeat 续租后认可当前 owner | identity map 保留旧过期时间 | `_lock_run` / PostgreSQL | `pytest test/integration/services/test_agent_run_lease.py` | 会话 A 缓存旧 lease，会话 B 续租后在旧截止时间之后持久化 | Not run |
| 过期或错误 owner 仍被拒绝 | 刷新等同绕过 fencing | `_require_lease_owner` | 既有 lease integration | 使用错误 worker 或数据库真实过期时间 | Not run |
| 真实 MiniMax 多 Agent 能完成父 Run | child 完成但父输出失败 | worker / Run repository | 页面、SSE、PostgreSQL Run/Attempt 回读 | 只检查 child 或页面文本 | Not run |

## 风险

强制刷新可能覆盖同一 session 尚未 flush 的 Run 字段，因此 `_lock_run` 只应在状态转换边界调用，调用前保持当前既有 flush/commit 约束。测试同时覆盖合法续租与错误 owner，防止 fencing 被弱化。
