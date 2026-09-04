# AgentScope Run 终态恢复

## 问题

Team worker 的工具输出可能包含 PostgreSQL `text/jsonb` 不接受的 NUL 字符。此时 AgentScope reply 和 `TeamSay` 已完成，但 Yuxi 保存 child Run 历史失败，父 Run 会持续等待该 child。若父任务随后达到 ARQ 超时或服务重载，Yuxi Run 可能失去执行所有权并长期停留在 `running`。

## 决定

- 在 AgentScope 事件转换为 Yuxi 持久化工具历史的边界递归替换 NUL，不修改实时事件和 Workspace 原始文件。
- Team 父 Run 等待 child 时，查询 AgentScope Session 权威状态；Session 已 `idle` 且对应 reply 已持久化时，从该 reply 幂等恢复 child 输出和终态。
- worker 启动时先恢复 `idle` Session 对应的 stale 顶层 Run，再重新投递仍需执行的 `chat/resume` Run；Team child 不作为独立 ARQ 任务重新执行。
- 已消费的审批或问答 reply 同步更新原 ToolCall 状态，再清除 pending key。
- Yuxi 内层超时比 ARQ 外层超时短 30 秒。明确超时先中断并确认 parent/worker Session 停止，再使用新事务收束 Run；服务重载产生的取消继续交给 ARQ 重试。
- 业务事务异常后先 rollback，再写失败终态，避免 aborted transaction 使收尾再次失败。
- 正常 Team 生命周期与恢复流程通过同一 Run 行锁竞争终态；只有获胜者可以创建输出消息和发送终态事件。
- 审批恢复的 ToolCall 在同一数据库事务内按 Conversation 限定更新，Run 终态提交成功后才清除 Redis pending key。

## 替代方案

- 仅手工修改卡住的 Run：不能阻止同类数据再次发生，也无法恢复输出和工具历史。
- 所有 stale Run 直接重新投递：会重复调用模型、重新创建 Team worker，并可能覆盖已完成的 AgentScope 结果。
- 把任意 `idle + running` 直接标记成功：缺少持久化 reply 时无法证明业务结果，可能误结束真实等待状态。

## 后果

- AgentScope 持久化 reply 成为失去 worker 所有权时的恢复依据；没有完成 reply、Session 非 `idle` 或仍有未完成交互时不自动收束。
- TeamSay 已成功但 Yuxi 本地历史写入失败时，child 按已交付完成处理。
- 恢复只重建最后一条完成的顶层 reply，避免重放模型和工具副作用。

## 验证

- NUL 工具输入、输出和错误信息的转换测试。
- TeamSay 成功后 reply 报错的恢复测试。
- stale re-enqueue 产生空 setup error 时保留已有最终答复的测试。
- ARQ 内层超时、服务重载取消和 aborted transaction 收尾测试。
- 真实对话 `bdd75162-ef26-4391-84e6-bdb700161c4b` 回读 PostgreSQL、AgentScope Session、Redis pending key 和浏览器 DOM。
