# ReMe 长期记忆接入

## 目标

在不修改 AgentScope 源码的前提下，为 Yuxi 普通 Agent 和 Team leader 接入 ReMe 长期记忆。记忆按 `用户 + Agent slug` 隔离，在同一 Agent 的不同 Thread/Session 间共享；Team worker 不读取或写入长期记忆。

## 行为约束

- 用户通过现有 `enable_memory` 开关选择是否启用，默认关闭；关闭不删除已有数据。
- 提取使用系统 `fast_model`，检索同时使用 BM25 和系统 `embed_model` 向量。
- Workspace 位于 `/app/saves/memory/reme/<uid hash>/<agent hash>`，目录不包含原始业务标识。
- ReMe 运行时搜索、写入失败不阻断聊天；模型、目录或依赖配置错误显式暴露。
- Yuxi 调度器每 15 分钟检查 Dream，23:15 后处理当天，并在重启后补跑遗漏日期。
- Yuxi 归并调度器每 5 分钟扫描最近 7 天：仅按项目、任务、数据周期、用例和周期等稳定键自动归并高置信近重复 Daily；低置信卡片保持原样。
- 原始 Daily 保留为 ReMe 按 Session 继续写回的目标并标记 `archived_duplicate`；管理面和召回默认隐藏，canonical 记录来源路径和数量。
- 召回只接受 Daily 卡片与三类 Digest，排除日期索引、Session、Transcript 和 metadata；同主题优先 Digest，再按 completed、failed、running、pending 选择最新鲜候选。
- Dream 使用固定 hint 合并跨 Session 证据并排除 pending/派发时间等一次性状态；成功后必须完成公开 `reindex` 才推进 `last_dream_date`。
- 每轮 `auto_memory` 写入后立即执行同主题治理：新 Daily 命中历史 `yuxi_topic_key` 时归档来源并更新 canonical，再刷新完整索引。
- `auto_memory` 提取阶段禁止记录 pending、派发、等待回报、执行中和承诺交付等运行态；测试项目代号只记录原文，不推断环境性质。
- ReMe 0.4.0.6 的提取附加提示参数固定为 `memory_hint`；Yuxi 同时过滤 assistant 运行态输入，并在 Daily 写入后再次硬清洗，避免模型忽略提示时污染长期记忆。
- 管理面只展示 Daily 和 Digest Markdown 卡片，不展示索引、会话转录、metadata 或 interest 文件。
- 删除 Agent 前清除该 Agent 的全部用户 scope；删除用户前清除该用户全部 scope。Thread 删除不清除长期记忆。

## 验收 Checklist

- [x] Thread A 写入的事实可在同一 Agent 的 Thread B 召回。
- [x] 不同用户、不同 Agent、Team worker 之间不存在记忆串用。
- [x] 重启 `agentscope-dev` 后记忆和索引仍可使用。
- [x] Dream 成功生成 Digest，并持久化 `last_dream_date`。
- [x] 三条星砂岛近重复 Daily 归并为两个 canonical，原 Session/Transcript 保留，真实召回只返回两个主题。
- [x] Dream 为两个不同主题各生成一个 Digest，重建索引后召回优先 Digest 且不泄漏归档 pending 内容。
- [x] 单项删除和清空后，已删除内容不再可检索。
- [x] 记忆管理入口统一为“智能体 → 编辑智能体 → 记忆”。
- [ ] 普通聊天、Team、审批、取消恢复、压缩、缓存命中率和 Thread 删除无回归。

## 当前验证状态

已验证：

- `reme-ai==0.4.0.6` 可在 `agentscope-dev` 导入，AgentScope 使用挂载的本地源码。
- Registry single-flight、reply/维护 lease、模型指纹轮换、LRU、关闭释放和 fail-open 回归通过。
- 边界回归覆盖 core 创建失败重试、活动实例不被 LRU 回收、取消释放 lease、单/多 scope 关闭失败禁止进入物理清理且保留失败 core 重试。
- 文件安全回归覆盖非法日期、损坏编码、卡片符号链接、恶意 session ID、非哈希 Workspace 路径，以及 Digest 与 Daily/transcript 的独立删除语义。
- API 边界回归覆盖非法 kind/category/page/page_size 和非 SHA-256 memory ID 在公开边界直接返回 422，不转发内部服务。
- Dream 回归覆盖 Memory 关闭跳过、活动 scope 跳过、缺失 Workspace、超时、失败退避、七天补跑上限，以及多日补跑部分成功后中断。
- 管理 API 的可见性、禁用后管理、409 映射，以及 Agent/用户删除前清理顺序通过单元测试。
- 自动化测试覆盖两个用户在同一 Agent 下的真实 Markdown 卡片隔离、临时 Agent 删除全部用户 scope、临时用户删除全部 Agent scope，以及唯一前端入口契约。
- `test/e2e/test_agentscope_memory_e2e.py` 使用随机唯一事实验证真实模型跨 Thread 召回；仅在显式设置 `E2E_MEMORY_RECALL_ENABLED=true` 且配置 E2E 登录凭据时运行。
- 临时 Daily 卡片在 `agentscope-dev` 重启后仍可读取，清空 scope 后 PostgreSQL catalog 与 Workspace 同时删除。
- 星流 `qwen3-embedding-8b` 已完成真实单文本和多文本 4096 维调用；真实登录页面已验证“编辑智能体 → 记忆”列表加载和旧入口移除。
- 2026-09-01 的三个星砂岛来源卡片已在真实 Workspace 自动标记为归档，生成两个 canonical；管理 API 和浏览器仅展示 canonical，三个 Session 与 Transcript 均保留。
- 2026-09-02 的 `starsand-island-perf-data-request` 已清除团队创建、委派和等待回报等运行态，保留项目、用户、数据日期与 Session；完整 reindex 后真实混合检索未再返回这些运行态。
- 真实登录页面的“智能体 → 编辑智能体 → 记忆”卡片摘要与管理 API 一致，仅展示清理后的稳定请求事实。
- 临时真实 `auto_memory` 调用日志确认 `hint=True`；生成卡片保留稳定事实且不含派发/等待文案，验证后临时 scope 已删除。
- 手工执行真实 Dream 后生成两个不同主题的 Digest，catalog 为 `completed`；完整 reindex 后真实 BM25/向量混合召回只保留两个 Digest，不返回 canonical、归档来源或日期索引。
- 重启 `agentscope-dev` 后服务健康，管理 API、Dream 状态、四张可见卡片（两个 Daily canonical、两个 Digest）和浏览器页签保持一致。

当前环境未验证：

- 当前测试进程未配置专用 `TEST_USERNAME` / `TEST_PASSWORD`，依赖真实登录的 API 集成测试已成功收集但未在自动化命令中执行。
- 真实模型跨 Thread 召回用例默认关闭，需同时配置 E2E 登录凭据和 `E2E_MEMORY_RECALL_ENABLED=true`；未启用时明确记为 skipped。
- Agent/用户删除的真实环境级联测试只操作自动创建的临时实体，禁止对现有业务用户或 Agent 执行破坏性验收。
