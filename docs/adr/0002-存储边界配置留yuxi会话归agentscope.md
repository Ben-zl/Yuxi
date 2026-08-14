# 0002 - 存储边界：配置域与运行事实留 yuxi，会话域归 agentscope

Agent/Skill/MCP/模型供应商继续以 yuxi PostgreSQL 为事实源（管理界面与权限体系不动），运行时经**统一配置投影**（单一模块职责）构造 agentscope 对象；线程会话与运行状态写入 agentscope 存储，指向同一 PostgreSQL 实例的**独立 database**（其 SQL backend 无 schema 级隔离参数）；实时消息走 Redis message bus——agentscope 将 storage 与 bus 刻意解耦，不可混写为"Redis storage"。

AgentRun 拆为两层：**运行事实**（提交即落库 yuxi 库，是排队互斥、幂等提交、挂起互斥与崩溃恢复的依据，先于派发写入）与**审计投影**（结束后从 agentscope session/run 事实派生）。

## Considered Options

- PostgreSQL 独立 schema 作边界（被否：其存储构造器无一等 schema 参数，只有 URL 级 database 边界）。
- AgentRun 整体降为结束后投影（被否：FIFO、挂起互斥、幂等提交与崩溃恢复都依赖运行前/运行中的持久事实）。
- 全量迁入 agentscope 的 agents/skills/mcps/credentials 表（被否：现有管理界面、权限与内置模型同步逻辑全部重写）。
