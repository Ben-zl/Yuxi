# WeKnora 部门级 workspace 隔离

状态：proposed
类型：feature
Owner：backend/package/yuxi/knowledge/weknora.py

> 本提案修订 [2026-09-09-weknora-knowledge-backend](../implemented/2026-09-09-weknora-knowledge-backend.md) 的"单空间与服务端统一 Key"决策；其余部分继续有效。

## 问题

当前实现把全部 Yuxi 托管库创建在部署 Key 所属的单一 WeKnora workspace：远端实测（kb.testplus.cn，2026-09-11）该 workspace 即 tenant 10005「质量中心」，与实例上既有 20 个业务库同空间。目标架构：每个部门一个独立 workspace，部门知识在 WeKnora 存储层即隔离，不与其他部门的库、更不与实例既有业务库混放。

2026-09-11 在远端实例实测的 workspace 能力（真实 HTTP 验证）：

| 验证点 | 结果 |
|---|---|
| `POST /tenants` 对 API Key 开放 | 通过：创建 workspace 即返回该空间专属新 API Key |
| workspace 间隔离 | 通过：新 Key 建库 `tenant_id=10035`；原管理 Key 跨空间读 403、列表互不可见 |
| 模型共享 | 通过：新 workspace 可用同一组系统级模型 ID（embedding/summary 等） |
| workspace 删除 | 受限：仅 Owner JWT（UI 登录）可删，API Key 403 |
| workspace Key 找回 | 受限：Key 仅创建时返回一次；`POST /tenants/{id}/api-keys` 远端 404，无法 API 补发 |
| 跨空间迁移库 | 无 API，不可行 |

## 提案

### 部门 workspace 自动开通

`WEKNORA_API_KEY` 语义收窄为**开通 Key**：仅用于创建 workspace，不用于任何知识库操作。部门首个托管库创建且尚无该部门映射时，服务端 `POST /tenants`（空间名取部门名），用返回的专属 Key 立即自检（该 Key `GET /knowledge-bases` 返回 200），通过后持久化映射 `{department_id → workspace_tenant_id, encrypted_api_key}`，再执行建库。已有映射直接复用，开通幂等。

### 专属 Key 加密落库

新增部署项 `WEKNORA_WORKSPACE_CREDENTIAL_KEY`（Fernet key，模式同 `AGENTSCOPE_CHANNEL_CREDENTIAL_KEY`）。新增部门 workspace 映射表：`department_id` 唯一、`workspace_tenant_id`、`encrypted_api_key` 密文、状态、时间戳。明文仅存在于服务端出站请求，不进日志、API 响应与错误体（沿用 `repr=False` 与脱敏纪律）。

### 绑定与取键模型

`remote_binding` 增加 `workspace_tenant_id`；绑定校验从"实例指纹一致"升级为"实例指纹 + workspace 一致"。全链路（建库、上传、状态同步、检索、预览、下载、重解析、文档编辑、删除）按 `KB → owning_department_id → 映射行 → 专属 Key` 解析出站凭据；映射缺失或解密失败时操作明确失败，不回退用开通 Key。

### 故障与生命周期语义

- 开通结果不确定（远端已建、本地落库失败）：workspace 残留且 API 不可删，记录待核对状态并提示人工 UI 清理；重新开通会再建新空间，残留空间不自动复用（开通 Key 无跨空间可见性，无法按名找回）。
- 映射行不自动删除：删除部门最后一个库或部门本身时，workspace 与映射保留并标记孤儿，清理由管理员在 WeKnora UI 执行。
- 凭据 Fernet Key 轮换：需先以旧 Key 解密、新 Key 重加密全表（提供运维脚本），轮换完成前不删旧 Key。
- 归属部门固定不可迁移（沿用既有决策），从根上规避跨 workspace 迁移库的需求。

### 不变项与既有绑定处理

模型 ID 仍为全局部署配置（远端模型系统级共享已实测）；fail-closed、`can_write_content` 权限矩阵、写操作不自动重试、状态映射、两套数据隔离等 2026-09-09 决策的其余部分全部保留。本特性未发布到 main，无生产绑定；开发环境既有 weknora 测试绑定直接删除重建，不做兼容层或迁移。

## 替代方案

- 人工预置 workspace 并逐部门配置 Key：可行但每新增部门多一步人工，Key 经人工渠道传递；已选自动开通，拒绝。
- 保持单空间 + Yuxi 侧隔离：远端存储层各部门库与实例既有库混放同一空间，与"每部门独立 workspace"目标不符；修订原决策，拒绝。
- 开通 Key 兼作部门操作 Key（不建子空间）：等价于单空间，拒绝。
- 跨空间迁移/找回工具：远端无对应 API，无法实现；以"归属部门固定 + 映射行不删"规避，拒绝。

## 验收标准

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 远端支持 API Key 开通 workspace 并返回专属 Key | POST 失败或响应缺 Key | yuxi.services.weknora_kb_service | 2026-09-11 实测 kb.testplus.cn `POST /tenants` 201,响应含新 Key | fork 403 行为差异须显式报错 | Passed |
| 部门空间在远端层互相隔离 | 跨空间读写未拒绝 | WeKnora 实例 | 专属 Key 建库 tenant_id=10035;开通 Key 直查该库 403(实测) | 以 A 部门 Key 查 B 部门库须 403 | Passed |
| 模型跨空间共享,建库模型配置不变 | 新空间缺模型致建库失败 | 部署配置 | 新空间 `GET /models` 返回同一组模型 ID(实测) | 模型 ID 错配时建库 4xx | Passed |
| 双部门自动开通与全链路 E2E | 开通/建库/上传/检索/删除任一环失败 | yuxi.services 全链路 | 实施后:双部门各自开通建库,上传至 completed,检索命中,删除双侧干净 | 重复建库须复用既有映射不重复开通 | Not run |
| 开通故障保留待核对 | 远端已建但本地落库失败 | yuxi.services.weknora_kb_service | 实施后:故障注入(截断响应/DB 失败)后映射处于待核对且可人工处置 | 不得自动重复开通 | Not run |
| 部门 Key 解析负向 | 映射缺失/密文损坏时回退开通 Key | yuxi.knowledge.weknora 客户端 | 实施后:删除映射行/损坏密文后操作明确失败 | 任何路径不得用开通 Key 操作部门库 | Not run |
| 内置模式回归零变化 | builtin 行为被改动 | 知识域整体 | 实施后:unit 全量对齐基线 + builtin 路径抽查 | `KNOWLEDGE_BACKEND=builtin` 语义不变 | Not run |

## 风险

- 本地 fork（qaclaw-main）`POST /tenants` 对 API Key 403（实测），与远端部署不一致：本地 E2E 无法走自动开通链路，需先对齐 fork 的 tenant API 行为，或实施期验证依赖远端实例。
- workspace Key 不可找回：映射行或凭据密文丢失后，该部门旧 workspace 对 Yuxi 永久不可达且库无法迁移，只能开通新空间重建内容；映射表与凭据 Fernet Key 的备份是硬运维要求。
- 远端残留 workspace：误开通、部门裁撤、开通结果不确定均会留下 API 不可删除的空间（本轮验证已残留一个空空间 10035「探测-Yuxi部门空间可行性」待 UI 清理），产品语义需向管理员说明。
- 开通 Key 权限集中：能调 `POST /tenants` 的 Key 可无限开通空间，部署侧应妥善保管；本提案不引入远端配额管理。
