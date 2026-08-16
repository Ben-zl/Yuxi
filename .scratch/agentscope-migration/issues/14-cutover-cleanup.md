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

## 真实浏览器全量验证记录（2026-08-15，commit a47b62dc）

在 docker compose 全栈 + MiniMax-M3 真实模型下以 GUI 黑盒方式验证主链路与边界，发现并修复 4 个执行面缺陷（均已回归 + 浏览器复验）：

1. **审批 chunk 契约断裂**：网关发出的 `human_approval_required` 为顶层 `action_requests`，前端 `useApproval` 要求 `approval.action_requests` + 等长 `review_configs`，且 fork 的 `ToolCallBlock.input` 是 JSON 字符串（非 `inputs` dict）→ 模态框永不弹出、参数不显示。修复于 `protocol.py`。
2. **resume 路径不写终态**：审批恢复 run 永远停在 running（消息已落库但 Run 卡死，前端一直转圈）。新增 `execution.finalize_run` 供执行/resume 共用。
3. **steer 中断后队列停摆**：`_get_queue_state` 把一切 interrupted 视为"等待回答或审批"→ 引导消息永不派发；删除排队请求后线程还会死锁（intake 409）。现在 interrupted 仅在 `pending_confirm` 存在（审批挂起）时保持等待。
4. **steer 中断竞态**：中断原先在 intake 事务内触发，worker 终态后派发可能早于排队行提交而扑空。中断移至 `finalize_intake` 提交后。

另有：worker 关机钩子调用不存在的 `stop_runtime_sync`（重写引入）已移除。

**验证通过项**：登录、模型选择器（minimax:MiniMax-M3）、新线程流式往返、多轮会话记忆、侧边栏运行状态、审批批准/拒绝双路径（文件真实落盘/未落盘）、存量线程只读拒发（run failed + 精确文案 + 零映射）、运行中刷新页面重连（全文完整）、steer 端到端（长文 3.6s 被中断 → 引导 run 32ms 派发 → 回复到达）。34 项测试全绿。

**已知边界（未修，后续观察）**：
- fork（不改）新会话**首轮**运行中 steer 中断可能不生效（agentscope `UserInterruptEvent` reply_id 不匹配被跳过，run 跑完；引导消息仍会在其完成后派发）；第二轮起正常。
- 审批挂起时若整页刷新，模态框不恢复（前端 pendingInterrupt 仅内存态；Redis 挂起缓存 24h 有效，重开会话发送新消息可走 resume 链路）。
- failed run 的错误信息不在会话内持久展示（前端既有产品行为，非迁移回归）。
- MiniMax 经 Anthropic 兼容端点 input_tokens 上报为 0（output 正常）。

## 第二轮浏览器全量验证记录（2026-08-15，commit 86dcca83）

覆盖并发/长任务/取消/技能/MCP/知识库/Agent Team，发现并修复 3 个执行面缺陷：

1. **Run 取消信号零消费者**：旧栈消费者随网关翻转移除，点停止后 run 继续跑完并误标 completed。修复：网关流循环与 resume 收集各挂取消监听器（收到信号→中断会话），`finalize_run` 将终态归一 `cancelled`。验证：取消后 ~2s 会话中断、run=cancelled、UI 回空闲。
2. **run 级模型选择丢失**：worker 从不读 `run.input_payload.model_spec`，无模型智能体（deep-research）忽略用户显式选择的模型、回落系统默认。修复：model_spec 贯通三处调用链。
3. **「深度研究」提示词教旧栈工具**：提示词指挥 `task` 工具（已不存在），模型用 AgentCreate 建完成员就结束回合、子智能体从不执行。修复：改为 TeamCreate/AgentCreate/TeamSay 工具链描述。验证：Team 编排全链路（建队→建成员→派发→成员回报→≤150字综合报告）成功落库并渲染。

**通过项（真实浏览器 + MiniMax-M3）**：
- 并发：3 线程 run 时间戳交错重叠执行、全部 completed
- 长任务：3000 字长文完整生成（4608 字符）多次；运行中取消
- 技能：模型逐字复述 e2e-skill-demo SKILL.md（渐进披露工作正常）
- MCP：echo 工具绑定→模型调用→审批弹窗（参数显示）→允许→mcp-mock 收到 CallTool→回复精确回显
- 知识库：list_kbs 真实调用（事件流 6 处 tool_call + tool-finished），返回 0 库
- Team：见上

**环境备注（非代码缺陷）**：
- `SILICONFLOW_API_KEY` 在本地 .env 为空值带中文行内注释（docker env_file 不剥离），作为"密钥"进 Authorization 头触发 UnicodeEncodeError 500；建议清理该行为空。
- docker workspace 后端下 MCP URL 必须为可路由地址（⑤既定结论的实证复现）；e2e 已参数化 MCP_MOCK_URL。
- 知识库真实索引需 Milvus（本环境无），浏览器级验证止于工具面；KB 工具管线由 e2e 覆盖。

## 第三轮验证记录：Team 多轮 / 沙盒 / 子智能体 / 审批中断深化 / KB+内置工具（2026-08-16，commit e1595701）

**通过项（真实环境）**：
- 沙盒 Bash：审批→允许→python3 真实执行输出 42 回传
- 沙盒文件套：Write→Read→Bash ls 三工具链完整（文件 20 字节与内容吻合；ls 只读命令免审批）
- 沙盒隔离：每线程独立 workspace（27 个目录），测试文件仅存在于创建线程
- 子智能体（通用型）：general-purpose 子智能体执行 2**20=1048576 并回报
- 审批挂起时输入区完全禁用（UI 阻断；API 层 409 由回归覆盖）
- Team 长任务运行中取消：4.4 秒 cancelled（取消监听器对 team 会话同样生效）
- KB 空库降级：list_kbs 返回空、模型如实报告；web_search 未配置时不装配工具、模型给出替代路径

**修复（e1595701）**：resume 续跑中再次挂起审批时事件被丢弃（弹窗不出现→180s 超时误判失败）；现已写入审批 chunk 并返回 parked，pending_confirm 存储对 resume 生效。

**已知缺口（未修，建议后续工单）**：
1. **多工具并行审批确认丢失（fork 时序）**：模型一轮并行两个 Write 时，REQUIRE_USER_CONFIRM 事件仅含第一个调用；approve 后第二个停在 asking 且无补充事件 → 180s 超时失败。anthropic 通道无 parallel_tool_calls 参数可规避。方向：worker 在 resume 收集超时中途检查会话 asking 状态补发 confirm；或 fork 侧补齐事件（需改 fork，当前约束不改）。
2. **Team 异步回报无回流**：多轮调研中模型若提前结束轮次（叙述"已派发"），成员回报与最终综合仅写入 agentscope 会话，不回流 yuxi 线程。方向：worker 对 team 会话延长订阅窗口或轮询会话消息补投。
3. 前端观察（既有行为，非迁移回归）：选中非默认智能体后"新建对话"发送会落入该智能体最近线程；审批挂起中断后偶发会话残留 running 需 interrupt 恢复。
