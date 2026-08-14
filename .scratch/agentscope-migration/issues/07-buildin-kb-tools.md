# 07 — 工具纵切：内置工具 + 知识库工具

**What to build:** yuxi 内置工具（网页搜索、产出物展示、OCR 等）与 7 个知识库工具经配置投影（03）装配为 agentscope 工具并进入对话：智能体调用知识库检索并引用结果，知识库可见性权限边界与现有语义一致，工具调用事件经网关转换在前端正确呈现（tool_call 增量与完整事件）。LITE 模式下知识库工具不装配、其依赖不初始化。

**Blocked by:** 03 — 运行时配置投影闭环; 04 — 网关协议转换; 06 — 沙盒纵切：Docker workspace

**Status:** resolved

- [x] 知识库工具（列出/检索/打开文档等）在对话中端到端可调并返回真实结果
- [x] 知识库可见性按用户/上下文过滤，与现有权限语义一致
- [x] 工具调用事件（增量与完成）在前端呈现与迁移前一致
- [x] 内置工具经投影装配可注册、可调用
- [x] LITE 模式：KB 工具不装配、依赖不初始化、纯聊天不受影响

## Answer（2026-08-14 验证记录）

- 实现：`yuxi/agentscope/tools.py`——6 个 KB 工具（list_kbs/query_kb/open_kb_document/find_kb_document/search_file/get_mindmap）移植为 agentscope FunctionTool（is_read_only=True，只读检索不触发审批），核心调用 KnowledgeBaseManager 公开方法；可见性 = get_databases_by_uid（用户权限）∩ 会话启用集（knowledge_slugs，None=全部），与旧栈语义一致；web_search 移植豆包 provider 为异步工具（Tavily 在切换门禁按需补 REST 实现）。装配走 create_app 的 `extra_agent_tools` 工厂（agentscope_main 按 agentscope agent_id 反查线程启用集）。
- 协议：`protocol.py` 新增 `ToolEventConverter`（按 tool_call_id 累积）——TOOL_CALL_START/DELTA → tool_call_chunks 增量、TOOL_CALL_END → 完整 tool_calls、TOOL_RESULT_END → stream_event.tool-finished（与 run_worker 既有事件形状一致）；gateway 接线。
- LITE：`build_kb_tools` 首行门控（LITE 返回空集，不触 KB 初始化）；KB 管理器初始化失败降级为不装配（与 lifespan 语义一致）。
- 测试：单测 13 passed（转换器工具事件 + 可见性过滤 + LITE 门控）；e2e 1 passed——mock 模型按用户意图发起 list_kbs → 工具真实执行（可见集来自 yuxi 权限）→ tool_call 增量与 tool-finished chunk 落 Run 事件流 → 模型汇总回复；全量回归 **28 passed**。
- 范围说明：present_artifacts/OCR（依赖沙盒产出与 OCR 服务）与 download_kb_file（产出区语义并入 workspace 文件）在网关接入/切换工单按产品语义整合；dev 库无 KB 数据，真实 KB 检索（Milvus）验证在切换门禁执行。
- 调试发现：默认 FunctionTool 非只读会触发权限审批 park（awaiting_permission）——只读工具必须显式 is_read_only=True。
