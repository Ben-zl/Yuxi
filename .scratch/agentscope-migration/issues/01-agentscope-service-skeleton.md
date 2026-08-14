# 01 — 骨架：worker 容器运行 agentscope service

**What to build:** fork 以 git 依赖固定 commit `ae6a563c`（2.0.6）进入后端依赖清单并经 lock 锁定（compose 构建上下文不含兄弟仓库，不得用本地路径作正式依赖）；worker 容器改为运行 agentscope service 骨架：持久存储指向同一 PostgreSQL 实例的**独立 database**（SQL backend，无 schema 级隔离），实时消息 bus 走 Redis（与 storage 解耦）。完成后 service 健康可见，创建并读取一个 session，数据落在独立 database。

**Blocked by:** None — can start immediately.

**Status:** resolved

- [x] 依赖清单含固定 commit 的 git 依赖，lock 锁定；干净环境按 lock 构建容器成功（可复现）
- [x] worker 容器内 agentscope service 启动并通过健康检查
- [x] 通过 service API 创建/读取 session，事实写入 PostgreSQL 独立 database（与业务库边界清晰）
- [x] Redis message bus 连通（组件健康、无连接错误）；跨进程会话锁的运行时行为随工单 02 chat 链路验证
- [x] agentscope 服务与主栈 postgres/redis 在同一 compose 网络共存无冲突（api/worker 容器级同拓扑共存待主栈整体启动后复核，切换工单收口）

## Answer（2026-08-14 验证记录）

- 镜像构建：`uv sync --no-cache --frozen` 干净环境成功；lock 中 `agentscope 2.0.6 @ git+…?rev=ae6a563c`。
- 运行：`agentscope-dev` 容器（worktree 项目 `yuxi-agentscope`，复用主栈网络与 PG/Redis 实例）；`GET /health` 200，storage/message_bus/workspace_manager 等全部 ok。
- 存储：同实例并存 `yuxi` 与 `agentscope` 两个 database；agents/credentials/sessions/teams 等表建于 `agentscope` 库；API 创建的 session 行落库核对一致。
- 测试：`test/e2e/test_agentscope_service_skeleton.py` 2 passed（健康 + agent/session 创建读取回路）。
- 实现备注：uvicorn `--reload` 在事件循环内导入模块，建库引导放独立线程执行（`backend/server/agentscope_main.py`）；worktree 本地用 `docker-compose.override.yml` 复用主栈基础设施（该文件不入库）。
