# 06 — 提供异步幂等的 API 触发入口

**What to build:** API Key 用户可异步触发已经保存并启用 API 触发的 AgentTask，通过稳定幂等键获得唯一 TaskExecution，并轮询执行状态。

**Blocked by:** 05 — 支持部门共享、执行身份和工具审批

**Status:** done

**Source:** [父规格 #958](https://github.com/xerrors/Yuxi/issues/958)

- [ ] API 触发只接受现有 Bearer API Key，并准确记录触发者和 api 渠道；JWT 请求不伪装为 API 触发。
- [ ] 请求必须携带 Idempotency-Key，成功时立即返回 202 和 execution_id。
- [ ] 同一任务、执行身份和 Idempotency-Key 的重复请求返回同一 execution_id，不创建第二条执行。
- [ ] API 只能执行保存的任务定义，不能覆盖智能体、提示词、模型、Skill、MCP、工具或审批模式。
- [ ] 个人任务和调用者有权访问的部门任务均遵循既有可见性及启停校验。
- [ ] 提供与任务权限一致的异步执行状态查询，并用真实 API Key 集成夹具验证，不在测试或日志中保存凭据。

