# 07 — 支持单条每日、每周或 Cron 定时规则

**What to build:** 用户可为 AgentTask 配置最多一条定时规则，预览指定时区中的未来触发时间；worker 到期后以任务所有者身份创建 TaskExecution，并进入与其他触发方式相同的 FIFO 队列。

**Blocked by:** 01 — 关闭 AgentScope 原生 Schedule 入口；04 — 实现任务级 FIFO 队列与生命周期操作

**Status:** done

**Source:** [父规格 #958](https://github.com/xerrors/Yuxi/issues/958)

- [ ] 每个任务最多保存一条规则，可选择每日、每周简单配置或五段 POSIX Cron，并保存 IANA 时区。
- [ ] 简单规则和 Cron 使用同一套解析与下次运行计算能力，不引入第二种调度模型。
- [ ] 创建和编辑时可预览未来五次本地执行时间；非法表达式和短于五分钟的周期被明确拒绝。
- [ ] worker 按固定扫描周期领取到期任务，持久化时间使用 UTC，定时执行身份为任务所有者。
- [ ] 定时触发创建 source 为 agent_task、channel 为 internal 的 TaskExecution，并进入统一任务队列。
- [ ] 禁用或归档任务不会继续生成定时执行。

