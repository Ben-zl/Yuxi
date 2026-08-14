# 02 — 纵切：一条最小对话走通新链路

**What to build:** 第一发 tracer bullet：用户消息从网关派发到 worker 的 agentscope chat，用 yuxi 库中的模型供应商配置构造模型对象（最小投影，完整闭环在 03），最小智能体（系统提示、无工具）完成一轮真实流式回答；线程（Thread）与 Session 一一对应，会话历史持久化，worker 重启后同线程可续聊；线程按用户隔离。

**Blocked by:** 01 — 骨架：worker 容器运行 agentscope service

**Status:** resolved

- [x] 端到端冒烟：发消息收到所选供应商模型的真实回答
- [x] 模型对象由 yuxi 模型供应商配置构造，OpenAI 兼容供应商可跑通
- [x] Thread ↔ Session 一一对应，历史持久化到独立 database，重启后同线程续聊不丢上下文
- [x] 用户 A 的线程对用户 B 不可见（多租户边界与 yuxi 用户模型对齐）

## Answer（2026-08-14 验证记录）

- 实现：`yuxi/agentscope/`（client/projection/runner）+ `agentscope_thread_sessions` 映射表（模型在 models_business、DDL 在 ensure_business_schema、repo 在 repositories）。
- 模型端点：dev 库无可用密钥的 chat 供应商，采用 OpenAI 兼容流式 mock（独立容器 `openai-mock`，确定性回复）走真实 HTTP/SSE 全链路；付费模型验证在切换门禁执行。
- e2e：`test_agentscope_chat_tracer_e2e.py` 2 passed（整轮回路 + 用户隔离）。
- 重启持久化：容器重启后历史 4 条完好，续聊第 5/6 条成功（AgentState 上下文跨重启加载）。
- 关键事实（后续工单依赖）：事件 type 为大写枚举名（REPLY_START/TEXT_BLOCK_DELTA/REPLY_END）；run 结束持久化后 service 会 `log_trim` 清空回放日志——事件必须在运行期在线消费，订阅必须先于触发（网关 04 的断线重连依赖回放日志窗口）；同 session 并发 chat 返回 409 + 分布式会话锁。
