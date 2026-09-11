# WeKnora 部门级 workspace 隔离

状态：implemented
类型：feature
Owner：backend/package/yuxi/services/weknora_workspace_service.py

> 本决策修订 [2026-09-09-weknora-knowledge-backend](2026-09-09-weknora-knowledge-backend.md) 的"单空间与服务端统一 Key"决策；其余部分继续有效。

## 问题

初版实现把全部 Yuxi 托管库创建在部署 Key 所属的单一 WeKnora workspace：远端实测（kb.testplus.cn，2026-09-11）该 workspace 即 tenant 10005「质量中心」，与实例上既有业务库同空间。目标架构：每个部门一个独立 workspace，部门知识在 WeKnora 存储层即隔离，不与其他部门的库、更不与实例既有业务库混放。

2026-09-11 在远端实例实测的 workspace 能力（真实 HTTP 验证）：

| 验证点 | 结果 |
|---|---|
| `POST /tenants` 对 API Key 开放 | 通过：创建 workspace 即返回该空间专属新 API Key |
| workspace 间隔离 | 通过：新 Key 建库 `tenant_id=10035`；原管理 Key 跨空间读 403、列表互不可见 |
| 模型共享 | 通过：新 workspace 可用同一组系统级模型 ID（embedding/summary 等） |
| workspace 删除 | 受限：仅 Owner JWT（UI 登录）可删，API Key 403 |
| workspace Key 找回 | 受限：Key 仅创建时返回一次；`POST /tenants/{id}/api-keys` 远端 404，无法 API 补发 |
| 跨空间迁移库 | 无 API，不可行 |

## 决策

### 部门 workspace 自动开通

`WEKNORA_API_KEY` 语义收窄为**开通 Key**：仅用于创建 workspace（`POST /tenants`），不用于任何知识库操作。部门首个托管库创建且尚无该部门映射时，服务端以部门名创建 workspace，用返回的专属 Key 立即自检（该 Key `GET /knowledge-bases` 返回 200），通过后持久化映射，再执行建库。已有当前实例的 confirmed 映射直接复用（幂等）；映射处于待核对或实例不符时重新开通，旧空间记录残留告警。

### 专属 Key 加密落库

部署项 `WEKNORA_WORKSPACE_CREDENTIAL_KEY`（Fernet key，模式同 `AGENTSCOPE_CHANNEL_CREDENTIAL_KEY`，weknora 模式必需配置）。映射表 `weknora_department_workspaces`：`department_id` 唯一、`workspace_tenant_id`、`encrypted_api_key` 密文、实例指纹、状态、时间戳。明文仅存在于服务端出站请求，不进日志、API 响应与错误体。schema v4 以幂等 DDL 落地。

### 绑定与取键模型

`remote_binding` 携带 `workspace_tenant_id`；绑定校验为"实例指纹 + workspace 一致"（`load_confirmed_binding` 收敛），部门重新开通后旧绑定立即失效并提示人工核对；旧版单空间绑定（无 workspace 字段）显式要求删除重建，不静默兼容。全链路（建库、上传、状态同步、检索、预览、下载、重解析、文档编辑、删除）按 `KB → owning_department_id → 映射行 → 专属 Key` 解析出站凭据（执行器经 `_department_client`，服务层经 `load_confirmed_binding`/`_client_for_kb`）；映射缺失、状态待核对、实例不符或解密失败均明确失败，任何路径不回退用开通 Key。

### 故障与生命周期语义

- 开通响应缺 id/api_key：明确报错并给出远端空间标识供人工核对，不落映射。
- 自检失败：以已知 tenant_id 与密文落 `pending_review` 映射后报错，不自动复用。
- 并发开通：department_id 唯一约束收敛，后落库分支复用先到映射并告警残留空间。
- 映射行不自动删除：删除部门最后一个库或部门本身时，workspace 与映射保留（远端 workspace 仅 Owner JWT 可删，API 不可回收）。
- 凭据 Fernet Key 轮换：需先以旧 Key 解密、新 Key 重加密全表，轮换完成前不删旧 Key。
- 归属部门固定不可迁移（沿用既有决策），从根上规避跨 workspace 迁移库的需求。

### 不变项与既有绑定处理

模型 ID 仍为全局部署配置（远端模型系统级共享已实测）；fail-closed、`can_write_content` 权限矩阵、写操作不自动重试、状态映射、两套数据隔离等 2026-09-09 决策的其余部分全部保留。本特性未发布，开发环境旧版单空间绑定直接删除重建，不做兼容层或迁移。

## 替代方案

- 人工预置 workspace 并逐部门配置 Key：每新增部门多一步人工，Key 经人工渠道传递；已选自动开通，拒绝。
- 保持单空间 + Yuxi 侧隔离：远端存储层各部门库与实例既有库混放同一空间，与"每部门独立 workspace"目标不符；修订原决策，拒绝。
- 开通 Key 兼作部门操作 Key（不建子空间）：等价于单空间，拒绝。
- 跨空间迁移/找回工具：远端无对应 API，无法实现；以"归属部门固定 + 映射行不删"规避，拒绝。

## 后果

新增部门 workspace 映射表与 schema v4 幂等迁移；weknora 模式必需配置新增 `WEKNORA_WORKSPACE_CREDENTIAL_KEY`（缺失即 fail-closed）；建库、文档、检索、代理下载全链路的出站凭据改为按部门解析；远端会随部门开通产生仅能 UI 删除的 workspace（误开通/部门裁撤同样残留），且部门专属 Key 不可找回、旧库不可跨空间迁移——映射表与凭据 Fernet Key 的备份成为硬运维要求；本地 fork 的 tenant API 与远端行为不一致，本地栈的自动开通链路依赖远端实例验证或先对齐 fork。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 部门首个库创建自动开通 workspace 并返回专属 Key | POST 失败或响应缺 Key | weknora_workspace_service | E2E:研发一部→tenant 10036、研发二部→10037,密文 164 字符落库 | 响应缺 api_key 报"人工核对"(单测) | Passed |
| 部门空间在远端层互相隔离 | 跨空间读写未拒绝 | WeKnora 实例 | 部门 2 Key→本部门库 200,→部门 3 库 403;部门 3 Key→部门 2 库 403(实测) | — | Passed |
| 模型跨空间共享,建库模型配置不变 | 新空间缺模型致建库失败 | 部署配置 | 两部门空间建库均用同一组模型 ID(实测) | — | Passed |
| 双部门自动开通与全链路 E2E | 开通/建库/上传/检索/删除任一环失败 | yuxi.services 全链路 | E2E:开通→建库 confirmed→上传 completed→检索命中(部门专属 Key)→预览/下载(166B 原件)→手工文档 completed→标题修订→删文档→删库双侧干净;第二库复用同空间(映射数恒 2) | 跨部门成员 403(实测) | Passed |
| 开通故障保留待核对 | 远端已建但自检/落库失败 | weknora_workspace_service | 单测:自检失败落 pending_review;并发冲突复用先到映射 | 不得自动重复开通 | Passed |
| 部门 Key 解析负向 | 映射缺失/密文损坏时回退开通 Key | weknora 客户端链路 | 单测:未开通/待核对/实例不符/解密失败四类均明确失败 | 任何路径不得用开通 Key 操作部门库 | Passed |
| 内置模式回归零变化 | builtin 行为被改动 | 知识域整体 | `pytest test/unit -m "not slow"`:69 失败均为容器环境性基线(compose 守卫/宿主路径),知识域与 weknora 域 258 用例全绿 | `KNOWLEDGE_BACKEND=builtin` 语义不变 | Passed |

## 风险

- 本地 fork（qaclaw-main）`POST /tenants` 对 API Key 403（实测），与远端部署不一致：本地栈无法走自动开通链路，本地 E2E 依赖远端实例或先对齐 fork 的 tenant API。
- workspace Key 不可找回：映射行或凭据密文丢失后，该部门旧 workspace 对 Yuxi 永久不可达且库无法迁移，只能开通新空间重建内容；映射表与凭据 Fernet Key 的备份是硬运维要求。
- 远端残留 workspace：远端现存在本轮验证产生的部门空间 10036「研发一部」、10037「研发二部」（映射保留可复用）及早期探测空间 10035「探测-Yuxi部门空间可行性」（待 UI 清理）；误开通、部门裁撤同样会留下仅能 UI 删除的空间，产品语义需向管理员说明。
- 开通 Key 权限集中：能调 `POST /tenants` 的 Key 可无限开通空间，部署侧应妥善保管；本决策不引入远端配额管理。
