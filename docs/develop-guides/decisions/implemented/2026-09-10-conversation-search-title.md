# 对话搜索同时匹配标题与正文

状态：implemented
类型：bug-fix
Owner：backend/package/yuxi/repositories/conversation_repository.py

## 问题

全局对话搜索只按用户和助手消息正文执行包含匹配，没有搜索对话标题。标题中存在完整关键词、正文中只有更短字符时，较长查询反而没有结果，较短查询则偶然命中正文，造成结果不一致和遗漏。

## 决策

搜索条件改为对话标题或可搜索消息正文任一命中。正文聚合子查询和外层对话查询都应用用户、状态、智能体及隐藏来源过滤，避免高频短词聚合其他租户消息。正文命中继续统计匹配消息并展示命中片段；只有标题命中时返回最近一条可搜索消息作为摘要。

排序使用最近正文命中时间；纯标题命中回退到对话更新时间。同一字段继续使用转义后的包含匹配，因此“询问”的命中集合不会因改用更短前缀“询”而丢失。

## 替代方案

只在前端过滤最近对话无法覆盖分页外历史，也无法搜索正文；把标题复制进消息会污染会话事实；模糊分词或拼音搜索超出本次包含匹配缺陷范围。

## 后果

标题与消息正文现在都是对话搜索入口。纯标题命中的 `matched_count` 为 0，但会返回最近消息摘要；现有 API 字段和前端渲染不需要改变。查询继续执行 `%keyword%` 包含匹配，数据量显著增长后的全文索引优化不属于本次修复。

## 验证

- 新增 repository 回归测试，原实现对标题“询问AI功能范围”、正文不含“询问”的会话返回空；修复后先红后绿。
- `pytest test/unit/storage/test_conversation_repository.py -q`：10 passed，覆盖标题命中、前缀结果包含关系、用户与状态隔离、隐藏来源、智能体过滤和分页。
- 本地 PostgreSQL 真实数据回读：“询问”返回 1 条，“询”返回 8 条，前者 Thread 集合是后者子集；纯标题包含“查询”的历史对话也进入短关键词结果。
- `ruff check`：Passed；`ruff format --check` 对本次修改后的文件 Passed。
- `python3 scripts/verify_engineering_contracts.py`、`python3 -m unittest scripts.test_verify_engineering_contracts`、`git diff --check`：Passed。
- 截图中的远端 `10.11.144.98:5180` 页面：Not run，本机当前无法访问该远端环境；未部署。
