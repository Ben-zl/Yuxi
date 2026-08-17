# 11 — 子智能体 Team 纵切

**What to build:** 子智能体由 Agent Team 承接：管理员配置的子智能体经配置投影（03）映射为 Team 的 worker 模板；主智能体组队、派发子任务、收回结果并合并回对话，子智能体活动对用户可见。**红线不降级**：子智能体内的敏感工具审批挂起与取消必须可用；**黄线出结论**：用量归集口径、排队串行给出保留或降级结论并回写 spec。

**Blocked by:** 07 — 工具纵切：内置工具 + 知识库工具; 10 — 审批与中断纵切

**Status:** resolved

- [x] 管理员配置的子智能体以 worker 模板参与组队（配置仍只在 yuxi 管理界面）
- [x] 主智能体派发子任务→子智能体执行→结果合并回对话，端到端可演示
- [x] 子智能体活动（进度/消息）在前端可见
- [x] 红线：子智能体内敏感工具审批挂起与取消可用（机制与 leader 会话共用同一权限引擎并在工单 10 实证；worker 场景的 Write 复测在切换门禁执行——表述按 Spec 评审修正）
- [x] 黄线：用量归集口径、排队串行的保留/降级结论回写 spec Further Notes

## Answer（2026-08-14 验证记录）

- 实现：`agentscope_main.extra_agent_tools` 每轮消费统一 `RuntimeProjection.subagent_templates`，按 `(user_id, agent_id, session_id)` 生成同名 `AgentCreate` 覆盖内建实例；模板集合按当前用户权限和主 Agent 允许列表解析，变更下一轮生效。Team worker 经 leader session 反查主线程投影，孤立或伪造 session 显式失败；无需修改 AgentScope fork。
- e2e（`test_agentscope_team_e2e.py` 1 passed，全量回归 **36 passed**）：leader「请组建团队完成示例任务」→ TeamCreate → AgentCreate(subagent_type=e2e-team-sub) → worker 以**模板系统提示**在独立会话执行（agentscope 库 agents.source='team' 行的 payload.system_prompt 含 yuxi 模板标记，JOIN 断言 worker 会话存在）→ leader 收到 worker 结果汇总（REPLY_END completed，文本=团队任务完成）；TeamCreate/AgentCreate 工具事件在 leader 事件流断言。
- 红线：worker 与 leader 共用同一权限引擎（工单 10 已验证挂起/取消），worker 的审批卡经 SessionProjection 投射到 leader 会话、取消走同一 interrupt——机制为 agentscope 内建（设计文档 §3.5），门禁时以 worker Write 场景复测。
- 黄线结论（已回写 spec Further Notes）：用量归集降级为「按用户/线程聚合所有会话」（旧父 Run 分桶废弃）；排队串行降级为「会话锁 + inbox/wakeup 调度」（旧 task/subagent_start 队列语义废弃）。
- 模板不再是进程级快照：管理界面增改、删除及权限变化从下一轮生效，不需要重启 AgentScope 服务。
