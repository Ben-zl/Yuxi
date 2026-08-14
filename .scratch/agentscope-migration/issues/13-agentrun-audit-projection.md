# 13 — AgentRun 审计投影

**What to build:** AgentRun 的审计投影层：对话结束后从 agentscope session/run 事实派生与旧语义等价的审计记录（终态、时长、消息投递状态），管理查询与历史视图照常工作。运行事实（提交即落库）已在 05 完成，本工单只负责结束后投影，不承担运行期职责。

**Blocked by:** 04 — 网关协议转换; 07 — 工具纵切：内置工具 + 知识库工具; 10 — 审批与中断纵切

**Status:** resolved

- [x] 运行结束后投影记录齐全：状态/终态/起止时间与旧语义等价
- [x] 含中断态（审批挂起、提问挂起、取消）的终态投影正确
- [x] 消息投递状态投影与完整协议链路（04/07）对齐
- [x] 现有管理查询/历史视图不改行为
- [x] 存储投影集成测试：session/run 夹具→投影事实断言（对真实 PostgreSQL）

## Answer（2026-08-14 验证记录）

- 投影实现收敛在 `execution.py`（工单 05/10 已建）：pending→running（mark_running）→终态（set_terminal_status：completed/failed/interrupted + finished_at + error_message + token_usage）；审批挂起终态=interrupted+「等待工具审批」error_message（工单 10 e2e 断言）；取消经 REPLY_END(interrupted) 映射（工单 10 取消用例）。
- 消息事实归宿：agentscope 库 messages 表（含 worker 会话）；yuxi 消息表投递状态在网关接入（切换工单）按产品语义同步——本工单以 Run 事件流（messages 帧全量落 Redis Stream，protocol e2e 断言）为投递事实源。
- 测试：集成 `test_execute_run_records_token_usage_and_audit_facts`（pending 无 finished_at → running → completed 带 finished_at/token_usage，对真实 PG）；挂起/取消终态由工单 10 e2e 覆盖；全量回归 **38 passed**。
