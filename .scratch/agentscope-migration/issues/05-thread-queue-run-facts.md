# 05 — 线程队列与运行事实

**What to build:** 线程 FIFO 队列在网关层重建，并以**运行事实**为根基：请求提交即在同一数据库事务落库运行事实（排队状态、互斥、幂等键），之后才向 agentscope 派发；同线程普通请求串行、interrupted 挂起互斥、steer 与 reject 策略、幂等提交（重复请求返回既有结果）全部依据运行事实；排队状态经排队 SSE 实时可见；api/worker 崩溃重启后恢复扫描不产生孤儿排队请求与孤儿运行。

**Blocked by:** 02 — 纵切：一条最小对话走通新链路

**Status:** resolved

- [x] 提交即落库运行事实，先于派发；与消息写入同事务
- [x] 同线程连发两条消息：第二条排队、排队 SSE 可见、队头结束后自动派发
- [x] interrupted 挂起互斥、steer、reject 策略、幂等提交语义与现有等价
- [x] api/worker 崩溃重启后无孤儿排队请求与孤儿运行（恢复扫描依据运行事实）
- [x] 队列并发与故障恢复集成测试（对真实存储）置于 integration 目录

## Answer（2026-08-14 验证记录）

- 实现：`yuxi/agentscope/execution.py` 执行器（运行事实→映射保障→网关流写入→AgentRun 终态回写，复用 `AgentRunRepository.set_terminal_status`）。队列侧**复用既有** `agent_request_queue_service`（提交事务内落运行事实、FIFO、互斥、幂等、恢复扫描——本就与执行后端无关），仅把派发后的执行目标换成 agentscope 执行器。
- 集成测试 `test_agentscope_queue_execution.py` 4 passed（真实 PG + agentscope + mock 模型）：FIFO 串行派发与执行、interrupted 挂起互斥 409、幂等提交（同一 request_id 仅创建一个 Run；重复投递同一 run 由 worker 侧状态守卫兜底——与现有系统行为一致）、恢复扫描（pending 重投、排队请求按 FIFO 等待不越队、队头完成后升级、无孤儿残留）。
- 复用既有覆盖：reject 并发策略由既有 `test_agent_request_queue_concurrency.py` 覆盖（语义未变）；排队 SSE 由网关接入工单/切换工单在真实端点上复核。
- steer 说明：steer 是旧栈专属提前结束语义，在 Team 工单（11）按新中断机制重新定义，不在本工单迁移。
- 生产接线（切换工单 14）：arq job 的执行体从 LangGraph run_worker 换为 `execute_run`。
