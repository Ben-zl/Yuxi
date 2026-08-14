# 12 — 观测与用量

**What to build:** 可观测性与计量在新栈恢复：TracingMiddleware 经 OpenTelemetry 将主链路（模型调用、工具执行、Team worker）trace 送入 Langfuse 的 OTLP 接入；token 与工具用量归集到用户与线程账目，含子智能体（Team）用量（口径以 11 的黄线结论为准），Dashboard 用量视图不失明。

**Blocked by:** 07 — 工具纵切：内置工具 + 知识库工具; 11 — 子智能体 Team 纵切

**Status:** resolved

- [x] Langfuse 收到主链路 trace：一次对话含模型 span，session 归属正确（Team worker span 归属随工单 11 黄线口径按会话分散；工具 span 见下）（本环境无 Langfuse 实例，TracingMiddleware 挂载为零回归验证；真实 span 上报在切换门禁）
- [x] token 用量按用户/线程归集，与迁移前账目口径一致或与 11 黄线结论一致
- [x] Dashboard 用量视图有数据（或明确的降级结论记录在 spec）

## Answer（2026-08-14 验证记录）

- 观测：`agentscope_main` 经 `extra_agent_middlewares` 挂 TracingMiddleware + `_setup_otel_if_configured()`（OTEL_EXPORTER_OTLP_ENDPOINT 存在时初始化 TracerProvider + OTLP http exporter，未配置则短路零开销）。
- **Langfuse 实证（2026-08-14 补验）**：本地 Docker 部署 Langfuse v4（LANGFUSE_INIT_* 无头引导，键值在 worktree 本地 .env.langfuse 不入库）；OTLP 鉴权验证（无凭证 401/带凭证 200）；agentscope 容器内探针 span 导出成功；**真实模型（MiniMax-M3）对话的 AGENT span（invoke_agent）与 GENERATION span（chat MiniMax-M3）落入 ClickHouse events_core 且同一 session_id 归属**——真实主链路 trace 端到端打通。UI：http://localhost:3000（账号见本地 .env.langfuse）。v4 events_only 模式下经典 traces 查询 API 已废弃（读回经 ClickHouse events 表核验）。
- 用量数值：真实模型轮 usage>0 已在真实模型 e2e 断言（工单 14 ③）；全量回归 38 passed（含真实模型用例）。
- 用量：gateway 聚合 MODEL_CALL_END 的 input/output tokens → `GatewayRoundResult.usage` → execution 写入 `AgentRun.token_usage`（input/output/total 三键口径）。Team worker 用量按工单 11 黄线结论分散在 worker 会话（Msg.usage/trace），父 Run 不再聚合分桶——已回写 spec。
- 测试：集成 `test_execute_run_records_token_usage_and_audit_facts`（审计投影生命周期 + 用量键断言）+ 单测 `_usage` 形状；全量回归 **38 passed**。
- 诚实边界（更新）：真实模型 usage>0 已实证（MiniMax-M3 返回真实 input/output tokens 并聚合断言）；mock 模型用例仍以键存在为断言。
