# 04 — 实现任务级 FIFO 队列与生命周期操作

**What to build:** 同一 AgentTask 的全部执行共享单一 FIFO 队列并严格串行，用户可预测地禁用、归档或取消任务执行，任一执行结束后队列继续推进。

**Blocked by:** 03 — 贯通个人任务的手动执行与历史查看

**Status:** ready-for-agent

**Source:** [父规格 #958](https://github.com/xerrors/Yuxi/issues/958)

- [ ] 同一任务同时收到多个手动触发时只运行队头，其余保持 queued 且顺序稳定。
- [ ] running 或 interrupted 执行占用唯一执行槽，不允许后续执行越过。
- [ ] succeeded、failed 或 cancelled 后自动派发下一项，不自动暂停任务。
- [ ] 禁用任务保留当前 AgentRun 并取消全部排队执行；禁用后拒绝新触发。
- [ ] 归档任务同时禁用任务、保留历史且不能再触发。
- [ ] 任务所有者可取消任意执行，取消行为继续复用标准 AgentRun 取消链路。
- [ ] 并发集成测试证明 PostgreSQL 领取约束下不会出现同任务重叠运行。

