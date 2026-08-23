# 03 — 贯通个人任务的手动执行与历史查看

**What to build:** 用户手动触发个人任务后立即获得 TaskExecution，系统通过标准提交链路创建 AgentRun 和独立线程；用户可从任务详情查看执行状态、结果和完整 Agent 线程。

**Blocked by:** 02 — 建立个人 AgentTask 定义管理

**Status:** done

**Source:** [父规格 #958](https://github.com/xerrors/Yuxi/issues/958)

- [ ] 启用中的任务可被所有者手动触发，并立即返回可查询的 execution_id。
- [ ] TaskExecution 在 AgentRun 创建前即存在，触发时固定智能体身份、提示词、审批模式和执行身份。
- [ ] 派发复用标准 AgentRun 提交、事件、取消和审计链路，运行时读取智能体最新配置。
- [ ] 每次任务执行使用独立线程，执行详情可展示消息、工具调用、事件和产物。
- [ ] 任务线程不出现在最近线程列表，终态线程不可继续输入。
- [ ] Docker 环境中的接口集成测试和浏览器路径覆盖“创建—触发—查看结果—打开线程”。

