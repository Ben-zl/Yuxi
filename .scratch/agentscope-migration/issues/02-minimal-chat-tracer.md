# 02 — 纵切：一条最小对话走通新链路

**What to build:** 第一发 tracer bullet：用户消息从网关派发到 worker 的 agentscope chat，用 yuxi 库中的模型供应商配置构造模型对象（最小投影，完整闭环在 03），最小智能体（系统提示、无工具）完成一轮真实流式回答；线程（Thread）与 Session 一一对应，会话历史持久化，worker 重启后同线程可续聊；线程按用户隔离。

**Blocked by:** 01 — 骨架：worker 容器运行 agentscope service

**Status:** ready-for-agent

- [ ] 端到端冒烟：发消息收到所选供应商模型的真实回答
- [ ] 模型对象由 yuxi 模型供应商配置构造，OpenAI 兼容供应商可跑通
- [ ] Thread ↔ Session 一一对应，历史持久化到独立 database，重启后同线程续聊不丢上下文
- [ ] 用户 A 的线程对用户 B 不可见（多租户边界与 yuxi 用户模型对齐）
