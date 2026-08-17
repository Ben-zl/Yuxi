# ARCHITECTURE.md

本文档是 Yuxi 的代码地图，只描述相对稳定的系统边界、目录职责、核心运行链路和架构不变量。它用于帮助贡献者判断“一个改动应该落在哪里”，不替代具体模块文档、测试规范或源码注释。

修改不熟悉的模块前，先阅读对应章节，再使用符号搜索定位具体类型、函数和路由。开发与运行拓扑始终以 `docker-compose.yml` 为准。

## 鸟瞰

Yuxi 是一个面向 RAG、知识图谱和多智能体工作流的知识库平台。用户通过 Vue 前端管理智能体、知识库、模型、工具、Skills、MCP 与 SubAgents；前端通过 `/api` 调用 FastAPI；后端服务层协调 PostgreSQL、Redis、MinIO、Milvus、Neo4j、AgentScope 和隔离 workspace。

普通智能体请求先在 PostgreSQL 中保存为请求和消息，再立即派发或进入线程级 FIFO 队列。派发后的 `AgentRun` 通过 Redis/ARQ 交给独立 worker 执行，运行事件写入 Redis Stream，最终状态和业务记录写回 PostgreSQL，前端通过 SSE 消费排队与运行事件。

核心开发服务包括：

- `web-dev`：Vue 3 / Vite 前端，挂载 `web/src` 并热重载。
- `api-dev`：FastAPI API 服务，挂载 `backend/server`、`backend/package` 和测试目录并热重载。
- `worker-dev`：ARQ worker，执行已经派发的 AgentRun，并负责异常恢复扫描。
- `agentscope-dev`：持久化 AgentScope 会话、执行 ReAct/Team、装配服务端工具并管理每会话隔离 workspace。
- `sandbox-provisioner`：仅为远程 Skill 安装等一次性管理任务提供隔离沙盒，不承载 AgentScope 对话工具。
- `postgres`：业务数据、知识库元数据、请求队列、AgentRun，以及独立 `agentscope` database 中的 AgentScope 会话状态。
- `redis`：ARQ 投递、运行事件、取消信号以及跨进程配置和模型缓存。
- `minio`：附件、知识库原始文件和其他对象数据。
- `milvus`、`etcd`：向量检索及其元数据协调。
- `graph`：Neo4j 知识图谱。
- `mineru-api`、`paddlex`：通过 `all` profile 可选启动的文档解析和 OCR 服务。

## 后端代码地图

后端分成两个顶层边界：`backend/server` 是 Web 应用入口与 HTTP 适配层，`backend/package/yuxi` 是业务和基础设施主体。新增领域逻辑通常优先放在 `yuxi` 包中，路由层只处理请求模型、认证上下文和响应装配。

### Web 与 worker 入口

- `server/main.py` 创建 FastAPI 应用、注册中间件，并将业务路由统一挂载到 `/api`。
- `server/routers` 是 HTTP 路由边界，所有路由集中在 `server/routers/__init__.py` 注册。
- `server/utils/lifespan.py` 管理数据库、内置模型/MCP/Skills、知识库、Redis、沙盒和通用 Tasker 的启动与关闭。
- `server/agentscope_main.py` 创建 AgentScope 服务，配置持久存储、Redis message bus、workspace、逐轮工具和观测中间件。
- `server/worker_main.py` 是 ARQ worker 入口，实际执行设置位于 `yuxi.services.run_worker`。

`LITE_MODE` 下保留认证、智能体、聊天、Skills、MCP、模型、工作区和系统管理接口，但不注册 `external_kb`、`knowledge`、`evaluation` 和 `graph` 路由，也不初始化知识库管理器。

### `backend/package/yuxi`

- `agents` 保留智能体管理面的 backend 元数据、`BaseContext` 配置 Schema，以及 Skills/MCP/工具目录；不再包含独立执行图。实际执行适配集中在 `agentscope`。
- `agentscope` 负责配置投影、Thread↔Session 映射、AgentScope HTTP 客户端、事件协议转换、取消/审批恢复和 worker 执行。
- `services` 是用例层。智能体主链路重点分为请求接入与排队、Run 生命周期、运行时配置、worker 执行和 SubAgent 调用；聊天历史、附件、工作区、文件预览、评估、认证和观测等跨模块流程也从这里找入口。
- `repositories` 是 PostgreSQL 访问边界，封装业务对象、知识库元数据、AgentRun、请求队列、Task 和扩展配置查询。路由不应绕过 repository 直接拼装持久化逻辑。
- `storage/postgres` 管理 SQLAlchemy 模型与业务连接池。
- `storage/redis` 管理同步/异步 Redis 客户端和 ARQ 连接参数；业务 key、事件格式和缓存语义留在各自服务中。
- `storage/minio` 管理对象上传、下载和临时文件访问。
- `storage/neo4j` 管理共享 Neo4j Driver、生命周期和图查询辅助。
- `knowledge` 是知识库、文档解析、评估和图谱领域。`runtime.py` 暴露运行时知识库管理器；`implementations` 放 Milvus、Dify、Notion 和只读连接器；`parser` 统一封装 OCR/文档解析；`chunking` 管理分块策略；`graphs` 管理 Milvus 与 Neo4j 图谱能力。
- `models` 封装 chat、embedding 和 rerank 模型适配；`models/providers` 使用 PostgreSQL 保存模型供应商，并通过 Redis 缓存向 API 和 worker 提供一致视图。
- `config` 区分系统级配置和用户级配置。系统配置写入 `base.toml` 并同步 Redis 快照，用户配置保存在 PostgreSQL。
- `utils` 只放跨领域且足够通用的日志、时间、SSE 和轻量工具。

### 两类后台任务

项目中存在两套用途不同的后台执行机制，不应混用：

- AgentRun：通过 PostgreSQL 保存事实状态，使用 Redis/ARQ 投递到 `worker-dev`，支持运行事件、取消、恢复和线程请求队列。
- `services/task_service.py` 中的 Tasker：运行在 API 进程内，用于知识库解析、评估和图谱构建等通用后台任务；任务摘要持久化到 PostgreSQL，但可执行 coroutine 和内存队列不具备跨进程重建能力。

测试代码位于 `backend/test`，按 `unit`、`integration`、`e2e` 分层。新增或修改后端行为时，测试应放在最能覆盖真实风险的层级。

## 前端代码地图

前端是 Vue 3 + Vite 应用，业务入口集中在 `web/src`。

- `main.js` 挂载应用，`App.vue` 是根组件。
- `router` 定义公开首页、登录、智能体、工作区、智能体管理、扩展和仪表盘路由，并负责认证、管理员和超级管理员守卫。
- `apis` 是后端接口封装边界。新增接口应在这里定义，复用 `base.js` 的请求、鉴权和错误处理。
- `stores` 保存用户、智能体配置、主题和其他跨页面状态。
- `views` 是页面级入口，`components` 是可复用界面块。智能体对话的主要交互位于 `AgentChatComponent`，由 `AgentView` 负责页面组合。
- `composables` 封装请求排队、Run SSE、流式消息、审批、线程状态、提及和其他可组合逻辑。
- `utils` 放轻量转换和展示辅助；全局样式集中在 `assets/css`，颜色和基础规范优先复用 `base.css`。

`/` 是公开首页；登录后的核心工作区是 `/agent`。`/extensions` 对所有登录用户开放，其中 Skills 对普通用户可见，知识库、工具和 MCP 管理能力仅管理员可见；Dashboard 仅超级管理员可访问。后端权限检查始终是最终边界，前端守卫只负责页面体验。

## 智能体运行链路

一次普通智能体请求经过以下边界（agentscope 执行体，详见 `yuxi/agentscope/` 与迁移方案 docs/vibe/2026-08-14）：

1. `AgentView` 和 `AgentChatComponent` 收集文本、图片、附件、模型与审批配置。
2. `web/src/apis/agent_api.js` 调用 `POST /api/agent/runs`，由请求队列服务在同一数据库事务落库运行事实（排队、互斥、幂等），提交后才投递 ARQ。
3. `worker-dev` 的 `process_agent_run`（`yuxi/services/run_worker.py`）执行 `yuxi.agentscope.worker_job`：统一配置投影（`config_projection`）→ 线程会话保障（Thread↔Session 映射，存量线程按 cutover 时间戳只读拒发）→ `gateway` 协议转换把 AgentScope 事件流实时写入 `run:events:{run_id}`（复用既有 SSE 投递与断线续传）。
4. 会话执行在独立的 AgentScope 服务（`server/agentscope_main.py`）。`DockerWorkspaceManager` 以 session 为单位创建隔离 workspace；附件上传到 `/workspace/uploads`，文件与命令工具在 workspace 中执行。
5. 知识库、HTTP MCP、网页搜索、补充工具与按当前用户/session 隔离的 `AgentCreate` 模板，通过 `extra_agent_tools` 消费同一 `RuntimeProjection` 在每轮开始时装配。HTTP MCP 由 AgentScope 服务进程中的无状态 `MCPClient` 调用，真实 URL、Header 和凭据不会进入 workspace；stdio MCP 不进入该运行路径。
6. 审批挂起经 `REQUIRE_USER_CONFIRM` 映射为 `human_approval_required` chunk，resume 载荷转换为 AgentScope 确认或外部执行结果事件。
7. 执行结束：助手消息落回 Yuxi 消息表（前端历史视图数据源），AgentRun 终态与 token 用量回写，队头完成后派发下一条排队请求。前端消费 Request/Run SSE；断线经 `Last-Event-ID` 从 Redis Stream 续传。

审批或人机输入的 resume 请求仍经队列创建新 AgentRun，worker 将 decisions 载荷映射为审批恢复、文本回答映射为新输入。线程状态接口由 `ThreadStateService` 从 Conversation/Message、AgentRun、Redis 挂起事件和 workspace 产物聚合，不读取旧 checkpoint。

## 架构不变量

- Docker Compose 是开发环境的事实来源。开发时先检查容器、日志和热重载，不默认要求本地裸跑服务。
- HTTP 路由保持薄；用例流程放在 `yuxi.services`，持久化查询放在 `yuxi.repositories`。
- 请求接入与 Run 执行是两个阶段：先提交 PostgreSQL 事实，再投递 ARQ，不能让队列消息先于数据库状态可见。
- 同一用户、智能体和线程的普通请求通过 FIFO 队列串行派发；排队请求与运行中的 Run 使用不同状态模型和 SSE。
- PostgreSQL 保存业务事实状态；Redis 承担投递、事件、取消和缓存，不作为 AgentRun 最终状态的唯一来源。
- 前端 API 调用集中在 `web/src/apis`，组件不要散落拼接普通 HTTP 接口。
- 智能体配置由 context 管理，运行能力通过 AgentScope 配置投影、`extra_agent_tools`、Skills、MCP 和 workspace 组合；不要把知识库、沙盒或扩展逻辑硬编码进单个页面或路由。
- Skill 依赖工具只有在对应 Skill 激活后才对模型开放；基础工具与受 Skill 门控的工具要保持边界。
- LITE 模式必须允许跳过知识库、图谱和评估等重依赖能力，新增导入、路由和启动逻辑时要尊重该边界。
- 沙盒虚拟路径以 `SANDBOX_VIRTUAL_PATH_PREFIX` 为边界，用户可见路径、对象存储 URL 与宿主机真实路径不能混用。
- 面向用户和外部系统的输入在边界校验；内部服务优先依赖已有类型、事务和仓储约束，避免用静默回退掩盖设计错误。

## 跨切面关注点

- **配置**：Compose 和 `.env` 提供部署配置；管理员系统配置写入 `base.toml` 并通过 Redis 快照同步；用户配置与模型供应商以 PostgreSQL 为事实来源。
- **权限**：前端路由和页面标签提供体验级约束，FastAPI 认证依赖和 repository 可见性查询提供最终授权。
- **状态与存储**：Yuxi PostgreSQL 保存请求、Run、消息、业务和知识库元数据；AgentScope 使用独立 PostgreSQL database 保存 agent/session/message；Redis 保存短期事件、取消信号、审批挂起、ARQ 和跨进程缓存；MinIO、AgentScope workspace 与本地 `saves` 分别承载不同生命周期的文件。
- **文档处理**：上传文件先进入对象存储和文件元数据边界，再经过解析、分块和知识库实现；解析器、分块策略和知识库连接器保持可替换。
- **观测与调试**：优先查看 `api-dev`、`worker-dev` 和相关依赖日志；Langfuse 集中在服务层和 AgentRun 上下文；SSE 问题同时检查 Redis 事件与 PostgreSQL 终态。
