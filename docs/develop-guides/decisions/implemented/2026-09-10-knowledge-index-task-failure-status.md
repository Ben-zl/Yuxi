# 文档入库作业以单文件失败决定终态

状态：implemented
类型：bug-fix
Owner：backend/server/routers/knowledge_router.py

## 问题

指定文件入库和按状态批量入库会捕获单文件异常并记录 `failed` 数量，但函数随后正常返回。Tasker 因协程未抛出异常而把作业标记为 `success`，任务中心显示“已完成”，与文件实际 `error_indexing` 状态冲突。

## 决策

两个入库执行函数继续保存逐文件结果、处理数和失败数；当 `failed > 0` 时，在写入最终进度和结果后抛出汇总异常，使 Tasker 将作业标记为 `failed`。全部成功时保持现有返回值和成功终态。

前端提交提示使用中性信息样式表达“入库任务已提交”，不在后台作业完成前显示绿色成功反馈。任务中心依据后台作业真实终态展示成功或失败。

## 替代方案

- 只修改任务中心文案：无法修正 Tasker 的成功计数、失败筛选和 API 终态。
- 遇到首个文件失败立即中止：会阻止同批次其他文件继续入库，降低批处理可恢复性。
- 修改 Tasker 根据任意 `result.failed` 猜测终态：会把通用调度器绑定到各业务结果结构。

## 后果

部分失败和全部失败的入库作业都会进入失败终态，但已成功的文件保持已入库，失败文件和逐项错误保留在作业结果中。调用方可继续针对失败文件重试。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 指定文件任一入库失败时作业失败 | 单文件异常被捕获 | `_run_index_file_ids` | router unit | 1 成功、1 失败 | Passed |
| 待入库批次任一失败时作业失败 | 批量异常被捕获 | `_run_index_pending_statuses` | router unit | 1 成功、1 失败 | Passed |
| 失败前保存逐文件结果 | 抛异常丢失结果 | TaskContext result | router + Tasker unit | `failed == 1` 且 items 保留 | Passed |
| 全部成功保持成功 | 修复误伤正常路径 | existing router unit | 两文件成功 | Passed |
| 提交阶段不提前显示成功 | 后台作业尚未执行 | database store | web unit | 两个入库入口均使用 info 提示 | Passed：204 tests |
| 截图环境终态正确 | 远端 worker / PostgreSQL / 页面 | live API/browser | Task 与文件状态回读 | 文件失败时不得显示已完成 | Not run |

## 运行记录

- 首次运行两个新增负向测试：2 failed，均因入库执行函数未抛出异常，复现任务被误判成功的原因。
- 修复后运行 `test_knowledge_router_cleanup.py` 与 `test_tasker_behavior.py`：37 passed，1 warning。
- 相关 Ruff 检查：All checks passed。
- `pnpm --dir web run lint:check`：Passed。
- `pnpm --dir web run test:unit`：204 passed。
- `pnpm --dir web run build`：Passed，保留既有大 Chunk 告警。
- `git diff --check`：Passed。
- 截图环境 `http://10.11.144.98:5180` 当前不可访问，未执行真实 API、PostgreSQL、worker 与浏览器回归。
