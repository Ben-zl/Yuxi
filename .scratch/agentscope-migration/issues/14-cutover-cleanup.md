# 14 — 切换与旧路径清除

**What to build:** 收口工单：验收门禁 checklist 全项通过后执行一刀切切换——默认链路切到 agentscope，移除 langchain/langgraph/deepagents 依赖与全部旧执行路径（不留兼容层），更新架构文档、changelog 与文档导航。门禁含：红线检查（审批挂起、取消、用户隔离、沙盒隔离）、存量线程只读行为、LITE 模式全链路、依赖可复现验收、`max_execution_steps`（300）与 `max_iters` 行为等价验证。

**Blocked by:** 03 — 05 — 06 — 07 — 08 — 09 — 10 — 11 — 12 — 13（全部已完成）

**Status:** blocked — 门禁评估完成，未过项阻塞破坏性切换（诚实记录，未执行切换）

- [ ] 门禁 checklist（spec 验收标准 + 迁移方案门禁）逐项通过并留证
- [ ] 红线检查：审批挂起、取消、用户隔离、沙盒隔离全部可用（任一失败即 NO-GO）
- [ ] 存量线程行为符合定义：只读展示、发送被拒并提示开新线程、不建空 Session
- [x] LITE 模式全链路验证：纯聊天可用、KB/图谱依赖不初始化（投影裁剪+工具不装配+不触发初始化，工单 03/07 断言）
- [x] 依赖可复现：lock 含固定 commit，干净环境构建成功（两次干净构建实证）
- [x] 长循环任务等价（配置面）：投影 react_config.max_iters=300 对齐旧 recursion_limit（集成断言）；真实 300 轮长循环行为在切换门禁以付费模型抽测
- [ ] 依赖树中不再有 langchain/langgraph/deepagents；旧执行路径代码删除
- [ ] ARCHITECTURE.md 智能体运行链路、changelog、docs 导航更新完成
- [ ] 切换后 docker compose 全链路 e2e 全绿

## Answer（2026-08-14 门禁评估：条件性 NO-GO）

### 已过项（证据）

1. **红线检查**：审批挂起（工单 10 e2e：park→批准续跑 1 passed）、取消（工单 10：挂起取消→interrupted）、用户隔离（工单 02：跨用户 404 + 映射隔离）、沙盒隔离（工单 06：按会话独立容器）——全部 e2e 实证。
2. **LITE 模式**：投影裁剪 knowledge_slugs=[]（工单 03 集成测试）、KB 工具不装配且不触发 KB 初始化（工单 07 单测）、纯聊天不受影响（全量回归含 LITE 断言）。
3. **依赖可复现**：uv.lock 固定 `agentscope 2.0.6 @ git+…?rev=ae6a563c`；镜像 `uv sync --no-cache --frozen` 干净构建两次成功（01/06）。
4. **300 步等价（配置面）**：投影携带 `react_config.max_iters = yuxi max_execution_steps（默认 300）`（工单 03 集成断言）；行为面等价需真实长循环任务，归入未过项 ⑥。
5. **新链路 e2e**：13 个测试文件 **38 passed**（docker compose 真实环境：PG/Redis/agentscope 服务/沙盒容器/MCP 服务器/mock 模型）。

### 补验（2026-08-14 晚）：③④ 已通过

- **③ 付费模型**：MiniMax-M3（Anthropic 兼容端点）以 ModelProvider 行接入（api_key 走 MINIMAX_API_KEY 环境变量，密钥仅存 worktree 本地 .env）；真实模型 e2e（test_agentscope_real_model_e2e.py，未配置密钥自动跳过）通过：投影（anthropic_credential+base_url）→ 流式回复非空 → **真实 usage total_tokens>0 聚合断言** → 历史持久化。
- **④ Langfuse**：本地 Docker 部署 v4 + OTLP 接线实证（见工单 12：AGENT/GENERATION span 入库、session 归属、鉴权 401/200）。

### 仍未过项（阻塞切换，GO 前必须补齐）

- **① 存量线程行为未实现**：网关侧「旧线程只读+拒发+提示开新线程」尚未编码（切换工单的一部分）。
- **② 网关翻转未执行**：`/api/agent/runs` 派发执行体仍为旧 LangGraph run_worker；arq job 替换为 `execute_run` 的接线、前端真实浏览器联调（零改动契约的最终裁判）未做。

- **⑤ workspace 网络接线**：MCP 在 workspace 容器内连接，默认 bridge 无 DNS；需 fork 给 DockerWorkspaceManager 加网络参数（或生产侧经可达地址发布 MCP）——e2e 以 bridge IP 绕过。
- **⑥ 工具面差额（Spec 评审补充）**：spec 声明 7 个 KB 工具，已交付 6 个（download_kb_file 顺延——产出区语义并入 workspace）；OCR/present_artifacts/Tavily web_search 未迁移；steer（旧栈提前结束语义）在工单 05 声明「由 11 重定义」但 11 未处理——四项均须在 GO 前补齐或正式降级记录。
- **⑦ 旧栈移除与文档**：依赖树仍含 langchain/langgraph（uv.lock 各 6 处，检查留证）；agents 模块/chat_service/run_worker 删除、旧 e2e 套件改造、ARCHITECTURE.md 重写未执行——依赖 ①②③④ 全部通过后才允许。

### 结论

**条件性 NO-GO（更新）**：技术链路已全量打通并实证——含付费模型（MiniMax-M3）与 Langfuse OTLP 真实上报，**38 tests green（含真实模型用例）**。剩余阻塞：①存量线程行为、②网关翻转与真实前端联调、⑤workspace 网络参数（fork）、⑥工具面差额（第 7 个 KB 工具/OCR/present_artifacts/steer/Tavily）、⑦旧栈移除与文档。下一步：实现 ①② 并以真实前端联调，fork 补 ⑤，补齐/降级 ⑥，随后分批执行 ⑦ 并复跑全量门禁（含付费模型用例）。
