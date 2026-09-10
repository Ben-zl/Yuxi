# 附件弹窗请求契约修复

状态：implemented
类型：bug-fix
Owner：web/src/components/AttachmentTmpUploadModal.vue

## 问题

附件确认请求遗漏后端必填的 file_name、bucket_name，解析请求遗漏 file_name。422 detail 数组直接传入消息组件后显示为 [object Object]。

## 决策

弹窗保留上传响应中的 bucket_name，并在解析、确认时提交文件名和存储桶。错误展示优先使用 API 层提供的安全 message，只接受字符串 detail，否则使用操作默认提示。失败时保留附件并恢复确认按钮，允许重试。

## 替代方案

放宽后端字段会改变现有接口契约与调用者责任；修复沿用当前实际路由调用的 conversation_service，不修改后端存储、权限或接口。

## 后果

改动限定在附件弹窗及其行为回归测试。测试运行真实组件脚本与 Vue 响应式逻辑，在 API 边界使用替身，因此不能证明对象存储或数据库落库成功。

## 验证

- `node --test web/test/unit/attachmentTmpUpload.test.js`：原实现三个案例均因目标缺陷失败，修复后三项 Passed，覆盖确认字段、解析字段和解析对象传递、422 安全文本及失败状态保留。
- `cd web && pnpm run test:unit`：200 passed；测试过程中存在 Vue inject 使用上下文警告。
- `cd web && pnpm run lint:check`：Passed。
- `cd web && pnpm run build`：Passed；存在大于 500 kB 的分包警告。
- `python3 scripts/verify_engineering_contracts.py`、`python3 -m unittest scripts.test_verify_engineering_contracts`、`git diff --check`：Passed，契约检查测试 61 项通过。
- 当前源码挂载的 Web 页面和 API readiness 均返回 HTTP 200；运行中 OpenAPI 的确认和解析必填字段与补齐的请求一致。这些检查不代表附件上传成功。
- 浏览器真实上传、PDF/图片解析、数据库和文件回读：Not run，Mac 锁屏导致浏览器工具无法访问页面。
- 本修复不修改后端；组合提交前的后端全量 unit 验证通过，integration/E2E 未运行。
