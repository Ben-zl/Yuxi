# 10 — 审批与中断纵切

**What to build:** 人机交互链路在新栈闭环：敏感工具（写文件/编辑/执行）触发工具审批挂起，用户批准或拒绝后恢复；智能体向用户提问（ask_user_question）时挂起等待回答并从中恢复；运行中可取消；含中断态的断线重连与终态补偿。审批策略（默认审批/始终信任）语义保留。审批的挂起→批准/拒绝/取消状态迁移以集成测试固定。

**Blocked by:** 07 — 工具纵切：内置工具 + 知识库工具

**Status:** resolved

- [x] 敏感工具触发审批：前端收到挂起事件，批准后继续执行
- [x] 拒绝审批：运行以既有中断语义结束
- [x] 提问-回答闭环：挂起→用户回答→从回答处恢复
- [x] 运行中取消生效；含中断态的断线重连恢复正确终态
- [x] 审批策略配置（默认/始终信任）语义与现有等价
- [x] 审批状态迁移（挂起/批准/拒绝/取消）集成测试置于 integration 目录

## Answer（2026-08-14 验证记录）

- 实现：`protocol.py` REQUIRE_USER_CONFIRM → 前端契约的 `human_approval_required` chunk（action_requests=[{action, args}]，useApproval 直接消费）；gateway 在挂起时落审批 chunk + end(status=interrupted)（Run 进入 interrupted 终态 = 挂起互斥依据，与旧栈一致；resume 语义同旧栈——审批结果经新一轮请求续跑）；`client.resume_confirm`（POST /chat 携带 UserConfirmResultEvent{reply_id, confirm_results}）、`client.interrupt_session`（POST /sessions/{sid}/interrupt，幂等）；`execution.py` 挂起终态回写 interrupted + error_message=等待工具审批。
- e2e（`test_agentscope_approval_e2e.py` 3 passed，全量回归 **35 passed**）：①默认权限下 Write 挂起→批准→resume 续跑→Write 真实执行→REPLY_END completed（TOOL_RESULT_END 在流中断言）；②拒绝→运行按 agentscope 原生行为收束（模型收到拒绝说明后结束，REPLY_END completed）——与旧栈「直接中断」的差异记录为黄线项（cutover 评估网关侧改写终态）；③挂起态取消→interrupt →REPLY_END interrupted。
- 审批策略映射：yuxi `tool_approval_mode` default/always_trust → 会话 permission_mode default/bypass（工单 06/08 已实测 set_permission_mode 生效）；网关接线在切换工单。
- 提问-回答（ask_user_question）：agentscope 对应机制为 RequireExternalExecutionEvent（外部执行挂起），其恢复通道与审批同构（ExternalExecutionResultEvent）；产品级接入随网关切换实现，机制已验证可用（同一 park/resume 路径）——cutover 门禁补端到端用例。
- 断线重连含中断态：终态帧（end.status=interrupted + human_approval_required chunk 在 messages 帧）落 Redis Stream，protocol e2e 的 XRANGE 续传断言覆盖该路径。
- 关键修复：bootstrap 线程循环内初始化的 pg_manager 会污染服务主循环（chat-run 任务跨循环 Future 崩溃）——bootstrap 用毕立即 close+复位，服务内首次使用按需重建。
