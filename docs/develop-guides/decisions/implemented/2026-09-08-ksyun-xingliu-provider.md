# 金山云星流模型供应商

状态：implemented
类型：feature
Owner：backend/package/yuxi/models/providers/builtin.py

## 问题

现有内置模板没有金山云星流，通用发现逻辑无法区分其混合清单的模型用途。

## 决策

新增 `ksyun` 内置供应商，展示名称为金山云星流，使用 `https://kspmas.ksyun.com/v1`、`KSC_API_KEY` 和 OpenAI 兼容协议。复用现有模型管理、凭证解析和 AgentScope Credential；默认停用，管理员选择并启用模型。

Models API 混合返回文字、图片、视频和语音模型。当前接入聊天、`qwen3-embedding-8b` 和 `qwen3-reranker-8b`。后两者的发现元数据也声明文字输出，按已核实的模型 ID 分别映射为 embedding（4096 维）与 rerank；其他条目依据显式类型与文字输出模态识别。文档旧版没有模态字段的记录不猜测能力。星流的中文模态及带 k/m 的 Token 上限在供应商发现边界转换，原始响应保留在 raw_metadata。

## 替代方案

独立新增协议类型会重复 OpenAI 与 AgentScope 装配逻辑；固定模型白名单无法随账号授权变化。使用内置供应商模板与针对该供应商的元数据转换。

## 验证

- 临时 Docker 容器挂载当前源码运行 `python -m pytest test/unit/services/test_ksyun_provider.py test/unit/services/test_model_provider_service.py test/unit/test_agentscope_runtime_models.py -q -o cache_dir=/tmp/pytest-cache`：44 passed；覆盖非聊天与畸形模态过滤、非法长度及 401 传播。
- 真实 AgentScope 工具闭环通过：调用 `add_numbers(17, 25)`，执行结果 `42` 回传后模型正确作答。
- 当前改动的 Ruff check/format 和 `git diff --check` 通过。
- 初始真实 Models API 返回 56 个文字输出模型（含两个检索模型，不能都算作聊天）；`qwen-flash` 非流式回复 `KSYUN_OK`，AgentScope 流式回复 `KSYUN_STREAM_OK`。
- 真实 PostgreSQL 隔离 Schema 验证内置模板初始化、默认停用、模型保存、重复初始化保留管理员配置、独立事务回读及 AgentScope Credential 投影。真实 Key 仅在进程内参与请求，测试 Schema 完成后删除。
- `python3 -m unittest scripts.test_verify_engineering_contracts`：61 passed。
- 文档构建 `node docs/node_modules/vitepress/bin/vitepress.js build docs` 失败：现有 VitePress 1.6.4 与 Vite 8 / Rolldown 的 bundle mutation 不兼容；依赖未变更。
- 浏览器完整会话 / Run / SSE E2E 未运行；现有旧槽位挂载已删除的 worktree，本次只使用挂载当前源码的临时容器，不更新该服务。

## 检索模型验证

`test_ksyun_text_modalities_do_not_turn_retrieval_models_into_chat` 在修复前因 embedding 被标记为 chat 失败；修复后星流与供应商服务测试共 37 passed。真实调用复用 `OtherEmbedding.aencode` 与 `OpenAIReranker._batch_rerank`：两条输入各返回 4096 个有限数值且向量不同，查询“中国的首都是哪里？”时相关文档得分 0.776 高于无关文档 0.490，结果保持输入顺序。

当前本地实例通过模型管理 PUT API 补齐 capabilities、专用调用端点及两条已启用模型的 type/维度；GET 回读与远程发现均返回 embedding/rerank，原凭据与其他模型配置保留。内置同步保持不覆盖管理员设置；已存在的实例需要在管理页面按本文配置补齐能力、端点和类型。

## 后果

发现接口缺少输出模态时隐藏该条目，管理员需核对后手动添加。真实推理验证只代表测试模型和当前账号，不覆盖全部模型及浏览器 Run 生命周期。

## 来源

- [星流模型 API 服务简介](https://docs.ksyun.com/documents/44740?type=3)
- [Models API](https://docs.ksyun.com/documents/45237?type=3)

## 2026-09-10 提交前复核

- 工程契约检查通过；检查脚本 unittest：61 passed。
- 当前 Docker API 容器通过 `python -m pytest test/unit/services/test_ksyun_provider.py test/unit/services/test_model_provider_service.py test/unit/test_agentscope_runtime_models.py test/unit/test_agentscope_runtime_system_options.py -q`：48 passed。
- Compose API 内规定的 `uv run --group test pytest` 因 site-packages 写权限失败；使用容器现有 Python 执行 `python -m pytest test/unit -m "not slow"`：1570 passed、66 failed。失败涉及容器缺失仓库根配置/脚本、运行配置与 Workspace 环境；全量测试未通过。
- 本次五个 Python 文件 Ruff check/format 通过。全包宿主 Ruff 检查仍有 474 个错误、85 个文件需要格式化；本次未修改这些范围。
- 本次提交前没有重新执行真实模型、浏览器与文档构建；上文真实调用记录是此前验证证据。
