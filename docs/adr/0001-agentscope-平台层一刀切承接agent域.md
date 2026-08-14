# 0001 - agent 域一刀切迁移到 agentscope 平台层

为摆脱 langchain/langgraph/deepagents 生态并获取 agentscope 2.x 的权限、长期记忆、多沙盒与 Agent Team 能力，yuxi 的智能体域（运行时与 run 管道）一刀切迁移到本地 fork `Ben-zl/agentscope`，以 git 依赖固定到 commit `ae6a563c`（版本 2.0.6，经 lock 固定，容器构建可复现），不走双栈渐进。api 容器保留为网关（认证、线程队列重建、SSE 协议转换，前端零改动），worker 容器改为运行 agentscope service（PostgreSQL 独立 database 作持久存储、Redis 作 message bus，两者解耦）；子智能体由 Agent Team 承接，工具审批挂起与取消为不可降级红线，用量归集口径与子智能体排队串行为可降级项（须书面记录）；沙盒换原生 workspace（Docker 自管），Skills 激活采用其渐进披露机制，观测走 TracingMiddleware → OTel → Langfuse。存量 LangGraph checkpoint 历史不迁移：旧线程只读展示，再次发送被网关拒绝并提示开新线程，不建立空 Session 续聊。

## Considered Options

- SDK 层内嵌 + backend_id 双栈渐进（被否：双栈过渡期成本高，且动机本就是全量摆脱 langchain 生态）。
- 保留 SubAgent 的 AgentRun 语义（被否：目标形态即 Team 动态组队）。

## Consequences

- 切换日无回退路径，验收必须前移到切换前完成。
- 依赖个人 fork 的本地版本，上游同步与 API 漂移是长期负担。
