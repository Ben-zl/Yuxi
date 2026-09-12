# 模型供应商与 MCP 部门共享

状态：implemented
类型：feature
Owner：backend/package/yuxi/permissions/resource_permission.py

## 问题
模型供应商和 MCP 原先以全局唯一的 `provider_id`/`slug` 管理，无法支持部门内独立凭据的同名资源；运行时、缓存和管理 API 也存在按旧逻辑标识解析而绕过可见性授权的风险。

## 决策
模型供应商和 MCP 使用全局唯一、不可变的 `resource_id` 作为引用身份；`provider_id` 和 `slug` 仅保留为逻辑/展示名称。资源共享配置使用 v2 协议，只允许 `global` 和 `department` 范围，存量记录迁移为 `global`。超级管理员负责 global 资源，部门管理员只能管理其部门范围内的 department 资源。读取响应仅返回元数据、范围和凭据状态，不返回 API Key、Headers、环境变量或连接命令。

运行时、缓存和 AgentScope 投影均按 `resource_id` 解析和索引。Agent 保存与 Run 提交在后端重新授权资源；Run manifest 只保存资源 ID、配置指纹等非敏感信息，已开始 Run 使用提交时解析结果，新 Run 遵守后续权限变更。

提交事务将已经授权的主 Agent 和子 Agent 运行投影保存在 `run_resource_snapshots` 中。该表仅保存执行身份、认证指纹及 Fernet 密文；专用密钥由跨进程共享的持久 `API_KEY_DERIVATION_SECRET` 经 HKDF 派生。公开 manifest 仅引用快照 ID 和指纹，不包含凭据密文或明文。Worker 在 lease 下校验 manifest 指纹，Runner 和逐轮工具回调读取同一快照；恢复与子 Run 复用父快照，缺少快照的历史未完成 Run 显式拒绝并要求重新提交。轮换时新快照只用当前密钥加密，`RUN_RESOURCE_SNAPSHOT_DECRYPTION_SECRETS` 以 JSON 字符串数组保留活动 Run 所需的历史解密密钥，队列和活动 Run 清空后移除。

Business schema v5 实际创建快照表，并删除旧 ORM 生成的 MCP slug 唯一索引后重建非唯一索引；升级支持 v3/v4，重复执行不改变资源身份。快照配置冻结模型、MCP、Agent 投影和 Skill 选择，不承诺冻结 Skill 源文件字节。

## 替代方案

- 仅增加 `department_id`：拒绝，因为无法表达 global 共享、同名资源并存和统一的管理/读取范围。
- 保持 `provider_id`/`slug` 为跨部门主键并只在路由层过滤：拒绝，因为缓存、运行时和内部调用仍可能绕过路由授权。
- 在 Agent 配置中写入凭据快照：拒绝，因为会扩大敏感信息持久化面且权限撤销后仍可使用。

## 后果

- 引用方必须使用 `provider_resource_id:model_id` 和 MCP `resource_id`；旧模型 spec、旧 MCP slug 在边界处 canonicalize 到资源 ID。
- 同名资源可以并存，但前端必须同时展示资源名和 global/部门范围。
- 部门管理员不能创建或扩大 global 配置；普通 READ 用户可使用授权资源但不能读取敏感字段。
- Agent 保存会验证所选 Skill、预加载 Skill、传递 Skill 依赖及其 MCP 依赖均覆盖 Agent 的完整读取和管理范围；依赖缺失、循环或越界时拒绝保存。
- Agent 保存时把省略的 Skill/MCP 选择固化为操作者当时可见的明确列表，再执行完整范围校验；运行时不依赖调用者重新决定该 Agent 的资源集合。
- Provider/MCP 脱敏读取会隐藏含 userinfo、query 或 fragment 的 URL/endpoint，前端仅在用户显式编辑后回传这些字段。
- Provider/MCP 的创建、修改、删除、启停和工具切换缺少操作者时 fail-closed；连接测试、远端模型读取和工具刷新要求 MANAGE，全局模型缓存刷新仅允许超级管理员。
- 模型和 MCP 连接错误仅返回稳定的脱敏消息，不向管理 API 透传底层 endpoint 或异常文本。
- Agent 配置授权由固定 permissions 策略拥有，Service 和 Repository 调用同一策略，Repository 不接受可替换的授权回调。Provider 的用户查询缺少用户上下文时 fail-closed；系统缓存与内置同步使用显式 internal 查询。
- Agent 配置授权只依赖模型/MCP repository 与 `agents.skills.catalog` 只读目录，不反向依赖 MCP/Skill Service；个人 Skill 与共享 Skill 的同名覆盖语义保持不变。Skill 授权目录和正式安装/运行共用 `agents.skills.metadata` 解析规则，MCP 保存期与运行时共用 `agents.mcp.catalog` 内置注册表。
- 内置知识库创建、模型变更或共享范围变更时，必须按当前操作者重新授权 embedding 与 LLM Provider，并确认 Provider 读取范围覆盖知识库读取范围、管理范围和 Owner；旧 `provider_id` 只在唯一时转换为 `resource_id`，歧义或跨部门引用在持久化前拒绝。WeKnora 的远程模型 ID 不进入本地 Provider 授权链路。
- 内置 Provider 同步和 OCR 运行时只选择 `is_builtin` 记录；部门资源即使复用同一 `provider_id` 也不能阻止内置同步或劫持内置凭据路径。
- `is_builtin` 不属于公开 Provider DTO；HTTP 额外字段直接返回 422，Service 创建强制写入 `false`，普通 Provider 更新也不能提升为内置资源。
- 真实 PostgreSQL migration、HTTP、worker runtime、关键 E2E 需要对应环境才能验证；这些证据不由 unit、lint 或 build 结果替代。

## 验证

- 已验证：Run/queue/resume/subagent/逐轮投影回归 154 passed；模型调用与资源权限相关回归 119 passed。
- 已验证：`python3 -m unittest scripts.test_verify_engineering_contracts` 61 passed。
- 已验证：`python3 scripts/verify_engineering_contracts.py` passed。
- 已验证：变更 Python 文件的定向 Ruff、前端 lint、前端 build、`git diff --check`。
- 已验证：独立 PostgreSQL schema 的旧 MCP 唯一索引升级、同名资源插入、v5 快照表和重复迁移；隔离 Compose 已执行实际 v4 -> v5 升级。
- 已验证：独立 Compose `/api/system/ready` 为 `ready`；真实 HTTP 动态创建两个部门及管理员，证明 global 双部门可见、department 同名资源按部门隔离、跨部门 resource_id/旧 provider_id/slug 详情不可见、部门管理员不能创建或扩大 global、跨部门删除失败、脱敏响应完全省略凭据字段。
- 已验证：修复后 MCP 工具管理路由将当前用户传入运行时，Provider/MCP 管理响应对包括超级管理员在内的读取者统一脱敏，MCP cache 清理按 resource_id，Milvus 图谱入口支持旧模型 spec canonicalize；相关回归 33 passed。
- 已验证：`test_resource_snapshot_e2e.py` 经真实认证 HTTP、ARQ Worker、AgentScope、受凭据校验的 mock 模型及 PostgreSQL 输出回读，证明提交后撤销权限和更换端点/凭据时，旧 Run 完成且指纹不变，新请求被拒绝。模型为受控 mock，不代表真实外部供应商验收。
- 已验证：`test_run_resource_snapshot.py` 使用真实 PostgreSQL 验证加密存储、配置变更后读取旧模型/MCP、拒绝跨用户及篡改，测试事务回滚。
- 已验证：真实 Team E2E 的 child Run、历史、绑定及清理；Team 清单重复固化改为继承明确父快照，31 项单元覆盖首次 Reply、continuation 和指纹异常。
- 已验证：同名 MCP 的真实 streamable HTTP 发现及 echo 调用生成两个独立工具身份。MCP URL 中存在 userinfo、query 或 fragment 时脱敏响应省略 URL，未编辑时更新不覆盖原值。
- 已验证：后端聚焦回归 193 passed；前端完整 unit 194 passed，lint/build 通过。真实登录浏览器验证部门管理员新建 MCP 默认使用本部门范围，卡片展示 department，详情编辑保留范围并写入 PostgreSQL；重新打开编辑页后控制台无 Vue warning。浏览器夹具清理后活跃测试用户、部门和 MCP 均为零。
- 已验证：独立 Review 修复后的 Agent/Skill、Provider/MCP、Router、Repository、Run/queue/AgentScope 扩展聚焦 unit 542 passed；真实 HTTP + PostgreSQL 管理权限、模型调用授权、MCP identity/schema、AgentScope 投影和快照 integration 10 passed、3 skipped。新增真实 HTTP 用例证明伪造 `is_builtin` 返回精确 422 且 PostgreSQL 不产生记录；3 个 skip 为未配置的外部模型连接条件。
- 已验证：知识库模型授权、旧标识转换、范围覆盖、共享范围扩大重校验、运行时图片模态数据库兜底和缓存 resource_id 键相关回归 140 passed；真实 HTTP + PostgreSQL 探针证明部门管理员不能把其他部门 Provider 绑定到知识库。旧缓存 fallback 使用 provider_id-only 唯一查询，并以真实 PostgreSQL 碰撞用例证明一个 Provider 的逻辑 ID 不能借另一个 Provider 的 resource_id 获得授权。
- 已验证：Provider 真实 HTTP 安全探针通过，敏感 URL 被省略，省略隐藏字段的更新保留既有凭据，内置 Provider 更新返回 403。快照 PostgreSQL integration 覆盖当前密钥加密与历史密钥解密，密钥轮换单独复跑通过。
- 完整后端 unit：挂载仓库根目录且不继承 Compose 运行时目录环境的独立 API 镜像容器运行结果为 1972 passed。直接在常驻 API 容器运行会因缺少仓库根挂载并继承固定 Workspace 环境产生 66 个环境性失败；对应 114 项在干净容器单独复跑全部通过。
- 队列 integration：`test_continue_paused_queue_materializes_and_dispatches` 两次返回 409 `queue_not_paused`，未通过；运行中的 Worker 和共享队列状态尚未形成可重复的暂停前置条件。
- 未验证：外部模型供应商及外部 MCP。

快照 E2E 只在独立测试槽位运行：先确认无活动 Run 并暂停 worker，以 `E2E_SNAPSHOT_PAUSED_WORKER=1` 运行 `pytest -s test/e2e/test_resource_snapshot_e2e.py`；测试打印 `SNAPSHOT_READY_TO_RESUME_WORKER` 后恢复 worker。测试使用 8080 端口的 Compose mock，无需宿主 8765 端口。普通自动化未设置该隔离条件时跳过，避免操作共享 worker。
