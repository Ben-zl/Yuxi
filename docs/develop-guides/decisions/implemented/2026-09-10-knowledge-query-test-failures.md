# 知识库检索测试失败语义

状态：implemented
类型：bug-fix
Owner：backend/package/yuxi/knowledge/implementations/milvus.py

## 问题

Milvus 查询链路把集合、Embedding、检索和来源回填异常统一记录后返回空列表，查询测试因此把运行故障显示为“未找到相关结果”。示例问题生成只接受可直接 `json.loads` 的对象；模型在 JSON 前输出 reasoning 标签时，生成请求返回 500。

## 决策

Milvus 集合初始化和查询运行异常继续向上传递，只有检索成功且没有命中时返回空列表。`query-test` 路由把运行异常映射为 HTTP 500，使前端进入已有错误提示分支。示例问题解析移除模型 `<think>` reasoning 标签，从 JSON 代码块或首个完整 JSON 对象、数组中解析，并验证结果为非空字符串问题列表。

## 替代方案

- 保留空列表并只增加日志：页面仍无法区分零命中与基础设施故障，用户没有可观察的失败状态。
- 前端识别 `{status: "failed"}`：路由仍返回 200，其他调用方容易继续把故障当作成功结果。
- 示例生成失败时使用固定模板问题：会把模型故障伪装为成功，并产生与文档内容无关的问题。

## 后果

查询成功但没有命中时仍返回空列表。集合、Embedding、Milvus 搜索、来源回填等运行故障返回失败 HTTP 状态，服务端日志保留具体异常，浏览器显示统一的服务器错误提示。示例问题生成兼容 qwen reasoning、Markdown JSON 代码块、JSON 对象和 JSON 数组；非法 JSON、空列表、非字符串问题继续失败。

真实环境仍需回读知识库的 `embedding_model_spec`、文件索引状态和模型缓存，才能确定截图环境中的底层失败是模型配置、Milvus 连接还是未完成索引。本修复保留这些状态的真实语义，不自动修改或重新导入知识库。

## 验证

- `pytest test/unit/knowledge/test_sample_question_utils.py test/unit/plugins/test_milvus_kb.py test/unit/routers/test_knowledge_query_test.py -q`：28 passed，3 warnings。
- `pytest test/unit -m "not slow" -q`：1658 passed，4 warnings。
- `python3 -m ruff check`：本次后端源码和测试通过。
- `python3 scripts/verify_engineering_contracts.py`：通过。
- `python3 -m unittest scripts.test_verify_engineering_contracts`：61 passed。
- `git diff --check`：通过。
- 截图环境 API、PostgreSQL、Redis、Milvus 和浏览器回归：Not run；当前没有该环境的认证日志访问，本机桌面浏览器锁屏。
