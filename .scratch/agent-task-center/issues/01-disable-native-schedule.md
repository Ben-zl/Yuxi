# 01 — 关闭 AgentScope 原生 Schedule 入口

**What to build:** 从智能体可用能力中移除 AgentScope 原生 Schedule 入口，确保用户无法绕过 Yuxi 的任务事实链路创建定时执行。后续受支持的定时触发必须统一产生 AgentTask、TaskExecution 和 AgentRun。

**Blocked by:** None — can start immediately.

**Status:** done

**Source:** [父规格 #958](https://github.com/xerrors/Yuxi/issues/958)

- [x] 普通智能体和相关模板不再装配或暴露 AgentScope 原生 Schedule 工具。（NativeScheduleBlockMiddleware 在 on_model_call 边界统一过滤 Schedule*；实测模型请求 33 工具零 Schedule，既有工具全保留）
- [x] 移除入口后，既有非调度工具、审批机制和 AgentRun 执行链路保持可用。（kb_tools e2e 通过；真实 MiniMax-M3 run completed）
- [x] 自动化测试能够证明原生 Schedule 无法从 Yuxi 智能体调用。（test_agentscope_middleware.py 2 用例：Schedule* 过滤 + 无工具透传）

