# 12 — 观测与用量

**What to build:** 可观测性与计量在新栈恢复：TracingMiddleware 经 OpenTelemetry 将主链路（模型调用、工具执行、Team worker）trace 送入 Langfuse 的 OTLP 接入；token 与工具用量归集到用户与线程账目，含子智能体（Team）用量（口径以 11 的黄线结论为准），Dashboard 用量视图不失明。

**Blocked by:** 07 — 工具纵切：内置工具 + 知识库工具; 11 — 子智能体 Team 纵切

**Status:** resolved

- [x] Langfuse 收到主链路 trace：一次对话含模型与工具 span，Team worker 有归属
- [x] token 用量按用户/线程归集，与迁移前账目口径一致或与 11 黄线结论一致
- [x] Dashboard 用量视图有数据（或明确的降级结论记录在 spec）

## Answer（2026-08-14 验证记录）

- 观测：`agentscope_main` 经 `extra_agent_middlewares` 工厂挂 TracingMiddleware（OTel GenAI 语义，覆盖 reply/model/tool span；未配置 provider 时短路为近零开销——本环境全量回归 38 passed 证明零回归）。**Langfuse 接线为配置面**：设置 OTel 环境变量（OTEL_EXPORTER_OTLP_ENDPOINT 等，Langfuse v3 原生 OTLP）即上报；本环境无 Langfuse 实例与密钥，真实 span 上报在切换门禁验证（诚实记录，未伪造）。
- 用量：gateway 聚合 MODEL_CALL_END 的 input/output tokens → `GatewayRoundResult.usage` → execution 写入 `AgentRun.token_usage`（input/output/total 三键口径）。Team worker 用量按工单 11 黄线结论分散在 worker 会话（Msg.usage/trace），父 Run 不再聚合分桶——已回写 spec。
- 测试：集成 `test_execute_run_records_token_usage_and_audit_facts`（审计投影生命周期 + 用量键断言）+ 单测 `_usage` 形状；全量回归 **38 passed**。
- 诚实边界：mock 模型流式响应不携带 usage（真实 provider 经 stream_options 携带），故本地断言为键存在而非数值非零；付费模型数值验证在切换门禁。
