# AgentScope 基线合并远程 main 的适配边界

状态：implemented
类型：architecture
Owner：backend/server/agentscope_main.py

## 问题

本地 `main` 已将产品执行、Session/Message 投影、Run/Attempt、lease、heartbeat、manifest、事件、reconciliation、Team、Workspace 和 Artifact 统一到 AgentScope 适配层；合并对象 `origin/main` `bd07ab4ac4faa2e5452507580b1c841543bbec61` 仍包含以旧 LangGraph/旧执行模型为前提的执行、持久化、附件、预览、权限、前端和工程门禁修改。按文件侧机械选择会恢复已废弃的旧执行事实，或丢失远程仍有用户价值的能力。

## 决策

- 使用一次 `--no-ff` 合并保留双方历史，合并提交为 `a2564a33643a86aed4d28b0828f8fd662a15834a`。
- AgentScope 执行、Session、Message、Run、Attempt、lease、heartbeat、manifest、事件、reconciliation、Team、Workspace 和 Artifact 继续由当前 Yuxi AgentScope/service/repository/storage 边界拥有。
- 远程配置、附件、预览、搜索、权限、安全、限速、前端展示、工程门禁、文档和 Compose 能力迁移到当前 AgentScope Run/SSE/Workspace/Artifact/Project 投影；不恢复旧 LangGraph checkpoint、旧 worker 或第二套 Run API。
- Project/Workspace 文件树的展示权限由 Project repository 与当前用户绑定关系决定；文件副作用继续由 Workspace no-follow Owner 和 Artifact/Viewer service 校验。
- AgentScope 使用外部 fork 的固定提交；Yuxi 仓库只实现适配层，不 vendor 或直接修改框架源码。

## 替代方案

- **直接保留远程冲突版本**：会重新引入旧 LangGraph/执行边界，拒绝。
- **全部保留本地版本**：会丢失远程安全、前端、预览和工程门禁能力，拒绝。
- **rebase 或逐个 cherry-pick**：会改写或拆散双方提交关系，拒绝。

## 后果

合并结果保留一个产品执行事实源：AgentScope Session/Execution 与 Yuxi Run/Attempt/lease/manifest/SSE/reconciliation 保持绑定。远程用户能力通过当前 Workspace、Project、Artifact、权限和前端边界提供。

后续审查发现的 manifest 发布、Team child heartbeat、Docker socket、CI 必填配置、测试 Sandbox cleanup、依赖可重建性和 Channel 入口问题，由 [AgentScope 远端合并审查闭环](2026-09-06-agentscope-merge-review-closure.md) 继续拥有；本记录不把未闭合项写成已通过。

## 验证

| 验收主张 | 语义 Owner | 直接证据 | 当前结果 |
|---|---|---|---|
| 合并提交保留本地与远端历史 | Git merge commit | `git show -s --format=%P a2564a33` | Passed |
| 旧 LangGraph shipping 执行入口未恢复 | AgentScope/Yuxi execution owners | engineering contracts、源码搜索、相关 unit | Passed |
| 远程 Workspace、Project、Artifact、Viewer 和前端能力进入当前边界 | services/repositories/web owners | 相关 unit、integration、web build | Passed |
| 合并后运行生命周期和 clean build 全部闭合 | 审查闭环 decision | 该记录中的证据矩阵 | Not run |
