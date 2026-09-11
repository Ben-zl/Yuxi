# 知识库文件写入后立即失效聚合统计缓存

状态：implemented
类型：bug-fix
Owner：backend/package/yuxi/repositories/knowledge_file_repository.py

## 问题

知识库文件列表直接查询 PostgreSQL，页面顶部的文件数、待解析、待入库和处理中数量来自 `KnowledgeFileRepository.get_kb_file_stats()` 的 Redis 缓存。该缓存保留 10 秒，但文件新增、删除和状态更新没有失效缓存，因此同一次页面刷新会同时展示最新文件列表和旧统计标签。

## 决策

统计缓存 key 由 repository 内的统一方法生成。同一知识库的缓存未命中回填和文件事实写入使用同一个 PostgreSQL 事务级 advisory lock。读取方取得锁后再次检查缓存，再聚合 PostgreSQL 并在释放锁前回填；写入方在锁内完成文件新增、字段或状态更新、删除或目录迁移，并在事务提交释放锁前删除统计缓存。这样，较早开始的读取要么先回填、随后被写入删除，要么等待写入提交后查询新事实，不能在写入完成后重新写回旧统计。

缓存失效是派生数据维护：Redis 暂时不可用时记录告警，但不撤销 PostgreSQL 文件变更。若数据库事务随后失败，已删除的派生缓存只会在下次读取时按未变化的数据库事实重建。读取路径保持现有短 TTL 缓存。

## 替代方案

- 前端延迟 10 秒再刷新：用户仍会看到短暂错误，且所有调用方都需要理解后端缓存实现。
- 完全删除统计缓存：能保证新鲜度，但会恢复列表页面的高频全表聚合成本。
- 只在 manager 操作完成后强制刷新：后台解析、索引状态更新和其他直接 repository 写入仍会留下旧统计。

## 后果

文件变更返回后，页面现有的详情和列表刷新会读取一致的最新统计，并发旧读不能在写入后重新驻留缓存。每次缓存未命中和文件写入会增加一个知识库级 PostgreSQL advisory lock；锁只覆盖统计聚合或单次文件元数据事务。Redis 故障时最坏仍由 10 秒 TTL 自愈，PostgreSQL 业务事实不受影响。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 结果 |
|---|---|---|---|---|---|
| 上传后文件数和待解析数立即更新 | 新增后命中旧 Redis 值 | `KnowledgeFileRepository.upsert` | repository unit | 缓存为 0，新增 1 个 uploaded 文件 | Passed |
| 删除后文件数和待解析数立即更新 | 删除后命中旧 Redis 值 | `KnowledgeFileRepository.delete` | repository unit | 缓存为 1，删除唯一文件 | Passed |
| 入库状态变化立即更新待处理标签 | 状态更新后命中旧 Redis 值 | `update_fields_if_status` | repository unit | `uploaded` 更新为 `indexed` | Passed |
| 缓存删除与提交位于同一统计锁内 | 旧读在提交后重新回填 | PostgreSQL advisory lock | repository unit + real race probe | 读取先聚合旧值、写入随后竞争同一锁 | Passed |
| 数据库提交失败只丢弃派生缓存 | 缓存删除早于失败提交 | repository transaction boundary | repository unit | context exit 抛出 commit error | Passed |
| 相关知识库行为保持兼容 | repository 写路径回归 | knowledge/plugin tests | targeted pytest：39 tests | 列表、配置更新、Milvus 文件操作 | Passed |
| 真实 PostgreSQL 与 Redis 立即一致 | 单元替身未覆盖真实提交和缓存 | repository / storage | 本地 Compose 存储探针 | 预置旧缓存后新增、删除并回读 | Passed |
| 截图页面删除后标签与列表一致 | 远端 API / PostgreSQL / Redis / 页面 | live runtime | browser + API/Redis readback | 表格 2 条时标签不得仍显示 3 | Not run |

## 风险

每次文件写入增加一次 Redis 删除操作和一个事务级 advisory lock，批量解析和入库会增加同一知识库内的短事务排队。统计聚合与文件元数据事务通常很短；不同知识库使用不同锁键。Redis 故障时记录告警并由 10 秒 TTL 自愈，不回滚 PostgreSQL 业务写入。

## 运行记录

- 新增回归测试首次运行：3 failed，分别返回缓存中的旧文件数、旧删除前数量和旧待解析数量。
- 初次修复后 repository 与相关知识库、Milvus 测试：39 passed，3 warnings。
- Reviewer 发现旧读回填竞态后新增事务锁顺序与 advisory key 测试；真实 PostgreSQL/Redis 交错探针得到 `REAL_STATS_STALE_REFILL_RACE=PASS`，旧读返回 1 后写入完成，下一次读取为 0。
- 前端 unit：204 passed。
- 本地 Compose PostgreSQL/Redis 探针：`REAL_POSTGRES_REDIS_STATS_INVALIDATION=PASS`，上传后 `file_count=1`、删除后 `file_count=0`，临时数据清理通过。
- Ruff、工程契约 61 项、`git diff --check`：Passed。
- 截图环境 `http://10.11.144.98:5180` 于 2026-09-10 访问超时，未执行该环境的浏览器回归。
