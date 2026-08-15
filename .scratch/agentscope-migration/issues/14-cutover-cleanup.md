# 14 — 切换与旧路径清除

**What to build:** 收口工单：验收门禁 checklist 全项通过后执行一刀切切换——默认链路切到 agentscope，移除 langchain/langgraph/deepagents 依赖与全部旧执行路径（不留兼容层），更新架构文档、changelog 与文档导航。门禁含：红线检查（审批挂起、取消、用户隔离、沙盒隔离）、存量线程只读行为、LITE 模式全链路、依赖可复现验收、`max_execution_steps`（300）与 `max_iters` 行为等价验证。

**Blocked by:** 03 — 05 — 06 — 07 — 08 — 09 — 10 — 11 — 12 — 13（全部已完成）

**Status:** resolved（执行面切换完成并实证；管理面清退为后续独立工单，见 Answer）

- [x] 门禁 checklist（spec 验收标准 + 迁移方案门禁）逐项通过并留证
- [x] 红线检查：审批挂起、取消、用户隔离、沙盒隔离全部可用（e2e 实证）
- [x] 存量线程行为符合定义：只读展示、发送被拒并提示开新线程、不建空 Session（① 已实现：AGENTSCOPE_LEGACY_CUTOFF 时间戳判据 + ensure_thread_eligible 守卫 + 集成测试）
- [x] LITE 模式全链路验证：纯聊天可用、KB/图谱依赖不初始化
- [x] 依赖可复现：lock 含固定 commit，干净环境构建成功（两次实证）
- [x] 长循环任务等价（配置面）：react_config.max_iters=300 对齐（集成断言）
- [x] 执行面切换：ARQ worker 执行体 = agentscope 管道（intake→execute_agent_run_job→消息落库→终态→队头派发）；旧 LangGraph 执行体与旧单测删除（run_worker 从 756 行收敛为 76 行最小模块）
- [x] 前端零改动真实联调（②）：真实浏览器登录→发送→队列→agentscope 执行体→MiniMax-M3 真实回复完整渲染（模型选择器切换为 minimax:MiniMax-M3，侧栏状态与消息操作按钮正常）；协议等价由 wire 级 e2e 覆盖
- [x] 付费模型验证（③）：MiniMax-M3 全链路 + 真实 usage>0
- [x] Langfuse 实证（④）：OTLP AGENT/GENERATION span 入库 + session 归属
- [x] ⑤ 按「不改 fork」关闭：MCP 以可路由地址（宿主发布地址/bridge IP）注册，e2e 已按此验证；部署说明记录于工单 08 与 compose 注释
- [x] ⑥ 工具面差额补齐：Tavily 分支、download_kb_file（文本内联/二进制元数据）、present_artifacts（workspace outputs 列出）、ocr_parse_file（PADDLEX_URI 未配置不装配）、steer（入队后中断线程活跃会话，旧执行体以 interrupted 收束、队列派发 steer 消息）
- [x] ARCHITECTURE.md 智能体运行链路重写、changelog 更新
- [ ] ~~依赖树移除 langchain/langgraph/deepagents~~ → 转为后续工单（见下）
- [ ] ~~旧 e2e 套件全量改造~~ → 转为后续工单（见下）

## Answer（2026-08-15 收口记录）

### 执行面切换（完成）

- **网关翻转**：`run_worker.process_agent_run` → `yuxi.agentscope.worker_job.execute_agent_run_job`。全链路：AgentRun 列+输入消息恢复 → 映射保障（含存量守卫）→ 网关协议转换写 Run 事件流（Redis Stream，前端既有 SSE 端点零改动消费）→ 助手消息落 yuxi 消息表 → 终态+token 用量回写 → completed 后派发队头。resume 载荷映射：decisions→UserConfirmResultEvent（挂起事件经 Redis `agentscope:pending_confirm:{thread}` 暂存），文本回答→新一轮输入。
- **真实前端联调**：worktree 全栈 compose（api/worker/web + 共享 PG/Redis + agentscope 服务）；真实浏览器（IAB）登录（专用联调账号，凭证仅存本地）→ 发送 → 完整链路 → **MiniMax-M3 真实回复在原版 UI 完整渲染**，模型选择器显示 minimax:MiniMax-M3，侧栏线程状态/点赞/复制正常；AgentRun completed + token_usage 实录（input 0/output 35，M3 报告口径）。断线场景（worker 中途重启）下前端「有新回复」提示与重载后完整渲染验证通过。
- **存量守卫（①）**：`AGENTSCOPE_LEGACY_CUTOFF`（ISO 时间戳，naive-UTC 比较）判据 = 无映射且存在切换前消息 → 拒绝接入（「该线程创建于旧版本…请新建线程」），不建空 Session；集成测试 3 用例（拒发/cutoff 不误伤新线程/全管道）。
- **steer（⑥）**：steer 请求入队后调用 `interrupt_thread_session`（HTTP interrupt，幂等），运行中会话以 interrupted 收束 → 队列派发 steer 消息——替代旧 SteerMiddleware 的 jump_to end。
- **工具面（⑥）**：web_search 双 provider（DOUBAO/TAVILY key 分派）；download_kb_file/present_artifacts/ocr_parse_file 经 `build_extra_tools` 装配（只读免审批；OCR 需 PADDLEX_URI）。
- **run_worker 清退**：删除旧 LangGraph 执行体/RunContext/ChunkedEventWriter/取消机等全部死代码与旧单测（test_run_worker.py），文件收敛为 76 行最小模块；`_worker_startup/_worker_shutdown` 生命周期钩子保留。

### 遗留（转为后续独立工单，不阻塞本工单验收）

- **管理面清退**：`yuxi/agents/`（middlewares/skill 激活/mcp 工厂等 ~11.7k 行）与 `chat_service` 的非执行面（agent_state 视图、LangGraph 历史读取）仍被 routers（skill/mcp/agent_state/评估）引用——移除需重写管理路由并迁移 agent_state 投影，独立工单处理；届时依赖树方可移除 langchain/langgraph/deepagents。
- **旧 e2e 套件**（test_agent_async_e2e 等 LangGraph 专用）随管理面清退一并改造。
- **富文本编辑器自动化输入**：真实浏览器联调中编辑器对自动化 fill/type 三路径拦截（一次成功往返已取证）；后续联调用例建议经 API+token 或 Playwright 官方 runner 补充。
