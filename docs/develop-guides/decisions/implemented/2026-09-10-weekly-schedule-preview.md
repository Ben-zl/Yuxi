# 每周定时预览跟随当前选择

状态：implemented
类型：bug-fix
Owner：web/src/components/agent-tasks/AgentTaskEditModal.vue

## 问题

星期按钮新增选项使用数组 push，原监听只追踪数组引用，导致新增星期不刷新预览。异步预览缺少请求顺序约束，较早的响应可能覆盖最新配置，清空星期产生的失败又使面板无提示消失。

## 决策

监听星期值的快照，并将弹窗打开状态纳入预览依赖。每次配置变化递增请求序号并清空旧结果，只有最新请求可以发布预览、错误与加载状态。关闭弹窗、关闭定时或每周无选项时使在途结果失效，不请求无星期的预览。

预览面板明确展示未选星期、计算中、失败及无结果状态，正常结果继续使用服务端的五次执行时间。

## 替代方案

仅改用新数组能解决新增星期漏刷，但无法处理异步响应倒序；仅添加 deep 监听也无法保证最新结果，因此同时约束输入追踪与结果发布。

## 后果

预览属于当前表单状态，不修改任务存储或调度规则；不新增依赖。旧请求仍可完成，但结果不再更新当前表单。

## 验证

- `node --test web/test/unit/taskSchedulePreview.test.js`：原实现三个案例因新增星期不刷新、旧响应覆盖和关闭后回填失败；修复后三项 Passed。
- `cd web && pnpm run test:unit`：200 passed。
- `cd web && pnpm run lint:check`、`cd web && pnpm run build`：Passed；构建存在大于 500 kB 的分包警告。
- 本地 Chrome 与实际预览 API：周一增加周二后返回交替的周一、周二日期；取消全部星期显示明确提示；重新选择周三返回五个周三日期，Passed。测试没有保存任务或触发执行。
- `python3 scripts/verify_engineering_contracts.py`、`python3 -m unittest scripts.test_verify_engineering_contracts`、`git diff --check`：Passed。
- 远端部署及实际定时触发：Not run，本变更只修复预览交互。
