# ReMe 长期记忆接入

## 目标

在不修改 AgentScope 源码的前提下，为 Yuxi 普通 Agent 和 Team leader 接入 ReMe 长期记忆。记忆按 `用户 + Agent slug` 隔离，在同一 Agent 的不同 Thread/Session 间共享；Team worker 不读取或写入长期记忆。

## 行为约束

- 用户通过现有 `enable_memory` 开关选择是否启用，默认关闭；关闭不删除已有数据。
- 提取使用系统 `fast_model`，检索同时使用 BM25 和系统 `embed_model` 向量。
- Workspace 位于 `/app/saves/memory/reme/<uid hash>/<agent hash>`，目录不包含原始业务标识。
- ReMe 运行时搜索、写入失败不阻断聊天；模型、目录或依赖配置错误显式暴露。
- Yuxi 调度器每 15 分钟检查 Dream，23:15 后处理当天，并在重启后补跑遗漏日期。
- 管理面只展示 Daily 和 Digest Markdown 卡片，不展示索引、会话转录、metadata 或 interest 文件。
- 删除 Agent 前清除该 Agent 的全部用户 scope；删除用户前清除该用户全部 scope。Thread 删除不清除长期记忆。

## 验收 Checklist

- [ ] Thread A 写入的事实可在同一 Agent 的 Thread B 召回。
- [ ] 不同用户、不同 Agent、Team worker 之间不存在记忆串用。
- [ ] 重启 `agentscope-dev` 后记忆和索引仍可使用。
- [ ] Dream 成功生成 Digest，并持久化 `last_dream_date`。
- [ ] 单项删除和清空后，已删除内容不再可检索。
- [ ] 设置页、聊天页和 Agent 管理页均可访问记忆管理。
- [ ] 普通聊天、Team、审批、取消恢复、压缩、缓存命中率和 Thread 删除无回归。

## 当前验证状态

已验证：

- `reme-ai==0.4.0.6` 可在 `agentscope-dev` 导入，AgentScope 使用挂载的本地源码。
- Registry single-flight、reply/维护 lease、模型指纹轮换、LRU、关闭释放和 fail-open 回归通过。
- 管理 API 的可见性、禁用后管理、409 映射，以及 Agent/用户删除前清理顺序通过单元测试。
- 临时 Daily 卡片在 `agentscope-dev` 重启后仍可读取，清空 scope 后 PostgreSQL catalog 与 Workspace 同时删除。
- 设置文案、聊天页入口、Agent 管理卡片入口和禁用状态弹窗已在真实登录页面验证。

当前环境未验证：

- 系统 `fast_model` / `embed_model` 所属 SiliconFlow provider 的 API Key 当前是不可调用的非 ASCII 占位值。
- 因此真实提取、跨 Thread 向量召回、Dream 生成、真实 reindex 和删除后向量不可检索尚未执行；配置有效模型后需继续完成上述 checklist。
- 集成测试文件已补充，但当前环境未配置专用 `TEST_USERNAME` / `TEST_PASSWORD`，不能把该脚本标记为通过；真实登录浏览器和内部 API 验证已完成。
