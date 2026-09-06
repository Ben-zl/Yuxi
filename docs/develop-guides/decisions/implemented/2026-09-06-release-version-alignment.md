# 0.7.2 发布元数据与跨分支补丁边界

状态：implemented
类型：bug-fix
Owner：backend/pyproject.toml

## 问题

前端与 Compose 默认镜像为 0.7.2，后端两个包及锁文件仍为 0.7.1。提交是否存在于祖先历史不能证明补丁是否适用于当前架构。

## 决策

统一后端包和锁文件为 0.7.2，通过结构化解析包元数据验证版本一致性。保留当前固定 Git SHA 的 AgentScope 依赖。

`a51e164e` 的列重命名依赖另一分支 ORM 已改用 `tool_call_id`；当前 ORM 使用 `langgraph_tool_call_id`，不得单独重命名数据库列。其 Docker 源码安装步骤也不适用于当前 frozen lock 构建。`75e1ff23` 的宿主源码挂载会替换锁定依赖，故不采用；LITE/full 路由边界继续由当前公开 OpenAPI 测试验证。

## 替代方案

直接 cherry-pick 两个提交会破坏 ORM 列契约及 AgentScope 构建可复现性，拒绝。只修改镜像标签不会修复 Python 包版本，拒绝。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 发布版本一致 | 后端仍报告旧包版本 | pyproject 与 uv.lock | test_release_version（6 passed）、uv lock --check；临时容器安装后 importlib.metadata 读取 0.7.2 | 各包、锁文件单独及全体退回旧版本 | Passed |
| 保留当前存储和路由契约 | 不匹配的列重命名或 LITE 路由回归 | storage_migration、router | storage migration unit、LITE/full unit（10 passed） | 现有边界测试 | Passed |

工程门禁和 61 项门禁单测通过，文档构建通过但存在 VitePress/Rolldown 警告。完整后端 unit 在 Compose 环境中为 1596 passed、3 failed；失败位于配置目录环境变量覆盖的 fresh-process 测试，隔离运行目录后该文件 16 项全部通过，未将两次结果合称完整单测通过。

## 后果

已运行的镜像不会自动刷新已安装的 Python distribution 元数据；需要重新构建或重新安装本地包后再检查服务版本。不得把文件版本修复写成已重建全部容器。
