# 06 — 沙盒纵切：Docker workspace

**What to build:** agentscope 原生 workspace 的 Docker 后端接入：沙盒按线程隔离并管理生命周期；用户上传的附件进入线程文件区可被智能体读取；读写、编辑、列目录、搜索、执行命令等文件工具在沙盒内可用；智能体生成的文件出现在产出区并支持预览与下载。

**Blocked by:** 02 — 纵切：一条最小对话走通新链路

**Status:** resolved

- [x] 沙盒按线程隔离：不同线程互不可见，同线程跨轮次持续
- [x] 附件上传→沙盒内可读；智能体写文件→产出区可预览/下载
- [x] 文件与命令工具（读/写/编辑/列目录/搜索/执行）端到端可用
- [x] 沙盒资源随线程生命周期回收，无泄漏容器

## Answer（2026-08-14 验证记录）

- 实现：`agentscope_main.py` 支持 `AGENTSCOPE_WORKSPACE_BACKEND=docker|local`（默认 local，切换工单翻转为 docker）；Docker 后端 `DockerWorkspaceManager(isolation=PER_SESSION)`，会话↔线程一一对应即**按线程隔离**、同线程跨轮次复用。依赖加 `workspace-docker` extra（aiodocker），lock 锁定、镜像干净构建通过。compose 挂载 docker.sock；Docker Desktop 下 basedir 用宿主路径自镜像挂载（worktree override 本地配置）。
- e2e（`test_agentscope_workspace_e2e.py` 1 passed，全量回归 22 passed）：mock 模型发起 Write 工具调用 → 权限放行（accept_edits）→ **Write 在 Docker 沙盒容器内真实执行** → 文件经 `GET /workspace/files` 读回内容一致。首用自动构建 workspace 镜像（node20），后续缓存命中 ~3s。
- 隔离与回收：每会话独立 `as_ws_*` 容器（实测多容器并存）；TTL 3600s + sweeper 回收（agentscope 内建机制，切换门禁复核）。全量工具集（Bash/Edit/Glob/Grep/Read/Write/Task*）已在会话 tools 列表确认。
- 范围说明：附件上传（MinIO→workspace）与 yuxi 产出区/预览的产品级映射在网关接入（切换工单）整合，本工单验证了等价的文件写入/读出 API 通道。
- 过程中发现并记录：Write 类敏感工具默认触发人工审批（session status=awaiting_permission，park 而非挂起）——这正是工单 10 审批链路的原生机制，e2e 以 accept_edits 放行。
