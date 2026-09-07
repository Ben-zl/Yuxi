# AgentScope 多智能体运行取消快速收敛

状态：proposed
类型：bug-fix
Owner：backend/package/yuxi/agentscope/gateway.py

## 问题

真实 MiniMax 多智能体运行在页面发起取消后，Yuxi 已提交 `cancel_requested` 且 AgentScope 已接受 Session interrupt，但当 AgentScope 未继续发送 `REPLY_END(interrupted)` 时，父 Run 仍会等待完整事件读取超时才收敛。2026-09-07 的真实页面复现中，取消后约 190 秒页面仍显示运行中，最终才写入 `cancelled`。用户取消不应依赖远端事件流一定产生终态事件。

## 提案

取消监听器在确认 Redis 取消事实并调用 AgentScope interrupt 后，同时向本轮已有的事件队列注入本地取消异常。事件收集器沿既有 `_fail_run` / `set_terminal_status(cancel_requested_as_cancelled)` 路径收敛为 `cancelled`，继续由 PostgreSQL Run/Attempt/lease 和既有 SSE end 事件拥有最终事实。

不修改 AgentScope 框架源码，不新增执行状态或 fallback；AgentScope 的 `REPLY_END(interrupted)` 仍是正常中断路径，本地取消信号只处理远端中断后无终态事件的失败面。

## 替代方案

- 把全局事件读取超时缩短：会误伤真实长模型调用，拒绝。
- 在事件收集器中每秒重复读取 Redis：与现有取消 watcher 重复轮询并增加外部 IO，拒绝。
- 由前端把 `cancel_requested` 乐观显示为已取消：会掩盖 PostgreSQL Run 尚未终态和 lease 未释放，拒绝。
- 等待 reconciliation：只能兜底失联 worker，不能满足交互式取消的及时反馈，拒绝。

## 验收标准

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| AgentScope interrupt 后即使无 `REPLY_END`，事件消费也快速退出 | 继续等待 `READ_TIMEOUT_SECONDS` | `gateway.start_cancel_watcher` | `pytest test/unit/test_agentscope_gateway.py` | stub Session 永不发送事件，取消信号出现后队列仍无退出事件 | Not run |
| 父 Run 最终为 `cancelled` 且 Attempt/lease 关闭 | 只停在 `cancel_requested` | AgentRun repository / worker | 真实 MiniMax 页面取消，回读 PostgreSQL 与 SSE | 只看 HTTP 200 或按钮变化 | Not run |
| 多 Agent child Run 不保留活跃 lease | child 仍运行或取消等待 | Team lifecycle / AgentRun repository | PostgreSQL 父子 Run 与 Attempt 回读 | 仅检查父 Run | Not run |
| 正常 Team 静默收束与普通对话不回归 | 取消修复提前终止未取消任务 | `collect_run_events` | gateway、team lifecycle unit | 无取消信号的活跃 child 仍须等待 | Not run |

## 风险

取消信号与自然完成可能竞争。最终状态继续由 PostgreSQL 行锁、worker lease owner 和 `cancel_requested_as_cancelled` 决定；如果取消事实已提交，完成不得覆盖取消。队列注入必须限定在当前 Run 的 watcher，且 watcher 结束时沿既有任务清理路径释放。
