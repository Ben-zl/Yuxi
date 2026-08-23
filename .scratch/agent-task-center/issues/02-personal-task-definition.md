# 02 — 建立个人 AgentTask 定义管理

**What to build:** 用户可在任务中心创建、查看和编辑个人 AgentTask，配置名称、智能体、提示词、启停状态、API/定时触发开关和工具审批模式，为后续执行入口建立统一任务定义。

**Blocked by:** None — can start immediately.

**Status:** done

**Source:** [父规格 #958](https://github.com/xerrors/Yuxi/issues/958)

- [x] 登录用户能够创建个人任务，并在列表和详情中看到保存后的核心配置。
- [x] 创建任务默认启用手动触发、默认选择"完全信任"，定时和 API 触发默认按表单选择保存。
- [x] 用户只能管理自己有权访问的任务，且任务可见范围不得超过所选智能体的可见范围。
- [x] 编辑任务只影响未来触发，不改写既有 TaskExecution。
- [x] 删除接口不作为生命周期入口；用户通过启停和后续归档能力管理任务。

