# 历史对话刷新后恢复所选模型

状态：implemented
类型：bug-fix
Owner：backend/package/yuxi/services/agent_request_queue_service.py

## 问题

用户在历史对话中选择 `glm-5`，刷新页面后模型选择器变为默认 qwen。前端仅在内存中更新 Conversation metadata；后端接收 Run 时没有把已解析的 `model_spec` 保存到 Conversation。历史消息恢复函数又把结果写入固定的 `model_spec` 键，而选择器按 `threadId` 读取，导致刷新后丢失选择。

## 决策

接受非 rejected 请求时，在同一个线程锁和数据库事务内把已解析的 `model_spec` 写入 Conversation metadata。历史接口从 AgentRun `input_payload` 投影每条用户消息实际使用的模型，为存量 Conversation 提供恢复来源。前端按 `threadId` 保存从历史消息恢复的最近模型。

## 替代方案

- 只把模型选择保存在浏览器本地状态：刷新或换设备后仍丢失，也不能表达服务端实际执行模型。
- 只修复前端线程键：新对话在 Conversation metadata 中仍没有模型，存量历史也缺少可靠恢复来源。
- 把 `model_spec` 复制进 Message 持久化 metadata：会复制 AgentRun 已拥有的执行事实；历史接口按需投影即可。

## 验收标准

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 已接受请求保存线程模型 | 刷新后 Conversation 无模型 | request intake / Conversation | queue unit | rejected 请求不得改模型 | Passed |
| 存量历史可恢复实际模型 | 老 Conversation metadata 为空 | history projection | history unit | Run 无 model_spec 时不伪造 | Passed |
| 前端按线程恢复模型 | 写入固定键导致当前线程读不到 | conversation model binding | web unit | 使用当前 `threadId` 作为状态键 | Passed |

## 后果

新请求被接受后，线程列表接口直接返回最近选择的模型。存量对话即使 Conversation metadata 为空，也能从历史 AgentRun 恢复每条用户消息实际使用的模型；前端取最近一条并写入当前线程键。拒绝请求不改变线程模型，各个已排队 Run 继续使用自身不可变的 `input_payload.model_spec`。

## 风险

同一线程可有多个排队请求选择不同模型。Conversation metadata 表达最近一次被接受的用户选择；每条 Run 的执行模型仍由各自不可变 `input_payload.model_spec` 决定，不能反向用 Conversation metadata 改写已排队 Run。

## 验证

- `PYTHONPATH=backend/package python3 -m pytest backend/test/unit/services/test_conversation_queue_history.py backend/test/unit/services/test_agent_request_queue_service.py -q`：51 passed，1 warning。
- `pnpm run test:unit`：201 passed。
- `pnpm run lint:check`：通过。
- `pnpm run build`：通过；保留既有大 chunk 警告。
- `python3 -m ruff check`：生产代码和新增测试逻辑通过；历史测试文件原有 4 行 E501 未在本次格式化。
- `git diff --check`：通过。
- 真实浏览器刷新与数据库回读：Not run，本机 Compose 缺少必需的 `AGENTSCOPE_CHANNEL_CREDENTIAL_KEY`。
