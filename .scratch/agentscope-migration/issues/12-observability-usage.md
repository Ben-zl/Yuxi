# 12 — 观测与用量

**What to build:** 可观测性与计量在新栈恢复：TracingMiddleware 经 OpenTelemetry 将主链路（模型调用、工具执行、Team worker）trace 送入 Langfuse 的 OTLP 接入；token 与工具用量归集到用户与线程账目，含子智能体（Team）用量（口径以 11 的黄线结论为准），Dashboard 用量视图不失明。

**Blocked by:** 07 — 工具纵切：内置工具 + 知识库工具; 11 — 子智能体 Team 纵切

**Status:** ready-for-agent

- [ ] Langfuse 收到主链路 trace：一次对话含模型与工具 span，Team worker 有归属
- [ ] token 用量按用户/线程归集，与迁移前账目口径一致或与 11 黄线结论一致
- [ ] Dashboard 用量视图有数据（或明确的降级结论记录在 spec）
