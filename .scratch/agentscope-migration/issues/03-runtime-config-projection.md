# 03 — 运行时配置投影闭环

**What to build:** 统一配置投影模块：把 yuxi 库的 Agent 配置、模型供应商与凭据、Skill、MCP 服务器、子智能体模板**完整**投影为 agentscope 运行时对象，闭合覆盖 agentscope service 从自身 StorageBase 解析 Agent 与 credential 的那一面（不留任何从 agentscope 自有表读配置的旁路）。LITE 模式下投影裁剪知识库相关部分且不初始化其依赖。后续工单（工具/Skills/MCP/Team）一律消费本投影，不得各自直连 yuxi 表。

**Blocked by:** 02 — 纵切：一条最小对话走通新链路

**Status:** resolved

- [x] 配置投影单一入口：给定 yuxi 库夹具，构造出的运行时对象覆盖模型与凭据、Agent 配置、Skill 源、MCP 客户端配置、子智能体模板
- [x] agentscope service 对 Agent/credential 的解析全部命中投影结果，无自有表旁路
- [x] LITE 模式：知识库相关投影裁剪、依赖不初始化、纯聊天可用
- [x] 统一夹具集成测试作为 Seam 2 入口（含 LITE 用例），置于 integration 目录

## Answer（2026-08-14 验证记录）

- 实现：`yuxi/agentscope/config_projection.py` 统一入口 `project_runtime()`（RuntimeProjection 载荷：agent_request、credential_data、chat_model_config、skill_slugs、mcp_server_names、knowledge_slugs、subagent_templates、model_spec）；`projection.py` 扩展 agent 请求与子智能体模板投影 + `is_lite_mode()`；runner 的 ensure_thread_session 改为唯一消费方（所有 agentscope 侧 agent/credential 写入均经投影，无旁路）。
- 调研事实（决定形态）：Agent/credential 记录唯一来源是 agentscope 的 StorageBase（ResourceAccessPolicy 只做跨 owner 引用，不注入记录），因此投影为**推写式**；Skills/MCP 进入 agent 的通道是 workspace（`workspace.list_skills/list_mcps`），装配分别落在工单 08/09。
- 测试：`test/integration/services/test_agentscope_config_projection.py` 3 passed（全组件覆盖、LITE 裁剪、显式失败）；tracer e2e 改用 DB 夹具后 2 passed；骨架 e2e 3 passed——合计 8 passed。
- 后续工单注意：夹具沿用 it-proj-* / e2e-* 命名空间并自行清理；Skill/MCP 装配走 workspace API，不直接写其存储表。
