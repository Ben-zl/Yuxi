# 知识库描述润色模型归属

状态：implemented
类型：bug-fix
Owner：backend/server/routers/knowledge_router.py

## 问题

知识库“润色”是系统辅助生成操作，原实现却调用系统默认对话模型。管理员切换默认对话模型后，润色会跟随该选择并可能因模型未启用或连接失败返回 500。模型返回空文本时，后端还会返回成功对象，前端只能再次显示模糊失败。

## 决策

润色使用系统配置中的 `fast_model`，与普通对话默认模型解耦。模型调用明确关闭流式输出，空响应作为上游模型无效响应返回 502。后端日志保留具体异常，对外 500 使用稳定错误信息。

## 替代方案

- 继续使用 `default_model`：普通对话模型选择会继续影响独立的辅助生成能力。
- 在 `fast_model` 失败后静默改用其他模型：会隐藏管理员配置错误并产生不可预测的供应商调用。
- 只修改前端错误提示：不能恢复模型调用，也不能修复成功空响应。

## 后果

管理员通过系统“快速响应模型”配置控制知识库描述润色使用的模型。`fast_model` 缺失、未启用或连接失败时请求明确失败且服务端记录原因；浏览器和其他 API 客户端不会收到供应商异常详情。模型返回空白或非文本内容时返回 502，不更新当前描述。

## 验证

- `pytest test/unit/routers/test_knowledge_description.py -q`：3 passed，1 warning。
- `pytest test/unit -m "not slow" -q`：1661 passed，4 warnings。
- `python3 -m ruff check`：本次后端源码和测试通过。
- `python3 scripts/verify_engineering_contracts.py`：通过。
- `python3 -m unittest scripts.test_verify_engineering_contracts`：通过。
- `git diff --check`：通过。
- 截图环境浏览器与 API 日志：Not run；从当前网络打开 `10.11.144.98:5180` 超时，无法核对该环境的 `fast_model` 与供应商连接。
