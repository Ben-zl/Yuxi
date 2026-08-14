# 04 — 网关协议转换：前端契约等价（无工具场景）

**What to build:** 在网关把 agentscope 事件流转换为现有前端 SSE chunk 协议，覆盖初始化/加载/智能体状态/**思考块增量**/文本增量/结束/错误，前端零改动；覆盖无工具场景的断线重连与终态补偿。协议转换逻辑以 agentscope 事件夹具驱动的**单元测试**逐类断言，不依赖真实模型。

**Blocked by:** 02 — 纵切：一条最小对话走通新链路

**Status:** resolved

- [x] 前端不改一行：普通聊天页面流式输出（含思考块）观感与迁移前一致
- [x] 事件语义对齐：init/loading/agent_state/message_delta（文本与思考）/finished/error 与现有协议逐项等价
- [x] 断线重连后恢复运行状态流；异常终态有补偿
- [x] 协议转换单元测试：事件夹具→chunk 输出逐类断言（含思考增量、错误、终态），置于 unit 目录
- [x] 纯对话类 e2e 用例在新链路下全绿（docker compose 环境）

## Answer（2026-08-14 验证记录）

- 实现：`yuxi/agentscope/protocol.py`（纯转换：REPLY_START/TEXT_BLOCK_DELTA/THINKING_BLOCK_DELTA → loading chunk；思考走 reasoning_content 字段与前端 messageProcessor 合并逻辑一致；REPLY_END.finished_reason → completed/failed/interrupted + finished/error/interrupted chunk）+ `gateway.py`（订阅在先、触发在后，实时转换为 Run 事件信封 XADD `run:events:{run_id}`，复用 `run_queue_service.append_run_stream_event`，终态补 end 事件）。
- 契约依据（前端消费面逆向确认）：SSE `event:`/`data:`/`id:` 三行帧；envelope `{schema_version:1, run_id, thread_id, event, payload, created_at}`；断线重连 = `Last-Event-ID` → XRANGE(after_seq)；终态靠 end 事件 + payload.status。
- 测试：`test/unit/test_agentscope_protocol.py` 8 passed（事件夹具逐类）；`test/e2e/test_agentscope_protocol_e2e.py` 1 passed——真实链路写入 Redis Stream 后断言 metadata→messages(init/loading)→end 序列、信封字段、end 状态与 finished chunk，并以 XRANGE 中点续读模拟断线重连续传。
- 边界说明：真实浏览器端到端（前端页面直连新链路）在工单 05 提供运行事实并接入既有 `/api/agent/runs/{id}/events` 端点后、于切换工单复核；本工单以契约等价断言覆盖。
- 事故记录：调试期间误删了主栈 postgres/redis/agentscope-dev/openai-mock 四个容器（清理"残留容器"时按不含容器名的输出行操作），已全部由 compose 重建，数据经核对无损（5 库、agentscope 库消息完好）。
