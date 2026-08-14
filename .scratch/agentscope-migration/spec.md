# Spec: agentscope 承接 agent 域

Status: ready-for-agent
Created: 2026-08-14 · Revised: 2026-08-14（评审 NO-GO 意见修订：运行事实断层、测试分层、LITE_MODE、依赖固定、配置投影闭环、存量线程行为、思考块、依赖边、存储/bus 职责、降级红线）
References: [ADR-0001](../../docs/adr/0001-agentscope-平台层一刀切承接agent域.md) · [ADR-0002](../../docs/adr/0002-存储边界配置留yuxi会话归agentscope.md) · [术语表](../../CONTEXT.md) · [迁移方案](../../docs/vibe/2026-08-14-agentscope-迁移方案.md)

## Problem Statement

Yuxi 的智能体域运行在 langchain/langgraph/deepagents 生态上，升级节奏与 breaking change（如流协议 v3 变化）被上游牵引，且缺少原生权限模式、长期记忆、多沙盒后端与动态组队能力。用户（终端用户与管理员）需要一个行为不变、但建立在 agentscope 平台之上的智能体域，使产品演进不再受 langchain 生态约束。

## Solution

用 agentscope（本地 fork `Ben-zl/agentscope`，git 依赖固定 commit `ae6a563c`、版本 2.0.6）的 SDK 与平台层一刀切替换智能体运行时与运行管道：线程（Thread）与 agentscope Session 一一对应；api 网关保留并承接认证、线程队列与 SSE 协议转换，**前端零改动**；子智能体由 Agent Team 动态组队承担；沙盒换原生 workspace（Docker 自管）；配置域（Agent/Skill/MCP/模型供应商）仍以 yuxi 库为事实源、经统一配置投影进入运行时；会话域归 agentscope 存储（同一 PostgreSQL 实例的独立 database），实时消息走 Redis message bus（storage 与 bus 解耦）；AgentRun 的运行事实提交即落库 yuxi 库，审计投影结束后派生；存量 checkpoint 数据不迁移。

## User Stories

1. As a 终端用户, I want 在智能体对话中收到与迁移前一致的流式回答（文本增量、思考块增量、工具调用事件）, so that 迁移对我完全无感。
2. As a 终端用户, I want 在同一线程连续发送消息时第二条请求进入排队并看到实时排队状态, so that 线程内请求串行且我知道何时执行。
3. As a 终端用户, I want 排队请求在队头运行结束后自动派发或按策略被拒绝, so that 我不需要手动重试。
4. As a 终端用户, I want 智能体调用敏感工具（写文件/编辑/执行命令）时收到工具审批请求并支持批准/拒绝, so that 风险操作始终受我控制。
5. As a 终端用户, I want 智能体向我提问（ask_user_question）时对话挂起等待我的回答并从中恢复, so that 人机协作链路不中断。
6. As a 终端用户, I want 随时中断正在运行的对话, so that 错误请求能及时止损。
7. As a 终端用户, I want 断线重连后恢复当前运行的状态流与终态补偿, so that 网络抖动不丢结果。
8. As a 终端用户, I want 我上传的附件出现在沙盒的线程文件区并可被智能体读取, so that 文件问答与加工照常可用。
9. As a 终端用户, I want 智能体生成的文件出现在产出区并支持预览与下载, so that 成果可取回。
10. As a 终端用户, I want 主智能体把子任务交给子智能体（Team worker）执行并把结果合并回对话, so that 复杂任务被分解完成。
11. As a 终端用户, I want 我的线程与文件区按用户隔离, so that 数据不被他人访问。
12. As a 终端用户, I want 对话历史随线程持久化并在刷新后继续, so that 上下文不丢失。
13. As a 终端用户, I want 知识库检索工具在对话中照常可调, so that RAG 能力不因迁移回退。
14. As a 终端用户, I want 迁移前的旧线程仍可浏览历史、但发送新消息时得到明确提示并引导开新线程, so that 旧记录不消失也不会产生残缺的新会话。
15. As a 管理员, I want 模型供应商配置继续在现有管理界面维护并被运行时正确使用, so that 管理方式不变。
16. As a 管理员, I want Skills 以渐进披露机制生效且 yuxi Skill 表为唯一配置来源, so that Skill 管理不出现双头。
17. As a 管理员, I want MCP 服务器配置继续由现有界面管理、工具按需对智能体开放, so that 扩展能力接入不变。
18. As a 管理员, I want 用户仅能添加 sse/streamable-http MCP、stdio 走内置白名单的约束保留, so that 安全边界不放松。
19. As a 管理员, I want 我配置的子智能体以 Team worker 模板参与组队, so that 受管子智能体语义延续。
20. As a 管理员, I want 工具审批策略（默认审批/始终信任）语义保留, so that 审批治理不变。
21. As a 管理员, I want token 与工具用量归集到用户与线程账目（或获得明确的降级结论）, so that 计量与 Dashboard 不失明。
22. As a 超级管理员, I want 在 Langfuse 通过 OTLP 看到主链路 trace, so that 可观测性延续。
23. As a 开发者, I want 迁移后的运行链路在 docker compose 中由 e2e 套件全绿验证, so that 一刀切切换有客观门禁。
24. As a 开发者, I want 沙盒按线程隔离且生命周期由 workspace 子系统管理, so that 隔离边界可验证、资源可回收。
25. As a 开发者, I want 会话数据落在独立 database、与业务库边界清晰, so that 存储职责不混淆。
26. As a 开发者, I want 依赖只来自固定 commit 的 fork 且容器构建可复现, so that 版本可控、升级显式。
27. As a 开发者, I want LITE 模式下纯聊天可用且知识库/图谱依赖不初始化, so that 轻量部署边界保留。
28. As a 开发者, I want 切换后 langchain/langgraph/deepagents 依赖与旧执行路径被彻底移除, so that 不留兼容层。

## Implementation Decisions

- **依赖固定**：fork 以 git 依赖固定 commit `ae6a563c`（2.0.6）写入后端依赖清单并经 lock 锁定；容器在干净环境按 lock 构建可复现（compose 构建上下文不含兄弟仓库，不得以本地路径作正式依赖）。
- **架构形态**：api 容器保留为网关（认证、线程 FIFO 队列与排队 SSE 重建、事件协议转换）；worker 容器改为运行 agentscope service；Thread ↔ Session 一一对应。
- **存储与通道职责**：持久存储 = agentscope SQL backend 指向同一 PostgreSQL 实例的**独立 database**（其存储无一等 schema 参数，schema 级隔离不可行）；实时消息 = Redis message bus；两者按 agentscope 设计解耦，不混用。
- **运行事实与审计投影分离**：请求提交即在 yuxi 库写入运行事实（排队状态、互斥、幂等键、恢复依据），先于向 agentscope 派发；FIFO、挂起互斥（interrupted/steer）、reject 策略与崩溃恢复全部依据运行事实；审计投影在结束后从 agentscope session/run 事实派生，不承担运行期职责。
- **统一配置投影**：单一模块负责把 yuxi 库的 Agent 配置、模型供应商与凭据、Skill、MCP、子智能体模板完整投影为 agentscope 运行时对象（其 service 从自身 StorageBase 解析 Agent 与 credential，投影必须闭合覆盖该解析面）；LITE 模式下投影裁剪知识库相关部分且不初始化其依赖。
- **前端契约冻结**：现有 SSE chunk 协议（初始化/排队/流式增量含思考块/工具调用/审批挂起/中断/终态）为冻结契约，转换层建在网关，前端不改。
- **子智能体红线**：Agent Team 动态组队；工具审批挂起、取消为**不可降级红线**（切换门禁强制）；用量归集口径、子智能体排队串行为**可降级项**（降级须书面记录结论）。
- **沙盒**：agentscope 原生 workspace，Docker 后端自管；线程级隔离与生命周期；附件、产出、Skill 文件的路径语义在新 workspace 中重新映射。
- **Skills**：采用 agentscope 渐进披露机制；yuxi Skill 表为唯一源，依赖工具/MCP 声明随之映射。
- **知识库域**：不改造；7 个知识库工具重写为 agentscope 工具，可见性权限边界与现有语义一致；LITE 模式不装配。
- **观测**：TracingMiddleware → OpenTelemetry → Langfuse OTLP 接入。
- **存量线程行为**：旧线程只读展示（历史读现有消息表），再次发送被网关拒绝并提示开新线程；不建立空 Session 续聊。
- **切换**：一次性切换，无双栈、无回退；切换前完成验收门禁；切换后移除旧依赖与旧执行路径。

## Testing Decisions

- **好测试的标准**：只测外部可观察行为——协议等价、构造结果、投影事实；不测内部实现细节。遵守仓库测试分层规范（unit / integration / e2e 目录与命名）。
- **Seam 1（主切面，e2e）**：网关 SSE/HTTP 契约。流式（含思考块）、工具调用、审批挂起/恢复、提问、取消、排队与拒绝、断线重连、终态补偿、旧线程只读行为，全部穿过该切面黑盒验证（docker compose 环境）。Team、沙盒、知识库工具行为只在此切面验证。
- **Seam 2（窄切面）**：配置投影闭环——统一库夹具（模型供应商/凭据/Agent/Skill/MCP/子智能体模板，含 LITE 裁剪用例）断言完整运行时对象构造，作为**集成测试统一入口**，各功能工单不得另起炉灶。
- **Seam 3（窄切面）**：运行事实与审计投影——队列并发/崩溃恢复集成测试（对运行事实与真实存储）、审批状态迁移集成测试、审计投影事实断言（对真实 PostgreSQL）。
- **协议转换单测**：agentscope 事件夹具 → chunk 协议输出逐类断言（含思考块增量、错误、终态），不依赖真实模型。

## Out of Scope

- 存量 LangGraph checkpoint 历史迁移与旧线程续聊（行为已定义为只读+拒发）。
- 前端任何改动（协议冻结为前提）。
- 知识库域、评估、图谱子系统的改造。
- 管理界面（Agent/Skills/MCP/模型）的行为变化。
- agentscope fork 与上游的同步策略。
- 双栈并存、灰度或回退机制（一刀切）。
- Langfuse 之外的观测体系迁移。

## Further Notes

- Team 之上黄线项（用量口径、排队串行）的降级结论在子智能体工单产出并回写本 spec；红线项（审批挂起、取消）不进入降级讨论，切换门禁强制检查。

### Team 黄线结论（工单 11 · 2026-08-14）

- **审批挂起（红线）**：保留。worker 会话与 leader 共用同一权限引擎；worker 的敏感工具挂起经 SessionProjection 把 HITL 卡片投射到 leader 会话（agentscope 内建），取消通道同一 interrupt 机制（工单 10 已验证该引擎）。
- **取消（红线）**：保留。worker 会话与普通会话同构，interrupt_session 幂等可用。
- **用量归集（黄线·降级）**：Team worker 用量分散在各 worker 会话的 Msg.usage/trace 中，不再聚合为父 Run 的单一账目；Dashboard 汇总口径切换为「按用户/线程聚合所有会话」，父 Run 归集口径不迁移（旧栈 TokenUsageMiddleware 分桶语义废弃）。
- **排队串行（黄线·降级）**：子智能体不再经过 yuxi 线程队列（旧栈 task/subagent_start 语义废弃），由 agentscope 会话锁与 inbox/wakeup 调度；同线程互斥语义由「leader 挂起互斥 + worker 会话锁」承担。
- `max_execution_steps`（300）与 `ReActConfig.max_iters` 的行为等价性在切换门禁验证。
- fork 升级走显式 bump：更新 commit、重跑配置投影与协议转换测试。
- 实施顺序与切换门禁 checklist 见迁移方案文档（docs/vibe/2026-08-14）。
