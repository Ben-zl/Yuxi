# Agent 删除后的长期记忆补偿清理

状态：implemented
类型：bug-fix
Owner：backend/package/yuxi/services/agent_cleanup_service.py

## 问题

Agent 删除需要先形成 Yuxi PostgreSQL 业务事实，再清理 AgentScope 外部长期记忆。外部服务不可用、仍有活动 Run，或进程重启时，物理删除 Agent 会丢失可靠补偿标记，并允许同 slug 新 Agent 接管旧执行稍后写入的记忆。

## 决策

`agents.deletion_pending_at` 是删除屏障和持久补偿事实。删除接口锁定 Agent 行、标记 tombstone、在同一锁内检查任务引用、解绑已归档任务引用并提交；公开 Agent repository 查询排除 tombstone，因此产品语义上 Agent 已删除，而数据库唯一 slug 在补偿完成前仍被占用。

Run intake、任务创建/换绑和 AgentScope memory scope 创建都会锁定并验证同 slug Agent 仍活动。补偿器仅在该 slug 没有 queued Request 和非终态 Run 时调用 AgentScope 清理；清理成功后再次回读 `agent_memory_scopes`，只有 catalog 为空才物理删除 tombstone。网络、超时、busy 等外部失败保留 tombstone，并由独立 ARQ cron 及 worker 启动扫描重试；领取时更新重试顺序，避免固定前 50 条阻塞队尾。

删除 API 返回 `success=true`；完整清理完成时 `cleanup_pending=false`，否则返回 `cleanup_pending=true`。补偿任务使用短外部超时，不与 Run lease reconciliation 共用串行循环。

## 替代方案

- 只复用 orphan `agent_memory_scopes`：无法阻止旧 Run 在清理后重建 scope，也无法可靠隔离跨表 slug 复用竞态，因此拒绝。
- 新增 cleanup outbox/table：可以工作，但 Agent tombstone 已同时提供删除屏障、唯一 slug 占用和重试 owner，额外表会复制生命周期事实。
- 外部清理成功后再提交删除：AgentScope 故障会阻止本地管理操作，且不能原子提交跨服务副作用。
- 仅日志或内存重试：重启后丢失，不能作为补偿事实。

## 后果

- AgentScope 长时间不可用或旧 Run 长时间非终态时，tombstone 会保留并阻止原 slug 复用；这是防止记忆串用的 fail-closed 行为。
- 数据库增加 `agents.deletion_pending_at`，部署必须先执行现有幂等 business schema migration。
- 补偿器不修改 AgentScope 框架源码；Yuxi repository、service、HTTP client 和 worker composition 拥有该行为。

## 验证

- Router 与 service unit 验证 tombstone 先提交、外部失败返回 pending、任务引用仍映射 409。
- Repository unit 验证 tombstone 占用 slug，memory ensure 在 Agent 删除/待删除时拒绝旧 writer。
- Worker unit 验证补偿使用独立 cron；client unit 验证 transport error 统一包装为可重试错误。
- 真实 PostgreSQL + 真实 AgentScope integration 验证：外部失败后 tombstone/catalog 保留，同 slug 分配为后缀；重试成功后 catalog 与 tombstone 删除，原 slug 才可复用。
- 负向案例覆盖恢复“物理删除后再清理”“仅捕获 HTTP 状态错误”“活动 writer 存在仍清理”和“删除后允许 ensure scope”时的失败。
