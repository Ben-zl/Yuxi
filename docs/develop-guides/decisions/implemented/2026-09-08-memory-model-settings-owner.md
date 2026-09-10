# 记忆模型读取持久化系统配置

状态：implemented
类型：bug-fix
Owner：backend/package/yuxi/agentscope/config_projection.py

## 问题

模型管理页面保存到 PostgreSQL system_options，但 AgentScope 投影仍读取进程 config 的默认、快速和向量模型。用户启用记忆后仍可能使用过期供应商凭据，记忆中间件被禁用而普通对话完成。

## 决策

运行时投影在当前数据库会话读取 system_options，默认对话模型与记忆 Chat/Embedding 使用同一配置事实。显式请求模型及 Agent 自有模型继续优先。当前用户记忆开关和 Team worker 隔离不变。

## 替代方案

把数据库设置复制到进程配置会引入第二个可漂移的事实来源；沿用现有 Option 数据库读取入口。

## 验证

回归用例把进程默认设置为过期值，修复前因 stale:chat 与 saved:chat 不符失败。修复后测试默认、Agent 配置及显式请求三种优先级，记忆模型与调度相关测试共 20 passed，Ruff check 通过。

真实 PostgreSQL 读取目标会话后，模型构造使用已保存的星流 Chat/Embedding 配置。无活动 Run 时暂停同槽位 worker/AgentScope，读取该 Session 的三条持久化消息，通过现有 YuxiReMeCore.on_reply 执行一次维护提取；不重新运行历史工具。真实模型生成一张 Daily 卡片，ReMe 索引持久化两条 embedding，文件回读成功后更新 scope.last_memory_at，随后恢复服务。

本轮验证覆盖目标历史会话补生成、配置投影和记忆持久化；未发起新的完整 Agent Run。

## 后果

已经完成且未进入记忆中间件的会话不会自动重放，补生成必须读取明确会话的持久化消息，不能再次执行历史工具。
