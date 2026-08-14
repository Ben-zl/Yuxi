# 03 — 运行时配置投影闭环

**What to build:** 统一配置投影模块：把 yuxi 库的 Agent 配置、模型供应商与凭据、Skill、MCP 服务器、子智能体模板**完整**投影为 agentscope 运行时对象，闭合覆盖 agentscope service 从自身 StorageBase 解析 Agent 与 credential 的那一面（不留任何从 agentscope 自有表读配置的旁路）。LITE 模式下投影裁剪知识库相关部分且不初始化其依赖。后续工单（工具/Skills/MCP/Team）一律消费本投影，不得各自直连 yuxi 表。

**Blocked by:** 02 — 纵切：一条最小对话走通新链路

**Status:** ready-for-agent

- [ ] 配置投影单一入口：给定 yuxi 库夹具，构造出的运行时对象覆盖模型与凭据、Agent 配置、Skill 源、MCP 客户端配置、子智能体模板
- [ ] agentscope service 对 Agent/credential 的解析全部命中投影结果，无自有表旁路
- [ ] LITE 模式：知识库相关投影裁剪、依赖不初始化、纯聊天可用
- [ ] 统一夹具集成测试作为 Seam 2 入口（含 LITE 用例），置于 integration 目录
