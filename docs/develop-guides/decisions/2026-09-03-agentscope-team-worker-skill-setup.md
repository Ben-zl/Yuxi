# AgentScope Team worker Skill 装配与准备失败终态

日期：2026-09-03

## 问题

旧数据中的内置 Skill 记录可能保留 `shared/<slug>` 目录。当前运行时把内置 Skill
安装到 `skills/<slug>`，但启动同步只更新版本与内容哈希，没有更新数据库目录，导致
Team worker 在 `extra_agent_tools` 阶段因找不到 `SKILL.md` 而准备失败。

AgentScope 的准备失败发生在 Agent 和 Reply middleware 构造完成前。Yuxi 已在
`AgentCreate` 成功后投影 child Run，但原有终态逻辑只观察 Reply 事件，因此准备失败的
child Run 会长期停留在 `running`。

## 决定

- 内置 Skill 初始化以代码注册表和 `saves/skills/<slug>` 为权威来源；更新已有记录时
  同步归一 `dir_path`，保留管理员设置的启停状态。
- Yuxi 的额外 middleware 或工具装配异常继续向 AgentScope 抛出，由 AgentScope 原生
  失败消息和 leader 通知负责用户侧语义。
- 若失败 Session 已存在 Team worker binding，Yuxi 同时把对应 child Run 结束为
  `failed/agentscope_team_setup`，并写入唯一终态事件。
- child Run 已终态或尚未完成 binding 投影时保持幂等，不创建新的 Run 或重复终态。

## 替代方案

- 只复制缺失目录：不能修复数据库中的持久错误路径，下一次换数据或重建环境仍会复现。
- 增加 Session 准备重试：确定性的 Skill 配置错误不会因重试恢复，还会重复通知 leader。
- 修改 AgentScope 的 setup error：会扩大到框架源码，不符合 Yuxi-only 约束。

## 后果

- 从旧数据副本启动时，内置 Skill 记录会在 API/worker lifespan 中自动收敛到当前目录。
- Team worker 的装配错误仍然对 leader 可见，但 Yuxi child Run 不再残留为运行中。
- 模型、知识库、MCP 或 Skill 的其他准备错误也共享同一 child Run 终态保证。

## 验证

- 单元测试恢复 `shared/<slug>` 旧记录，证明初始化后目录更新为 `skills/<slug>`。
- 单元测试模拟 Reply middleware 之前的准备失败，证明 child Run 只写一次失败终态。
- Docker 环境回放原 worker 的 `_extra_agent_tools`，确认 `auto-platform-query` 成功安装。
- 通过公开 Run API 创建真实 Team，回读 parent/child Run、消息和 `TeamSay` 均完成。
