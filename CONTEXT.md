# Yuxi 智能体域

Yuxi 是面向 RAG、知识图谱与多智能体的知识库平台。本文件固定智能体域的核心术语，作为产品、代码与迁移方案（agentscope 承接）的统一语言。

## Language

**Thread（线程）**:
用户与某个智能体的一段持续对话，承载历史与文件工作区的生命周期单位。
在 agentscope 平台层与其 Session 一一对应。
_Avoid_: 会话（对外产品语言统一用「线程」）

**AgentRun（运行）**:
一次用户请求触发的智能体执行实例；由提交即落库的运行事实与结束后的审计投影共同构成完整记录。
_Avoid_: 任务（任务专指可重复触发的 AgentTask 定义）、后台作业、Reply

**AgentTask（任务）**:
用户保存的、可通过手动、定时或 API 反复触发的智能体执行定义，可见范围为个人或部门，并具有独立的启停生命周期。
_Avoid_: 定时任务（定时只是触发方式）、后台任务、Tasker 任务

**TaskExecution（任务执行）**:
某个 AgentTask 被触发一次所形成的执行记录；排队时独立存在，派发后至多关联一个 AgentRun 与对应线程。
_Avoid_: AgentRun、任务实例、执行任务

**BackgroundJob（后台作业）**:
由 Tasker 承载的知识库解析、评估或图谱构建等平台内部异步作业。
_Avoid_: 任务、AgentTask、AgentRun

**任务队列（Task Queue）**:
同一 AgentTask 的全部手动、定时与 API 触发按 FIFO 共享的单一执行序列。
_Avoid_: 线程队列、调度队列、用户队列

**执行身份（Execution Principal）**:
一次任务执行实际使用其权限、凭据、环境变量与沙盒的用户身份；手动和 API 取触发者，定时触发取任务所有者。
_Avoid_: 任务身份、Agent 身份、创建者身份

**运行事实（Run Facts）**:
请求提交与执行过程中的持久状态，是排队互斥、幂等提交与崩溃恢复的依据，先于派发写入。
_Avoid_: 运行时状态（口语混用）

**工具审批（Tool Approval）**:
敏感工具执行前挂起 AgentRun、等待用户批准或拒绝的机制。
_Avoid_: HITL（口语，不入文档）

**子智能体（SubAgent）**:
管理员配置、可被主智能体调用的受管智能体；运行形态由 agentscope Agent Team 的 worker 承担。
_Avoid_: 队员、worker（产品语言统一用「子智能体」）

**线程队列（Queue）**:
同一线程的普通请求按 FIFO 串行派发的排队机制，含排队事件与拒绝策略，由网关层持有。
_Avoid_: 调度队列

**沙盒（Workspace）**:
智能体工具执行所在的隔离环境，持有线程级文件区（上传/工作区/产出）。
_Avoid_: sandbox（文档统一用「沙盒」）

**Skill**:
以 SKILL.md 为入口的扩展能力包，按需激活并携带工具与 MCP 依赖。
_Avoid_: 技能、插件
