# E2E Runtime Closure

状态：implemented
类型：testing
Owner：backend/package/yuxi/services/conversation_service.py

## 问题

真实 Compose E2E 暴露出的附件、Artifact、Skill preload、AgentScope 会话、运行快照和 Sandbox 挂载边界不一致已经闭合。验证区分生产代码缺陷与外部模型能力、专项测试开关等环境条件。

## 决策

1. Conversation 绑定的 Project Workdir 是正式附件和附件 Artifact 的唯一持久化 Owner。确认临时附件时，原始文件与解析 Markdown 都通过授权 `Workdir` 写入 `/uploads`，API 返回的 runtime path、数据库记录和 Artifact resolver 必须指向同一资源。该约束同样支持 `clients/acme` 及根级 `uploads`、`outputs` 等显式关联 Project Workdir；下载和保存 Artifact 必须先判定当前 Workdir Owner，同用户其他 Project 的真实文件也不能跨 Workdir 读取。新建 Project 在同 UID 路径锁内拒绝严格父子 Workdir 重叠，完全相同的目录仍可共享；历史重叠绑定在运行期 fail-closed。选择历史重叠 linked Project 新建 Conversation 时，必须在插入和 commit 前完成同一 Owner 校验，不得留下不可用线程。非当前 Workdir 的 Session 根级产物由同一线程 Session 授权读取，也可有界保存到用户目录；用户根级单文件和 `saved_artifacts` 保留既有兼容，但后者不能绕过其他 active Project 的 Owner。
2. Run 提交时解析的完整 Skill tree 属于该 Run 的加密资源快照，包括 `SKILL.md`、脚本、资产、空目录和可执行位。执行阶段在 AgentScope 进程内临时物化并安装，不能重新读取当前 Skill 内容或授权投影替代提交时快照。历史快照缺少完整 tree 时继续使用其历史 `source_dir`。
3. AgentScope 执行必须经过统一 Yuxi Run 边界。直接触发没有 active Yuxi Run 的 AgentScope chat 不是有效的生产执行路径，相关 E2E 应通过 Run submission、SSE 和 PostgreSQL 事实验证。
4. Subagent 的用户可见状态从 PostgreSQL 子 Run 事实投影，不以 AgentScope 临时线程状态作为唯一来源。
5. Dashboard 的 token 数据以终态 `AgentRun.token_usage` 和 `finished_at` 为事实来源，覆盖 `chat`、`resume`、`subagent` 及失败 Run 已消耗的 token；排除已删除 Conversation 和 User。`Message.extra_metadata` 不再作为统计来源。
6. MCP 工具解析始终要求当前用户授权上下文，资源引用优先使用不可变 `resource_id`；测试不得恢复按 slug 绕过授权的调用。
7. Schema 13 把历史 `agent_run_attempts` 的整数 ID、时区时间列和旧字段名迁移到当前事实模型，并把已废弃的 `expired` Run 状态归一为 `interrupted`，清空失效 lease 与 cleanup 标记；`message_feedbacks.message_id` 只允许数字旧值转换，非法值必须使整个迁移回滚；历史 `message_feedbacks.conversation_id` 及其冗余外键被删除，反馈只通过 `message_id` 归属消息；旧 Agent `read/write` 工具引用在迁移中删除。
8. 持久 Sandbox 的 UID Skill projection 目录是只读挂载的结构前提。Provider 和 Worker 只负责安全建立空目录，不在执行阶段查询当前授权或填充 Skill；本次 Run 的 Skill 仍只从加密快照装入 AgentScope workspace。
9. Skill 快照 slug 必须符合现有 Skill slug 规范，路径必须为安全相对 POSIX 路径，文件与目录声明不能冲突。v2 快照把完整 tree 按内容指纹存入顶层 `skill_trees`，各 Agent projection 只保存 `snapshot_ref`；同一内容和 slug 在一个 Run 中只序列化一次。`preloaded_skill_contents` 和已注入 `system_prompt` 的 preload 正文不再按 projection 持久化，加载已认证快照后统一从对应 tree 的根级 `SKILL.md` 派生。preload 根文件按单次与累计字节预算有界读取，Skill tree 在目录枚举时增量限制条目数。主 Agent 和全部 SubAgent 的唯一 Skill tree 合计最多 4096 个条目、100 MiB 未编码内容；每捕获一棵立即去重和累计，超限不再捕获后续树；加密前限制完整明文，解密前限制密文，解密后再次限制明文并校验引用、slug 和内容身份。历史内联 `snapshot_files` 和已包含 preload 正文的旧 v2 快照继续兼容。
10. 提交期资源解析属于请求 intake 边界；模型、MCP 或 Skill 授权不满足时返回 HTTP 422，基础设施异常仍按服务错误处理。
11. E2E Conversation、Project 和 Run request 的 canonical 标记为 `YUXI_TEST_`，新增或改造的真实 HTTP 测试通过统一 helper 生成；存量测试仍允许清理器中列明的严格历史格式，并在触及对应测试时迁移。清理事务从目标 Run manifest 和未派发 Request 的 `_run_manifest` 提取 `runtime_snapshot.id`，并按 `uid + snapshot_id` 删除对应加密快照；同一用户未被目标 Run/Request 引用的快照必须保留。静态 `e2e-`/`e2e_` 专用 UID，以及严格列明的历史 `codexe2e<10digits>`、`codex-e2e-admin`、Team E2E 和 `task-it-user` fixture，统一先收集 `CleanupConversationResource`，在业务行删除前通过现有 AgentScope 删除链路销毁 Session 和物理 Workspace，再在 Project Owner 锁和 Workdir 重叠校验内删除文件，最后物理删除该用户的 orphan Run/Request、AgentScope mapping、Memory scope、Snapshot、API Key、UserConfig、AgentEnv、OperationLog 和用户行。数据库确认该 UID 已无 Project 后，才允许删除其精确共享 Workspace 根；历史 snapshot 测试只额外兼容严格的 `snap_<8hex>` UID，无用户行的 `pytest-user-<uuid>`、`pytest-lock-user-<uuid>`、`pytest-project-recreate-<32hex>` 和 `snapshot-<8-10hex>` 只作为孤儿 Workspace 根回收。普通 UID、相似前缀、越界路径和任意 symlink 均拒绝。会话级清理保留当前认证管理员，最终收尾再显式删除；严格 `snapshot-<8hex>` 部门只有在无 User 和 API Key 引用时才作为孤儿删除。
12. 持久 Sandbox 的写权限由当前实例的规范化 `workdir_path` 决定。文件 API 只允许当前 Workdir 写入，根级历史 `uploads`、`outputs` 和 `workspace` 不再作为写入兼容根；Docker 与 Kubernetes 把用户 Workspace 根挂为只读，只把当前 Workdir 子目录覆盖为可写，避免命令执行绕过文件 API 后跨 Project 写入。
13. Worker 只接受当前 Conversation Workdir 的 runtime 附件路径。历史绝对路径仅兼容当前线程旧 `uploads` 根，并逐层 `O_NOFOLLOW` 打开普通文件。Worker 保持已验证 fd，并把内容有界复制到本次上传专用临时普通文件，AgentScope Client 不再重新打开原始路径；上传结束后删除临时文件。附件删除只通过 Workdir capability 或相同旧根删除，不对数据库中的任意绝对路径执行 `unlink`。
14. 删除历史线程时，AgentScope Session 已不存在属于可恢复的 404：上层保留空 Workspace ID 并继续进入底层 Docker 历史路径清理。409、传输错误和其他状态继续 fail-closed；父 Session 与 Team worker binding 使用相同语义。
15. Reviewer 发现的安全边界必须具有直接负向测试：managed/linked Workdir 邻接写入、绝对附件和 symlink、越界删除、父/子 Session 404、Run 级 Skill 总条目和总字节均在正确原因上失败后再修复。
16. Skill 的工具依赖只通过 `skill_dependency_gateway` 或 `skill_external_dependency_gateway` 暴露，preload 只改变非 external Gateway 的首轮激活状态和描述，不把依赖隐式提升进 Agent 顶层 `tool_slugs`。external 依赖即使来自 preload Skill，也必须先调用 `Skill` 获取当前 Session 的随机 activation token。
17. 外部入口新建 implicit Project/Conversation、附件元数据、Request、Run 和运行快照属于同一提交单元。同一 `request_id` 在附件副作用前用 PostgreSQL 事务锁串行化；已有 Request 经完整持久化作用域校验后直接返回原视图，即使 Agent 后续对用户不可见也不再次写附件或投递；只有首次新提交才检查当前 Agent 可见性。附件写入或 intake 授权失败时回滚数据库和本次附件；进入 commit 后若调用端只收到错误，先通过独立数据库会话按 Request/附件 ID 回读归属，已提交或无法安全确认时保留文件。只有确认未提交且异常不属于连接中断或取消时才补偿。managed Workdir 的删除在同 UID Project 路径锁内复核重叠 Owner，仅逐层删除空目录，不递归删除用户后续写入；任何补偿失败附加诊断而不覆盖原异常。commit 成功后才进入 Workdir 物化和 ARQ 投递，此阶段失败保留已提交事实并由 pending dispatch recovery 接管，不能反向删除附件或业务行。并发命中既有 Conversation 时不得补偿删除既有 Project。
18. MCP 工具发现和实际调用共同承担连接异常脱敏。发现异常只记录 `resource_id` 和异常类型，并向 Worker 抛出不带 cause 的固定错误；发现成功后的工具由同一包装对象供顶层 MCP 和 Skill Gateway 使用，`call()` 抛出的异常以及 MCP 协议返回的 error ToolChunk 都替换为固定错误。日志、模型上下文、ToolCall、Run 终态、Redis 事件、SSE 和结果 API 不能接触 URL、header、token 或其他底层异常正文。
19. 同步 Agent Call/Eval 等待必须传播任务取消。API 停止、客户端断开或调度取消产生的 `asyncio.CancelledError` 不能结束 SSE 迭代并加载非终态 Run，也不能转换成 HTTP 504；只有真实达到 `RUN_SSE_MAX_CONNECTION_MINUTES` 才产生等待超时。完整 E2E 使用 `docker-compose.e2e.yml` 移除 API/AgentScope 的热重载和 Worker 的 `watchfiles`，避免验证期间由开发热重载改变运行语义。
20. E2E cleanup fence 必须顺序执行全部清理步骤。普通异常和 `CancelledError` 都不能阻断后续步骤；清理 helper 收到主体异常时保持主体异常并附加清理失败 note，主体成功但清理失败时分别通过 `ExceptionGroup` 或 `BaseExceptionGroup` 使测试失败。pytest 的 session fixture 无法取得测试主体异常，其收尾失败作为独立 teardown 错误报告，不掩盖原测试失败。会话清理在删除 Run 行前捕获当前测试 UID 的标记线程及待删除静态 UID 的全部拥有线程的 Sandbox ID，只有相应 Run 清理完成后才使用捕获的归属删除 provisioner 资源；捕获或 Run 清理失败不得静默跳过 Sandbox。linked Project、Memory、Snapshot、E2E 会话总清理和 Agent task integration fixture 使用同一 fence；PostgreSQL、Redis 和 ARQ pool 的关闭不能被前序数据清理失败短路。严格静态测试管理员在每次 setup 前通过 PostgreSQL 幂等 provisioning，能够在非空长期数据库中重复运行；setup 保留当前认证身份，final teardown 在全部授权 HTTP、Sandbox 和知识库清理完成后物理删除该身份。
21. 挂起审批事件保留线程级 Redis Key，新写入事件携带 Run Owner。取消 API 在提交数据库事实后以 Redis 原子 Owner 比对清理，历史无 Owner 的事件只在目标 Run 从 `interrupted` 转成 `cancelled` 或重试该转移时允许删除；同线程新 Run 的审批不得被旧 Run 取消清理。Redis 清理失败时仍发布取消信号，返回可重试的 503；重试清理成功后续派队头。Worker 在持久化终态后按自己的 Run Owner 清理交错写入的审批事件；异常收束也在失败终态提交后清理本 Run 的审批键，且持久的 `cancel_requested` 优先于 Redis 信号读漏。`cancel_requested` 与审批挂起交错时以数据库取消事实收束为 `cancelled`、补发 end 并续派队头。已挂起或尚未认领的 Run 在取消提交并清理后由 API 续派队头。SSE 对早于数据库终态或与持久终态冲突的 end 延后输出，以 PostgreSQL 状态为准；历史失败 end 的 `error` 状态保留现有协议含义。新聊天回合先恢复无有效挂起事实的 AgentScope Session，不受旧线程键干扰。

## 替代方案

- 只修改 E2E 断言或把失败测试标记为 skip：无法证明 Project Workdir、Run 快照和 AgentScope 首轮请求的一致性。
- 让 Artifact resolver 同时猜测多个历史路径：会保留多个文件 Owner，无法保证权限和清理边界。
- 放宽 `present_artifacts` 对空文件清单的校验：会破坏产出物交付契约，不能作为 deterministic mock 的修复。

## 后果

- 附件、Artifact、AgentScope runtime path 和 Project Workdir 只保留一个正式 Owner，测试夹具必须完整遵守临时附件确认契约。
- 直接 AgentScope chat 不再作为有效 E2E 入口；执行测试统一创建 Yuxi Run 并验证 SSE、PostgreSQL Run、Message 和 Artifact 事实。
- MCP 内部与测试调用必须携带当前用户并使用 `resource_id`，缺少授权上下文时 fail-closed。
- 历史业务 schema 升级会转换 Attempt 主键和 UTC 时间语义；feedback 存在非法旧 ID 时迁移整体失败并保留原始数据。
- 需要工具自动执行的非 HITL E2E 必须显式使用 `always_trust`，避免把审批状态误判为被测功能失败。
- 持久 Sandbox 可在 projection 冷启动时创建，但不会因此获得最新 Skill 授权；权限变更只影响新 Run，已提交 Run 继续消费自己的加密快照。
- preload Skill 的非 external 本地/MCP 依赖在首轮视为已激活，Gateway 的工具描述包含真实工具名和参数 schema；external 依赖仍只能通过显式调用 `Skill` 获得随机 activation token。
- Skill 依赖不再作为顶层工具重复暴露；如果 Agent 自身显式配置该工具或使用 `tools=None`，其独立顶层授权语义保持不变。
- 新外部 Run 在提交前失败不会留下孤立 Conversation、implicit Project、附件元数据或 managed Workdir；提交成功后的投递失败仍由既有 Run 恢复机制处理。

## 验收标准

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 附件与 OCR Artifact 使用同一 Project Workdir | 返回路径与实际文件来源不一致 | `conversation_service.py`、`artifact_service.py` | 历史附件、OCR、非视觉 OCR fallback E2E + Session Owner 和真实邻接 Project 文件 unit；linked Project 原始 Artifact URL 完整 E2E 回读 | Artifact 返回 404、跨 Project 读取或确认请求 422 | Passed（历史路径与 linked Project 完整 Run） |
| 首轮请求包含 preload 内容与必要工具 | 仅 manifest 有 hash，AgentScope 请求缺失内容或工具 | `config_projection.py`、AgentScope tool projection | projection unit + deterministic replay E2E | replay 返回 `preloaded_skill_missing` 或 `preloaded_tool_missing` | Passed |
| 执行入口统一经过 Yuxi Run | 直接 AgentScope chat 没有 active Run | `run_submission_service.py`、E2E helpers | API/SSE/PostgreSQL E2E | session guard 拒绝无 Run 请求 | Passed |
| Subagent 状态从 PostgreSQL 事实投影 | 线程状态丢失子 Run | `thread_state_service.py`、`subagent_run_service.py` | Subagent E2E 与 DB 对照 | `subagent_runs` 为空 | Passed |
| Dashboard token 统计语义明确 | 调用次数与 token 数断言混用 | `dashboard_repository.py` | Steer E2E Dashboard API + DB 事实 | token usage 已存在但统计为 0 | Passed |
| MCP 使用 resource ID 与用户授权上下文 | 旧 slug 或无 user 调用绕过授权 | MCP service、MCP E2E | MCP runtime、stdio security、service unit | `PermissionError` 或旧 slug 404 | Passed |
| MCP 执行异常全链路脱敏 | client 异常正文进入日志、Run、SSE 或结果 API | `agents/mcp/service.py` | MCP service unit + Worker PostgreSQL/Redis integration | 合成敏感标记由 client 异常注入 | Passed |
| Schema 13 真实旧表升级幂等 | 新 ORM 无法写入升级后的旧表，旧 `expired` Run 被误判为活跃 | `storage/postgres/manager.py` | 隔离 PostgreSQL schema integration + 真实 Compose schema 回读 | 非数字 feedback ID 回滚，旧 `conversation_id NOT NULL` 阻塞反馈写入，历史 `expired` Run | Passed |
| 完整 Skill tree 使用提交时快照 | 源目录删除或脚本更新后串用当前版本 | `skill_snapshot.py`、`run_resource_snapshot_service.py` | 暂停 Worker 快照 E2E + snapshot unit | 非法 slug、超限、路径冲突、撤销权限后新 Run | Passed |
| Sandbox 冷启动不重新查询授权 | 缺少 UID projection 导致创建失败，或恢复最新授权破坏不可变性 | `sandbox/provider.py`、`worker_job.py` | deterministic 与 linked Project E2E | projection 缺失、projection symlink | Passed |
| E2E 清理回收 Run 快照、Project、静态 UID 用户事实和 Workspace | 软删除、取消或 fixture 失败后残留 User、Conversation、Run、Request、Snapshot、Memory、Workdir、部门或 Sandbox | `test/live_api_cleanup.py`、`agentscope_e2e_fixtures.py`、E2E 标记 helper | 清理 unit + 真实 PostgreSQL integration + 完整 E2E 后回读 | cleanup `CancelledError`；保留当前认证管理员和同 UID 未引用快照；拒绝普通 UID、共享 Workdir、越界和 symlink | Passed |
| Sandbox 只能写当前 Workdir | 整个用户 Workspace 可写导致跨 Project 修改 | Sandbox Backend、Docker/Kubernetes Provisioner | 路径权限 unit + 真实 Docker mount/写入探针 | managed A→B、linked 邻接目录、容器命令跨目录写入 | Passed |
| 附件绝对路径不能绕过 Workdir | 任意宿主路径读取、删除或校验后替换 | `worker_job.py`、`conversation_service.py` | 附件 unit + linked Project E2E | 根外绝对路径、symlink、校验后替换、越界删除 | Passed |
| 历史 Session 404 仍清理 Workspace | 上层提前退出，底层历史兼容不可达 | `conversation_service.py`、`workspace_cleanup.py` | 服务入口 unit + workspace cleanup unit | parent mapping 和 worker binding 缺失 Session | Passed |
| Skill 快照限制覆盖整个 Run | 多个单独合法或重复 Skill 使快照、preload 正文、明文或密文无界增长 | `skill_snapshot.py`、`run_resource_snapshot_service.py` | 快照 unit + 暂停 Worker E2E | 合计条目/字节超限、主/子 Agent 共享 preload 正文只存一份、未知引用、身份错配、加解密载荷超限 | Passed |
| preload Skill 依赖只通过 Gateway 暴露 | external/local 依赖被隐式提升为顶层工具，绕过激活或 token | `config_projection.py`、`tools.py` | 完整投影/工具装配 unit + deterministic replay E2E | `tools=[]` 时顶层出现 `ask_user_question`；replay 不经 Gateway 调用 `present_artifacts` | Passed |
| 外部新 Run 提交失败与不确定结果区分 | 提交前遗留孤立 Workdir，或提交确认丢失后删除已提交附件 | `run_submission_service.py`、`attachment_service.py` | 真实 PostgreSQL + 临时 UserWorkspace integration | 附件成功后 intake 校验、commit 回读、重复取消；commit 后投递失败保留恢复事实 | Passed |
| 同步等待不把取消伪装成超时 | reload、服务停止或客户端断开后读取非终态 Run 并返回 504 | `agent_run_service.py`、`docker-compose.e2e.yml` | 取消传播 unit + Agent Eval/Call 连续 E2E | `CancelledError` 被吞掉或稳定拓扑发生容器重启 | Passed |

## 验证

- 最新完整 Compose E2E（2026-09-14）：`37 passed, 4 skipped`，包含新增 linked Project 附件用例；当前未注入 `MINIMAX_API_KEY`，真实 MiniMax-M3 Roundtrip 条件跳过，其余 3 个 skip 分别为独立视觉模型、外部非视觉 capability 和暂停 Worker 资源快照专项。此前有凭据的完整套件曾为 `38 passed, 3 skipped`，当时 MiniMax-M3 Roundtrip 和 ReMe 跨 Thread 长期记忆召回均实际运行，但该结果早于新增附件断言。
- MiniMax-M3 真实模型 Roundtrip、SSE 终态和非零 token usage：`1 passed`；同一真实模型环境下 ReMe 跨 Thread 长期记忆召回与测试用户物理清理：专项重跑 `1 passed`。
- 暂停 Worker 资源快照专项：`1 passed`。提交 Run、撤销 Skill 权限、删除 Skill 来源并替换模型凭据后恢复 worker，旧 Run 完成且指纹不变，新 Run 被拒绝。
- Schema 13 隔离 PostgreSQL 迁移覆盖整数 Attempt ID、四个 UTC 时间列、feedback ID、历史 `conversation_id` 清理、旧工具清理、旧 `expired` Run 终态归一、非数字/越界/孤儿 ID 回滚和重跑幂等；真实 Compose 数据库已回读 `business:13`，旧状态已归一为 `interrupted`，且仅保留 `message_feedbacks.message_id` 外键。
- clean API 镜像容器只读挂载完整仓库根目录，隔离 `saves` 和 Skill projection，并移除干扰测试夹具的运行配置目录覆盖；提交前最新完整 unit：`2136 passed`，包含静态 UID 未标记线程的 Sandbox 归属捕获、completed→failed SSE 负向测试和 Artifact 正式 HTTP 路由回归。直接在长期 API 服务容器运行会因仓库根未挂载和运行目录污染产生环境型失败，不能替代隔离单测容器。
- 历史 inline-v2 Skill 快照和 MCP 异常脱敏直接回归：相关 service unit `46 passed`，Worker PostgreSQL/Redis integration `8 passed`，MCP 真实 HTTP/AgentScope/Redis/PostgreSQL E2E `4 passed`。发现异常、`call()` 抛出异常和 MCP error ToolChunk 都只向下游传播固定错误，合成敏感标记未进入 MCP/Worker 日志、模型上下文、ToolCall、`agent_runs.error_message`、Redis 事件、SSE 或结果 API；`skill_trees` 缺失的历史 v2 保留已有 preload 正文，字段存在但类型非法时继续拒绝。
- 本次串行聚焦 PostgreSQL/Redis integration：`61 passed`，包含 Worker、E2E 用户物理清理、MCP 资源身份与旧表升级、Schema 13、Dashboard token、提交补偿、同 `request_id` 事务锁、历史父子 Project Workdir 拒绝、线程创建前数据库零残留、Run lease、历史无 Owner 审批、两种取消交错、已挂起与未认领 Run 取消后续派、持久取消优先于 Redis 读漏、审批持久化失败后的 Owner 清理、未标记线程 Sandbox 列举和未派发 Request 快照定向回收。Agent task API integration 在夹具显式取消测试 UID 未完成 Run 并回读终态后串行运行 `11 passed`；专用 UID 的 User、Run、Conversation 物理回读均为零。同一 Compose project 中并行运行这两组测试出现共享 Worker/夹具时序失败，不能作为通过证据。测试清理补充严格历史 UID、integration fixture 与孤儿 Workspace 白名单后，清理 unit 持续通过；Skill E2E 专项和临时管理员 provisioning 后的 Agent Eval/Call 均为 `1 passed`，Skill E2E 运行前后 AgentScope E2E Workspace marker 数保持不变。
- Artifact 正式 HTTP 保存路由接入 `artifact_service` 的当前 Project Workdir 授权，并透传可选 `destination_path`；个人目录的保存结果使用 `/api/workspace/download`，不能凭线程 Artifact URL 读取任意未绑定目录。其他 Project 的源路径及目标目录均拒绝；Session 根级路径也先排除其他 active Project Owner。Project 创建、删除、Conversation 新建、附件确认/直传/删除、Run 提交与 Artifact 读写共用同 UID 事务级路径锁；附件补偿在 rollback 后重新加锁并复核原 Project 身份再删除。正式附件删除在会话行锁下检查附件绑定状态、queued Request 输入消息中的 `attachment_file_ids` 和活动 Run；先提交元数据，再重新取得路径锁确认 Project Owner 后清理当前及历史路径；提交失败保留文件，Owner 变化保留可能的孤儿文件而不跨 Project 删除。ASGI 路由与 Workdir 夹具回归 `36 passed`；真实 PostgreSQL Workdir Owner 与附件并发回归本次 `7 passed`（含提交失败和未绑定 queued Request 回读），原有清理路径锁回归 `1 passed`；Run 提交补偿集成 `4 passed`。未使用的线程文件浏览/内容路由已移除，正式文件浏览由 Viewer 提供。临时测试管理员绑定部门后，Artifact 保存与遗留路由移除的真实 HTTP integration `5 passed`，测试账号回读为零。
- 本轮再次串行组合运行相关 PostgreSQL/Redis integration：`49 passed, 1 failed`；唯一失败是 `test_fifo_serial_dispatch_and_execution` 触发旧异步连接跨 event loop 复用。该用例随后在独立 pytest 进程中 `1 passed`，但组合运行仍不能记为整体通过。
- 本轮全部 backend 修改和新增 Python 文件的定向 Ruff check 曾通过；本次提交门禁发现 3 处格式问题并已修正，修订文件的 Ruff check 与 format 重新检查。全仓及 `docker/sandbox_provisioner/app.py` 仍有既存 lint/格式债务，不计为本轮通过。
- 工程契约通过，工程契约自测 `61 tests OK`，docs build 和 `git diff --check` 通过。
- 当前前端工作区 lint、`241` 个 unit 和 production build 通过；Compose Web 镜像未挂载 `web/test`，不能用镜像内旧测试替代当前工作区测试。
- Reviewer 四项修复后的真实 Sandbox 探针确认 `/home/gem/user-data` 为只读、当前 Workdir 为可写；当前目录写入成功，邻接 Workdir 写入被容器文件系统拒绝。
- 本轮按相同只读用户根与当前 Workdir 可写的嵌套 bind mount 重测：当前 Workdir 创建文件成功，根级历史 `uploads` 创建文件返回 `Read-only file system`；本次探针临时目录已删除。

## 未验证范围

未配置独立视觉模型，`E2E_VISION_MODEL` 对应的真实图片理解测试未运行。MiniMax-M3 在本次配置中明确为纯文本模型，不能用修改 capability 的方式替代视觉模型证据。

未配置独立 `E2E_NON_VISION_MODEL`，外部模型 capability 拒绝专项未运行；第三方真实 MCP 未验证，MCP 证据来自真实授权链路和 Compose MCP 服务。

2026-09-14 新增的 Session 建立后回读 linked Project 原附件 URL 的 E2E 断言已在稳定 Compose 中以临时管理员单独运行 `1 passed`，并在最新完整 E2E 中再次通过；原始 Artifact 返回上传时的完整字节。完整套件后临时 UID 的 User、Conversation、Project、Run、Request、Snapshot 回读均为零，Workspace 根不存在，`/api/system/ready` 返回 200。Artifact 保存及遗留路由移除的 5 条 HTTP integration 曾用临时 `TEST_USERNAME`/`TEST_PASSWORD` 运行。对应 Owner 路由、同名直传隔离、Conversation 两个附件入口和 Run 提交的取消/异常补偿定向 unit 曾为 `121 passed`；后续又补充了真实 PostgreSQL 提交归属回读、重复取消、清理失败及空目录 Owner 保护回归。

完整 integration inventory 最近一次结果为 `274 passed, 29 failed, 4 errors, 4 skipped`，不能记为通过；相对本轮既有 `271 passed, 30 failed, 4 errors, 4 skipped` 没有新增失败。剩余失败包含既有队列暂停竞态、MinIO bucket/Workspace 测试环境、过期 HTTP 断言与测试夹具，以及 pytest 异步单例跨 event loop；本轮计划指定的 PostgreSQL/API integration 另有 `28 passed`。
