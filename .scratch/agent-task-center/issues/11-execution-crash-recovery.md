# 11 — 实现 TaskExecution 与 AgentRun 的幂等崩溃恢复

**What to build:** 手动、API 和定时触发在数据库提交、Run 关联或 ARQ 投递之间发生进程中断后，系统能修复已有事实并继续执行，同时保证一个 TaskExecution 最多对应一个 AgentRun 和线程。

**Blocked by:** 06 — 提供异步幂等的 API 触发入口；08 — 补齐定时队列合并、停机恢复和 DST 语义

**Status:** done

**Source:** [父规格 #958](https://github.com/xerrors/Yuxi/issues/958)

- [x] 所有触发方式使用 execution_id 作为标准 Run 提交的稳定 request_id，并为线程生成确定身份。
- [x] TaskExecution 到 AgentRun、线程的关联具有唯一性，重试不能创建第二个 Run 或线程。
- [x] 恢复扫描先按稳定身份查找并重新关联已有 Request、AgentRun 和线程；仅在这些事实均不存在时重试派发。
- [x] 已提交但未成功投递的 pending AgentRun 复用现有确定性 ARQ 恢复机制重新投递。
- [x] Agent 业务失败不自动重试；只有派发基础设施中断进入幂等恢复路径。
- [x] 故障注入集成测试覆盖提交后中断、关联前中断、投递失败和恢复进程重复运行，并证明不会重复执行。
- [ ] 完整浏览器/API 验收覆盖手动、部门共享、API、定时、审批、取消、历史下钻及最近线程排除主路径。
