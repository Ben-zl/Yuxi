# 14 — 切换与旧路径清除

**What to build:** 收口工单：验收门禁 checklist 全项通过后执行一刀切切换——默认链路切到 agentscope，移除 langchain/langgraph/deepagents 依赖与全部旧执行路径（不留兼容层），更新架构文档、changelog 与文档导航。门禁含：红线检查（审批挂起、取消、用户隔离、沙盒隔离）、存量线程只读行为、LITE 模式全链路、依赖可复现验收、`max_execution_steps`（300）与 `max_iters` 行为等价验证。

**Blocked by:** 03 — 运行时配置投影闭环; 04 — 网关协议转换; 05 — 线程队列与运行事实; 06 — 沙盒纵切：Docker workspace; 07 — 工具纵切：内置工具 + 知识库工具; 08 — MCP 接入; 09 — Skills 渐进披露; 10 — 审批与中断纵切; 11 — 子智能体 Team 纵切; 12 — 观测与用量; 13 — AgentRun 审计投影

**Status:** ready-for-agent

- [ ] 门禁 checklist（spec 验收标准 + 迁移方案门禁）逐项通过并留证
- [ ] 红线检查：审批挂起、取消、用户隔离、沙盒隔离全部可用（任一失败即 NO-GO）
- [ ] 存量线程行为符合定义：只读展示、发送被拒并提示开新线程、不建空 Session
- [ ] LITE 模式全链路验证：纯聊天可用、KB/图谱依赖不初始化
- [ ] 依赖可复现：lock 含固定 commit，干净环境构建成功
- [ ] 长循环任务行为等价性验证：300 步上限语义在新配置下等价
- [ ] 依赖树中不再有 langchain/langgraph/deepagents；旧执行路径代码删除
- [ ] ARCHITECTURE.md 智能体运行链路、changelog、docs 导航更新完成
- [ ] 切换后 docker compose 全链路 e2e 全绿
