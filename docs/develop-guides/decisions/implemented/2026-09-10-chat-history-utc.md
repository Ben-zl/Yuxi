# 会话历史时间输出显式 UTC

状态：implemented
类型：bug-fix
Owner：backend/package/yuxi/services/conversation_service.py

## 问题

数据库消息时间保存为无时区 UTC，历史接口直接 isoformat 输出后，浏览器按本地时间解释，上海时区显示早八小时。

## 决策

会话及历史响应的数据库日期复用 format_utc_datetime 输出带 Z 的 ISO 时间，覆盖消息、失败回复和反馈。前端继续使用既有上海时区格式化；数据库时间值保持不变。

## 替代方案

前端加八小时会破坏已有显式时区数据，修改通用日期解析会影响其他输入，均不采用。

## 后果

UTC 时区信息在序列化边界保留；存量消息无需迁移。数据按模型的 UTC 契约解释，绕过模型写入的异常历史数据不属于本修复范围。

## 验证

- 原历史服务回归测试新增时间断言后，普通消息与失败回复两个案例因缺少 Z 失败；修复后通过。
- 在挂载当前源码的 API 容器执行 `python -m pytest test/unit/services/test_conversation_queue_history.py test/unit/services/test_conversation_thread_status.py test/unit/utils/test_datetime_utils.py -q -o cache_dir=/tmp/pytest-timestamp-cache`：29 passed，存在 SQLAlchemy declarative_base 弃用警告。
- `TZ=Asia/Shanghai node --test web/test/unit/time.test.js` 与 `TZ=UTC node --test web/test/unit/time.test.js`：各 7 passed；覆盖 UTC 08:07 → 上海 16:07、显式 +08:00 不重复换算和跨日。
- 真实 PostgreSQL 只读回读：消息 UTC 12:19:09 经历史服务输出 12:19:09Z；本地 Chrome 加载同一历史消息显示“周二 20:19”，截图核对通过。没有生成新模型对话。
- `ruff check backend/package/yuxi/services/conversation_service.py`：Passed。`ruff format ... --check`：未通过，报告原有两处多行表达式格式差异，本修复不扩大格式化范围。
- `python3 scripts/verify_engineering_contracts.py`、`python3 -m unittest scripts.test_verify_engineering_contracts`、`git diff --check`：Passed；工程检查测试 61 项通过。
- 容器内 `uv run --group test pytest ...` 因系统 site-packages 文件权限失败，改用已安装的 `python -m pytest` 完成相关测试；容器未安装 ruff，使用宿主机 ruff。
- 组合提交前后端全量 unit：1641 项直接通过，3 项按所需工作目录与配置目录重跑后通过；integration/E2E 未运行。
- 用户截图中的服务器环境：Not run；本地证据不代表远端已更新。
