# 08 — 补齐定时队列合并、停机恢复和 DST 语义

**What to build:** 定时任务在执行缓慢、worker 停机和夏令时切换时仍产生确定、可解释的执行历史，不积压无界定时执行，也不集中补发陈旧任务。

**Blocked by:** 07 — 支持单条每日、每周或 Cron 定时规则

**Status:** ready-for-agent

**Source:** [父规格 #958](https://github.com/xerrors/Yuxi/issues/958)

- [ ] 每个任务最多存在一条 queued 的定时 TaskExecution；后续到期周期记录为 skipped。
- [ ] worker 恢复时只补发固定十五分钟宽限期内最近一次到期执行，其他未执行周期记录为 missed。
- [ ] 春季 DST 不存在的本地时间记录为 skipped，不移动到其他时刻执行。
- [ ] 秋季 DST 重复的本地时间只对第一次实例创建一次执行。
- [ ] 多 worker 并发扫描时，同一计划时间不会产生重复 TaskExecution。
- [ ] 纯时间计算单元测试和真实 PostgreSQL/worker 集成测试共同覆盖时区、恢复及并发风险。

