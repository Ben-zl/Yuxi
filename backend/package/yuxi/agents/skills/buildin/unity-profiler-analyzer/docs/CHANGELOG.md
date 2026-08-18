# Unity Profiler Analyzer - 变更历史

## 版本历史

| 版本 | 日期 | 变更内容 |
|------|------|----------|
| v3.12 | 2026-01-28 | **合并 jank_frames 和 big_jank_frames**：卡顿分析现在同时获取并合并 `get_jank_frame` 接口返回的 `jank_frames`（普通卡顿帧，帧时间 > 33ms）和 `big_jank_frames`（严重卡顿帧，帧时间 > 100ms）两个数组；使用 set 去重确保不重复分析同一帧；保留原有数组顺序；更新 `JankCollector.collect()` 方法的合并逻辑；更新 `jank-analysis.md` 文档说明数据结构和合并逻辑 |
| v3.11 | 2026-01-28 | **截图默认显示为图片**：卡顿分析报告默认将截图以图片形式显示，而非仅显示链接；AI 报告提示词模板更新，使用 Markdown 图片语法 `![帧 #XXX 截图](url)` 直接显示截图；每个卡顿帧包含"查看大图"链接（在新标签页打开原图）和截图图片；更新 `jank-analysis.md` 的 AI 提示词模板和输出示例；更新 `SKILL.md` 中截图功能说明 |
| v3.10 | 2026-01-28 | **基于时间戳的截图匹配**：新增 `collect_with_timestamp_screenshots()` 方法实现基于时间戳的截图匹配；使用 `get_original_files()` API 获取原始 profiler 数据作为校准点；使用 `get_case_info()` API 获取 frameTime.frames 数据计算每帧时间戳；每 300 帧使用原始文件数据进行校准；通过计算的时间戳匹配截图而非帧号；CLI 新增 `--use-timestamp-screenshots` 和 `--max-screenshot-offset` 选项；`_parse_raw_filename()` 解析 `{timestamp}_{frame}.raw.zip` 格式文件名；`_calculate_frame_timestamps()` 计算所有帧的时间戳；`_find_screenshot_by_timestamp()` 按时间戳查找最近截图 |
| v3.9.2 | 2026-01-28 | **截图匹配范围扩大**：将截图匹配范围从 100 帧扩大到 200 帧；修复 `ProfilerAnalyzer` 中 `base_url` 包含 `/api` 后缀导致的双重路径问题；修复 `_get_screen_list()` 和 `_find_screenshot_for_frame()` 对 URL 列表数据格式的处理；确保截图 API 正常工作并返回有效 URL |
| v3.9.1 | 2026-01-28 | **截图匹配优化**：改进 `_find_screenshot_for_frame()` 方法，使用最近匹配算法而非精确匹配；查找最接近目标帧的截图（100 帧范围内）；如果截图帧号与目标帧不同，在 URL 中添加 `#frame_offset=N` 标识偏移量；确保即使没有精确匹配的截图也能显示最接近的截图 |
| v3.9 | 2026-01-28 | **截图支持**：在 ubox-api 的 CPUAPI 中新增 `get_screen_list()` 方法用于获取截图列表；在 `JankCollector` 中新增 `collect_with_screenshots()` 方法支持获取截图数据；CLI 工具新增 `--with-screenshots` 选项用于启用截图功能；`JankReporter` 更新以包含 screenshot_url 字段；在 `jank-analysis.md` 中添加截图 URL 格式说明和使用示例；在 SKILL.md 中更新卡顿分析部分说明截图功能；截图 URL 格式：`https://minio-cluster.testplus.cn/{project_id}/{case_id}/screenshot/{timestamp}_{frame}.jpg` |
| v3.8 | 2026-01-27 | **配置文件支持**：新增配置文件功能支持自定义目录；扩展 `Config` 类添加 `temp_dir` 和 `reports_dir` 字段；新增 `from_config_file()` 方法支持从 `.upa-config.json` 加载配置；支持 3 个配置文件搜索位置（当前目录、用户主目录、Skill 目录）；添加环境变量 `UPA_TEMP_DIR` 和 `UPA_REPORTS_DIR` 支持；新增 `get_temp_dir()` 和 `get_reports_dir()` 辅助方法；创建 `.upa-config.json.example` 配置示例；在 SKILL.md 和 file-management.md 中添加配置文件使用说明 |
| v3.7 | 2026-01-27 | **默认值调整**：将 `--top` 参数的默认值从 10 改为 20；卡顿分析现在默认获取 Top 20 卡顿帧；更新 CLI 工具帮助文本和文档说明 |
| v3.6 | 2026-01-27 | **文件管理文档化**：将文件管理规范从 SKILL.md 拆分为独立文档 [docs/file-management.md](docs/file-management.md)；在工作流中强调必须先查阅文件管理规范；在参考文档中突出显示文件管理规范为必读文档（3 星标记）；明确所有分析类型都必须遵守统一的文件管理规范 |
| v3.5 | 2026-01-27 | **文件管理重构**：新增"文件管理"章节，详细规定临时文件和最终报告的存放规则；临时文件统一存放于 `.upa_temp/{case_id}/` 目录，包含命名规范、清理策略和管理建议；报告文件增加时间戳（`YYYYMMDD_HHMMSS`），完善版本管理和归档策略；新增 AI Agent 报告生成完整工作流和示例代码；明确临时文件与缓存的区别 |
| v3.4 | 2026-01-27 | **架构重构**：完全隔离各个 Top N 维度的统计逻辑，避免相互影响；为每个排序维度（self_time、total_time、valid_frame_avg、high_cost_frames 等）创建独立的数据处理管道；新增 `_collect_by_dimension()` 方法和 8 个维度特定的收集器方法；每个维度有独立的合并策略和排序逻辑 |
| v3.3 | 2026-01-27 | **修正**：高耗时帧数维度简化排序规则，只按 `high_frame` 排序，不再按 `totaltime_max` 二次排序；显示字段改为 `high_selftime`（高耗时帧平均耗时），移除"最大耗时"列 |
| v3.2 | 2026-01-27 | **修正**：有效帧平均耗时维度改用 `selftime_avg`（有效帧自身平均耗时）字段，而非 `totaltime_avg`；更新所有相关文档和表格说明 |
| v3.1 | 2026-01-26 | **明确说明**：明确有效帧平均耗时维度的两步筛选逻辑（前 40% valid_frame_count → totaltime_avg）；强调 `totaltime_avg` 直接使用 API 返回值，无需计算；移除 `--min-calls` 阈值参数 |
| v3.0 | 2026-01-26 | **重大更新**：强调热点函数分析必须获取所有 4 个排序维度的数据；在 SKILL.md 中添加详细的数据获取流程说明；在 hotspot-analysis.md 开头添加重要要求章节；更新 AI 报告生成提示词，要求基于所有维度生成综合报告；添加跨维度对比分析要求 |
| v2.9 | 2026-01-26 | **简化**：简化 URL 解析描述，删除详细的 Python 代码实现和用户对话示例，只保留核心说明和使用示例 |
| v2.8 | 2026-01-26 | **重要更新**：强调 AI Agent 执行不同类型分析时必须查阅对应类型的 md 文档；在工作流中添加"查阅对应模块的 md 文档"关键步骤；在"分析类型详解"中为每个类型添加"📖 必须先查阅"标识；更新"参考文档"部分，明确每个文档包含的内容（AI 提示词模板、报告结构等） |
| v2.7 | 2026-01-26 | **修复**：由于 `python -m unity_profiler_analyzer` 无法找到模块（包结构限制），改为使用 `python scripts/cli.py` 直接调用；更新所有命令示例 |
| v2.6 | 2026-01-25 | **新增**：添加 `--jank` CLI 选项，直接获取卡顿帧数据；新增 `JankCollector` 类，支持逐帧分析卡顿原因；更新 `jank-analysis.md` 文档，添加两种卡顿分析方式对比说明 |
| v2.5 | 2026-01-24 | **新增**：添加报告保存功能，AI Agent 在生成分析报告后自动保存到本地文件系统；新增文件命名规则和目录结构建议；更新工作流添加报告保存步骤 |
| v2.4 | 2026-01-24 | **新增**：支持从 ubox.testplus.cn URL 自动提取 project_id 和 case_id，添加 URL 解析工具函数和使用示例 |
| v2.3 | 2026-01-24 | **优化**：重构完整分析文档，改为引用其他模块文档，避免重复内容 |
| v2.2 | 2026-01-24 | **新增**：添加完整性能分析功能，整合概况、热点、卡顿、内存等所有分析维度 |
| v2.1 | 2026-01-24 | **重构**：将 AI 报告生成提示词移至各模块文档，SKILL.md 保留工作流描述和引用 |
| v2.0 | 2026-01-24 | **重大更新**：删除 Python 报告生成脚本，改用 AI 提示词生成 Markdown 报告 |
| v1.7 | 2026-01-24 | 修复导入路径问题，支持 Claude Code skill 目录；新增函数名合并功能 |
| v1.6 | 2026-01-23 | 新增按模块分组分析功能，使用 get_func_data_summary 数据 |
| v1.5 | 2026-01-23 | 删除工具直接调用描述，完善模块文档引用 |
| v1.4 | 2026-01-23 | 优化排序逻辑，默认过滤 Unity Entry，使用 get_func_data_summary 数据 |
| v1.3 | 2026-01-23 | 新增数据来源说明和 API 文档引用 |
| v1.2 | 2026-01-22 | 整合工作流到 SKILL.md |
| v1.1 | 2026-01-22 | 按模块拆分文档，创建独立模块文档 |
| v1.0 | 2026-01-22 | 初始 SKILL 文档 |
