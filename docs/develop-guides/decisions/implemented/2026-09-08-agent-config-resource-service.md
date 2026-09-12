# Agent 配置资源授权边界

状态：implemented
类型：architecture
Owner：backend/package/yuxi/services/agent_config_resource_service.py

## 问题

模型与 MCP 引用的授权策略放在 HTTP 路由中，内部调用 AgentRepository 保存配置时无法执行相同约束。

## 决策

公开服务 `authorize_agent_config_resources` 持有唯一策略：用当前操作者校验资源可见性，校验资源读取范围覆盖 Agent 的 read_scope、manage_scope 和所有者，并返回规范资源引用的配置副本。路由调用公开服务；AgentRepository 的通用 create/update 在修改对象与提交前调用同一服务，缺少 creator/updater 显式失败。仅扩大共享范围的更新也重新校验已有配置。

## 替代方案

仅移动路由函数无法保护内部保存调用。引入独立保存门面仍允许现有 repository 调用绕过；因此在当前持久化入口复用服务，接受路由预检与保存校验的重复查询。

## 后果

内部 create/update 调用必须显式传入操作者。内置 Agent 初始化、ensure_default_agent 对已有默认 Agent 的范围修正以及直接 ORM 写入不经过通用保存入口，仍是未闭合边界。本决定不改变 manifest、runtime，也不提供资源权限并发变更与 Agent 保存之间的事务锁保证。

## 验证

直接 service 测试覆盖规范化、操作者可见性、禁用资源、读取和管理范围、跨部门所有者以及失效用户。repository 负向测试直接调用真实保存方法，验证缺少操作者或共享范围扩大时拒绝保存且对象不变；路由测试验证公开服务错误映射。执行结果以本次测试报告为准，unit 不证明真实 HTTP、PostgreSQL 或 worker 链路。
