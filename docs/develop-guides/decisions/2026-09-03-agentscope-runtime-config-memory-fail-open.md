# AgentScope 运行时配置同步与 Memory 降级边界

日期：2026-09-03

## 问题

AgentScope 服务独立于 API 进程运行。管理员更新系统模型后，API 会从 Redis
运行时快照同步配置，但 AgentScope 未启动同一同步机制，导致长期记忆仍使用进程启动时
的默认模型。默认凭证不可用时，ReMe 模型构造异常会发生在 Agent 回复前，并使普通对话
以 Session setup 错误结束。

## 决定

- `Config.start_runtime_sync()` 在创建后台同步线程前先执行一次同步，消除首个同步周期内
  的旧配置窗口。
- AgentScope 在原生 lifespan 已启动后调用同一配置同步入口，不另建配置读取路径。
- ReMe 模型和中间件构造失败时记录告警并跳过本轮长期记忆；普通对话的模型、工具、
  权限和 Session 装配继续遵循原有失败语义。
- Dream、索引重建等记忆维护任务仍显式报告失败，不伪装为成功。

## 替代方案

- 只在 AgentScope 启动时读取一次 Redis：无法处理管理员运行时切换模型。
- 在每轮对话中直接读取 Redis：增加主链路 I/O，并重复现有配置同步能力。
- 关闭用户 Memory：会改变用户配置且掩盖服务生命周期缺陷。

## 后果

- API、worker 和 AgentScope 使用同一 Redis 配置快照语义。
- 管理员切换 `fast_model` 或 `embed_model` 后，AgentScope 最迟在同步周期内生效。
- 可选长期记忆故障不会阻断普通聊天，但会产生包含 scope、且不包含凭证的告警。

## 验证

- 单元测试证明同步线程启动前已应用 Redis 快照。
- 单元测试证明 ReMe 构造异常返回空中间件并记录告警。
- Docker 环境重启 AgentScope 后检查实际运行配置，并重放失败对话，回读
  AgentScope Execution、Yuxi Run、消息和事件终态。
