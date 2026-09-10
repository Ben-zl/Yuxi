# 图片输入遵循模型能力

状态：implemented
类型：bug-fix
Owner：backend/package/yuxi/models/providers/cache.py

## 问题

对话是否允许上传图片只取决于智能体的文件能力，没有校验本轮模型是否支持图像输入。文本模型会收到无法消费的图片内容，用户界面仍显示图片已发送，最终模型声称没有收到图片。

## 决策

模型缓存和模型列表保留 `input_modalities`。前端在发送图片前先读取服务端模型元数据，再用本地模型目录补充未知能力；明确不支持图片时阻止发送、恢复图片预览并提示选择视觉模型。模型目录加载失败不影响服务端已经明确的能力。服务端在 Run 接入边界执行同一项确定性校验，防止其他入口静默丢弃图片。

模型列表发现 Redis 旧缓存与 PostgreSQL 已声明的输入模态不一致时重建缓存。Run 接入在缓存字段缺失时回读 PostgreSQL。模型能力仍未知时保留现有行为，避免误伤未声明元数据的自定义视觉模型。

## 替代方案

自动切换模型会改变用户明确选择及成本；只在前端校验无法覆盖 API 等入口；一律拒绝能力未知的自定义模型会误伤未声明元数据的视觉模型。

## 后果

使用纯文本模型发送图片时，前端不会创建 Thread/Run，图片继续留在输入区。绕过前端的调用在供应商配置明确声明纯文本能力时收到 422。模型目录或供应商配置没有提供模态信息时仍允许发送，因此自定义供应商应保存准确的 `input_modalities`。

## 验证

- `pytest test/unit/services/test_model_cache.py test/unit/services/test_agent_run_service.py test/unit/services/test_agent_request_queue_service.py test/unit/server/test_model_provider_router.py test/unit/test_agentscope_protocol.py -q`：126 passed；另有 1 个既有 SQLAlchemy 弃用警告和 1 个容器 pytest cache 权限警告。
- `cd web && pnpm run test:unit`：200 passed；模型能力测试覆盖文本模型、视觉模型、未知模型、不同供应商下相同模型 ID 的目录回退，以及模型目录加载失败时继续使用服务端明确能力。
- `pnpm run lint:check`：Passed。
- `pnpm run build`：Passed；保留既有 chunk size 与 Vue compiler 提示。
- `ruff check`：Passed。
- `python3 scripts/verify_engineering_contracts.py`、`python3 -m unittest scripts.test_verify_engineering_contracts`、`git diff --check`：Passed，契约检查测试 61 项通过。
- AgentScope 请求体测试确认图片以 `data/base64` 内容块发送，并保留 `media_type`。
- 真实浏览器点击验证：Not run，macOS 锁屏导致浏览器工具无法访问页面。
- 本次诊断创建的本地测试会话已软删除，临时图片已删除。AgentScope 会话删除接口因挂起会话连续超时，未重启共享本地服务，底层测试 Session/Workspace 清理未验证。
