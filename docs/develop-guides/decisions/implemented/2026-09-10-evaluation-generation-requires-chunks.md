# 自动生成评估基准要求知识库已有 Chunk

状态：implemented
类型：bug-fix
Owner：backend/package/yuxi/knowledge/eval/service.py

## 问题

自动生成评估基准依赖 PostgreSQL 中已入库的知识库 Chunk。知识库只有待入库文件且 Chunk 数为零时，旧页面仍允许提交；后端先创建空基准并提交后台作业，作业随后以“生成失败、0 题”结束。模型连接异常也曾与模型输出格式错误一起被单题重试逻辑吞掉，最终只暴露有效题目不足。

## 决策

后端在创建数据集和后台作业前读取知识库 Chunk 数；零 Chunk 时同步拒绝请求，不产生数据集或后台作业。自动生成弹窗复用图谱状态接口返回的 `total_chunks`；该值与后端生成入口一样直接统计 `knowledge_chunks`。零 Chunk 时页面展示前置条件提示、禁用确定按钮，并在直接触发提交函数时提示先完成文件入库。预检加载失败时不把未知状态解释为零 Chunk，仍由后端实时计数承担最终校验。

模型调用位于单题 JSON 解析容错边界之外。模型连接和调用异常立即向上传递，只有模型已返回但内容无法形成有效题目时才继续单题重试。

自动生成不会触发文件解析或入库，也不会修改现有知识库内容。用户完成文件入库并重新打开自动生成弹窗后，页面依据实时 Chunk 数解除限制；后端实时计数仍是最终边界。

## 替代方案

- 只依赖后台作业失败：会持续制造无效的失败基准，用户只能事后发现前置条件不足。
- 自动触发文件解析与入库：会把评估操作扩展成知识库内容写入流程，状态、权限和失败恢复边界更复杂。
- 只做前端禁用：API 调用和旧客户端仍可创建必然失败的数据集。

## 后果

零 Chunk 的请求会在同步接口阶段得到明确错误，页面不会新增“0 题、生成失败”的基准。Chunk 统计在前端可能短暂滞后，因此后端实时计数继续承担最终校验。模型服务异常会直接结束后台作业并保留原始失败原因，模型输出格式不合格仍按既有上限重试。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 结果 |
|---|---|---|---|---|---|
| 零 Chunk 不创建数据集和后台作业 | 文件尚未入库 | evaluation service | targeted backend unit | repository 返回 0 | Passed：35 tests |
| 模型调用异常保留真实失败 | 模型服务不可用 | benchmark generation | targeted backend unit | `llm.call` 抛出异常 | Passed：35 tests |
| 页面提交前说明前置条件 | 0 Chunks | generation modal | web source unit | 警告可见、确定按钮禁用、直接调用被阻止 | Passed：203 tests |
| 页面预检与后端使用相同 Chunk 事实 | 文件聚合统计漂移 | graph status → generation modal | web source unit | 使用实时 `total_chunks`，请求失败时不误判为零 | Passed：203 tests |
| 已有 Chunk 保持原生成流程 | 正常知识库 | service + web | existing generation tests、web build | count > 0 可提交 | Passed |
| 工程与格式约束 | 变更违反项目约定 | repository contracts | engineering contracts、Ruff、ESLint、`git diff --check` | 相关源码或 decision 不合规 | Passed |
| 截图环境生成成功 | 入库、模型、检索与后台作业运行 | live API/browser | 数据回读与页面 | 未入库时不得创建失败基准 | Not run：截图环境不可访问，且截图知识库仍为 0 Chunks |

## 运行记录

- `docker exec -u 0 yuxi-merge-agentscope-api-1 uv run --group test pytest test/unit/knowledge/eval/test_service_generation.py test/unit/knowledge/eval/test_benchmark_generation.py test/unit/knowledge/eval/test_dataset_generation_resume.py`：35 passed，1 warning。
- `docker exec -u 0 yuxi-merge-agentscope-api-1 uv run ruff check ...`：All checks passed。
- `pnpm --dir web run lint:check`：Passed。
- `pnpm --dir web run test:unit`：203 passed。
- `pnpm --dir web run build`：Passed，保留既有大 Chunk 告警。
- `python3 scripts/verify_engineering_contracts.py`：Passed。
- `python3 -m unittest scripts.test_verify_engineering_contracts`：61 passed。
- `git diff --check`：Passed。
- 后端全量非慢速 unit 在复用的 API 运行容器内得到 1597 passed、66 failed；失败集中在容器未挂载仓库根部 Compose/脚本文件，以及该运行容器固定 Workspace 环境覆盖测试临时目录，不能作为隔离全量测试结果。相关评估测试独立运行全部通过。
