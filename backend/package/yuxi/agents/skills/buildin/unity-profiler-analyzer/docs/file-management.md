# 文件管理规范

## 概述

本文档规定 Unity Profiler Analyzer 的文件管理规范，包括临时文件和最终报告的存放规则。

> **⚠️ 强制要求**：所有分析类型（完整分析、概况、热点、卡顿、内存、对比）**必须严格遵守**本文档规定的文件管理规范。

---

## ⚙️ 自定义目录配置

### 配置文件方式（推荐）

您可以使用配置文件来自定义临时文件、报告和缓存目录的位置，而无需修改代码或每次指定路径。

#### 配置文件位置

Unity Profiler Analyzer 会按以下顺序搜索配置文件（找到第一个即停止）：

1. **当前工作目录**: `.upa-config.json`
2. **用户主目录**: `~/.upa/config.json`
3. **Skill 目录**: `unity-profiler-analyzer/.upa-config.json`

#### 配置文件格式

```json
{
  "directories": {
    "temp": ".upa_temp",
    "reports": "reports",
    "cache": "./cache"
  }
}
```

**支持的配置项**：

| 配置项 | 说明 | 默认值 | 示例 |
|--------|------|--------|------|
| `directories.temp` | 临时文件目录 | `.upa_temp` | `".upa_temp"` 或 `"D:/temp/upa"` |
| `directories.reports` | 报告目录 | `reports` | `"reports"` 或 `"D:/documents/upa/reports"` |
| `directories.cache` | 缓存目录 | `./cache` | `"./cache"` 或 `"D:/cache/upa"` |

**路径格式**：
- 支持相对路径（相对于当前工作目录）：`.upa_temp`、`reports`、`./cache`
- 支持绝对路径（Windows/Linux/Mac）：`D:/temp/upa`、`/tmp/upa`

#### 快速开始

1. **复制配置文件示例**：
   ```bash
   # 从 skill 目录复制示例配置
   cp .upa-config.json.example .upa-config.json
   ```

2. **编辑配置文件**：
   ```bash
   # Windows (PowerShell)
   notepad .upa-config.json

   # Linux/Mac
   vim .upa-config.json
   ```

3. **自定义目录**：
   ```json
   {
     "directories": {
       "temp": "D:/upa_temp",
       "reports": "D:/upa_reports",
       "cache": "D:/upa_cache"
     }
   }
   ```

4. **验证配置**（运行分析时）：
   ```bash
   python scripts/cli.py analyze <case_id> --jank --verbose
   # 输出会显示使用的目录路径
   ```

#### 配置文件完整示例

完整的配置文件示例请参考：[`.upa-config.json.example`](../.upa-config.json.example)

### 环境变量方式

除了配置文件，您也可以使用环境变量设置目录：

| 环境变量 | 说明 | 默认值 |
|---------|------|--------|
| `UPA_TEMP_DIR` | 临时文件目录 | `.upa_temp` |
| `UPA_REPORTS_DIR` | 报告目录 | `reports` |
| `UBOX_CACHE_DIR` | 缓存目录 | `./cache` |

**设置环境变量**：

```bash
# Windows (PowerShell)
$env:UPA_TEMP_DIR = "D:/upa_temp"
$env:UPA_REPORTS_DIR = "D:/upa_reports"

# Linux/Mac
export UPA_TEMP_DIR="/tmp/upa"
export UPA_REPORTS_DIR="~/upa_reports"
```

**优先级**：配置文件 > 环境变量 > 默认值

### CLI 参数方式

某些分析工具支持 `-o` 参数指定输出文件，但这只影响单个文件的输出位置。

**示例**：
```bash
# 指定输出文件（不影响其他文件的存放位置）
python scripts/cli.py analyze <case_id> --jank -o custom_output.json
```

**注意**：`-o` 参数**不会**影响临时文件和报告的默认存放位置。要统一管理所有文件路径，建议使用**配置文件方式**。

---

## 临时文件管理

### 临时文件类型

AI Agent 在执行分析过程中会生成以下类型的临时文件：

| 文件类型 | 用途 | 示例文件名 |
|---------|------|-----------|
| JSON 数据文件 | CLI 工具输出的原始 JSON 数据 | `overview.json`, `hotspots_self_time.json` |
| 合并数据文件 | 多维度数据合并后的中间文件 | `merged_hotspots.json` |
| 对比数据文件 | 报告对比的中间数据 | `comparison_data.json` |
| 调试输出文件 | 调试和验证用的临时数据 | `debug_{purpose}.json` |

### 临时文件存放规则

**存放位置**：

| 运行场景 | 临时文件目录 | 说明 |
|---------|-------------|------|
| 从 ubox 目录运行 | `H:\work\ubox\.upa\_temp\{case_id}\` | 项目级临时目录，与 reports 同级 |
| 从 skill 目录运行 | `skill\_root/cache/_temp/{case_id}/` | skill 级临时目录，与 cache 同级 |
| 通用场景 | `./.upa\_temp/{case_id}/` | 当前工作目录下的临时文件夹 |

**目录结构示例**：
```
.upa_temp/
  bf909275-f505-11f0-9e9c-708bcdbcb7b1/
    overview.json
    hotspots_self_time.json
    hotspots_total_time.json
    hotspots_valid_frame_avg.json
    hotspots_high_cost_frames.json
    jank_ai_ready.json
    memory.json
    merged_hotspots.json
```

### 临时文件命名规则

**格式**: `{data_type}_{dimension?}.json`

**命名规范**：
- **概况数据**: `overview.json`
- **热点函数（单维度）**: `hotspots_{dimension}.json`（dimension: self_time/total_time/valid_frame_avg/high_cost_frames）
- **热点函数（合并后）**: `merged_hotspots.json`
- **卡顿分析**: `jank_{mode}.json`（mode: raw/ai_ready）
- **内存分析**: `memory.json`
- **报告对比**: `comparison_{case_a}_vs_{case_b}.json`

### 临时文件清理策略

**自动清理**：
- ⚠️ **重要**：临时文件不会自动删除，需要手动清理
- 推荐每次分析完成后，确认报告生成无误后立即删除临时文件
- 定期清理（如每周）整个 `.upa_temp` 目录

**手动清理命令**：
```bash
# Windows (从 ubox 目录)
rmdir /s /q .upa_temp

# Linux/Mac
rm -rf .upa_temp

# 清理特定案例的临时文件
rmdir /s /q .upa_temp\bf909275-f505-11f0-9e9c-708bcdbcb7b1
```

**清理时机建议**：
1. ✅ 报告生成完成并确认无误后
2. ✅ 每次开始新的分析任务前
3. ✅ 定期维护（每周/每月）
4. ❌ 不要在分析过程中清理（可能导致数据丢失）

### 临时文件与缓存的区别

| 特性 | 临时文件 | 缓存 (cache) |
|------|---------|-------------|
| 用途 | 存放 CLI 输出的原始 JSON 数据 | 存放 API 响应数据 |
| 存放位置 | `.upa_temp/{case_id}/` | `cache/{case_id}/` |
| 生命周期 | 手动管理，需要主动清理 | TTL 管理（默认 24 小时） |
| 文件内容 | 原始 JSON 输出 | 结构化的 API 数据 |
| 管理方式 | 完全手动 | CacheManager 自动管理 |

---

## 报告文件管理

### 报告文件命名规则

**完整格式**: `{report_type}_{case_id}_{timestamp}.{ext}`

**命名规范**：

| 报告类型 | report_type 值 | 示例文件名 |
|---------|----------------|-----------|
| 完整性能分析 | `complete` | `complete_bf909275-f505-11f0-9e9c-708bcdbcb7b1_20260127_143052.md` |
| 性能概况 | `overview` | `overview_bf909275-f505-11f0-9e9c-708bcdbcb7b1_20260127_143055.md` |
| 热点函数分析 | `hotspot` | `hotspot_bf909275-f505-11f0-9e9c-708bcdbcb7b1_20260127_143058.md` |
| 卡顿分析 | `jank` | `jank_bf909275-f505-11f0-9e9c-708bcdbcb7b1_20260127_143102.md` |
| 内存分析 | `memory` | `memory_bf909275-f505-11f0-9e9c-708bcdbcb7b1_20260127_143105.md` |
| 报告对比 | `comparison` | `comparison_case_a_vs_case_b_20260127_143110.md` |

**时间戳格式**: `YYYYMMDD_HHMMSS`（精确到秒）

### 报告存放位置

**标准目录结构**：

```
unity-profiler-analyzer/
  reports/                          # 所有报告的根目录
    bf909275-f505-11f0-9e9c-708bcdbcb7b1/    # 按案例 ID 分组
      complete_bf909275_20260127_143052.md   # 完整分析报告
      hotspot_bf909275_20260127_143058.md    # 热点函数报告
      overview_bf909275_20260127_143055.md   # 概况报告
      jank_bf909275_20260127_143102.md       # 卡顿分析报告
      memory_bf909275_20260127_143105.md     # 内存分析报告
    c5e8a321-xxxx-xxxx-xxxx-xxxxxxxxxxxx/    # 另一个案例
      complete_c5e8a321_20260127_150030.md
      hotspot_c5e8a321_20260127_150032.md
  _temp/                            # 临时文件目录（可选与 .upa_temp 二选一）
    bf909275-f505-11f0-9e9c-708bcdbcb7b1/
      overview.json
      merged_hotspots.json
```

**推荐存放策略**：

| 场景 | 报告目录 | 配置方式 |
|------|---------|---------|
| 从 ubox 目录运行 | `H:\work\ubox\reports\{case_id}\` | 使用项目级 reports 目录 |
| 从 skill 目录运行 | `skill_root/reports/{case_id}/` | 使用 skill 级 reports 目录 |
| 自定义位置 | 用户指定目录 | 通过参数或配置文件指定 |

### 报告文件管理策略

**版本管理**：
- 每次生成报告时使用新的时间戳，保留历史版本
- 文件名中的时间戳确保版本唯一性
- 便于追踪同一案例在不同时间点的分析结果

**归档策略**：
- **短期**（近 7 天）：保留所有报告
- **中期**（近 30 天）：保留每天的最佳版本（如每天保留一份）
- **长期**（超过 30 天）：仅保留重要案例的最终版本

**清理建议**：
```bash
# 清理 30 天前的旧报告（Windows PowerShell）
Get-ChildItem -Path reports\ -Recurse -Filter "*.md" |
  Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-30) } |
  Remove-Item -Force

# 清理 30 天前的旧报告（Linux/Mac）
find reports/ -name "*.md" -mtime +30 -delete
```

**报告组织最佳实践**：

1. ✅ **按案例分组**：每个 case_id 一个子目录
2. ✅ **包含时间戳**：文件名精确到秒，避免冲突
3. ✅ **类型前缀**：文件名开头标识报告类型，便于识别
4. ✅ **保留完整 case_id**：便于追溯和关联原始数据
5. ✅ **定期归档**：将旧报告移至 archive 目录
6. ❌ **不要使用中文文件名**：避免跨平台兼容性问题
7. ❌ **不要在同一目录存放过多文件**：影响性能和可读性

**目录结构进阶版**（大型项目推荐）：

```
reports/
  {project_id}/                    # 按 APPKEY 分组（可选）
    bf909275-f505-11f0-9e9c-708bcdbcb7b1/
      2026-01/                     # 按年月归档
        complete_bf909275_20260127_143052.md
        hotspot_bf909275_20260127_143058.md
      2026-02/
        complete_bf909275_20260215_103020.md
  archive/                         # 归档目录
    2025/                          # 按年份归档
      12/
        bf909275/
          complete_bf909275_20251230_183045.md
```

---

## AI Agent 报告生成工作流

### 完整工作流

```
1. 确定分析类型和案例信息
   ↓
2. 创建临时文件目录
   .upa_temp/{case_id}/
   ↓
3. 调用 CLI 工具获取数据（保存到临时目录）
   python scripts/cli.py analyze {case_id} --overview -o .upa_temp/{case_id}/overview.json
   python scripts/cli.py analyze {case_id} --hotspots --sort-by self_time -o .upa_temp/{case_id}/hotspots_self_time.json
   ... (获取所有需要的数据)
   ↓
4. 合并和处理数据（可选）
   读取多个 JSON 文件 → 合并为一个中间文件
   ↓
5. 使用 AI 提示词生成报告
   读取临时文件 + AI 提示词 → 生成 Markdown 内容
   ↓
6. 保存最终报告
   创建 reports/{case_id}/ 目录
   保存为 {report_type}_{case_id}_{timestamp}.md
   ↓
7. 清理临时文件（可选但推荐）
   删除 .upa_temp/{case_id}/ 目录
   ↓
8. 通知用户
   返回报告文件路径和关键发现摘要
```

### 示例代码（AI Agent 伪代码）

```python
import json
from datetime import datetime
from pathlib import Path

# 1. 设置路径
case_id = "bf909275-f505-11f0-9e9c-708bcdbcb7b1"
temp_dir = Path(".upa_temp") / case_id
report_dir = Path("reports") / case_id
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

# 2. 创建目录
temp_dir.mkdir(parents=True, exist_ok=True)
report_dir.mkdir(parents=True, exist_ok=True)

# 3. 获取数据（保存到临时目录）
# ... CLI 命令调用 ...

# 4. 读取临时文件
with open(temp_dir / "overview.json", "r", encoding="utf-8") as f:
    overview_data = json.load(f)

# 5. 生成报告内容
report_content = generate_report(overview_data)

# 6. 保存最终报告
report_path = report_dir / f"complete_{case_id}_{timestamp}.md"
with open(report_path, "w", encoding="utf-8") as f:
    f.write(report_content)

# 7. 清理临时文件（可选）
# import shutil
# shutil.rmtree(temp_dir)

# 8. 通知用户
print(f"✅ 报告已保存到: {report_path}")
```

---

## 各分析类型的文件使用清单

### 完整性能分析

**临时文件**：
- `overview.json`
- `hotspots_self_time.json`
- `hotspots_total_time.json`
- `hotspots_valid_frame_avg.json`
- `hotspots_high_cost_frames.json`
- `jank_ai_ready.json`
- `memory.json`
- `merged_hotspots.json`（合并后）

**最终报告**：
- `complete_{case_id}_{timestamp}.md`

### 性能概况分析

**临时文件**：
- `overview.json`

**最终报告**：
- `overview_{case_id}_{timestamp}.md`

### 热点函数分析

**临时文件**：
- `hotspots_self_time.json`
- `hotspots_total_time.json`
- `hotspots_valid_frame_avg.json`
- `hotspots_high_cost_frames.json`
- `merged_hotspots.json`（合并后）

**最终报告**：
- `hotspot_{case_id}_{timestamp}.md`

### 卡顿分析

**临时文件**：
- `jank_ai_ready.json`

**最终报告**：
- `jank_{case_id}_{timestamp}.md`

### 内存分析

**临时文件**：
- `memory.json`

**最终报告**：
- `memory_{case_id}_{timestamp}.md`

### 报告对比分析

**临时文件**：
- `comparison_{case_a}_vs_{case_b}.json`

**最终报告**：
- `comparison_{case_a}_vs_{case_b}_{timestamp}.md`

---

## 常见问题

### Q: 为什么临时文件不会自动清理？

A: 临时文件主要用于调试和验证，保留临时文件可以帮助排查问题。AI Agent 应该在确认报告生成无误后主动清理。

### Q: 报告文件是否必须包含时间戳？

A: 是的。时间戳确保版本唯一性，便于追踪同一案例在不同时间点的分析结果。

### Q: 是否可以在同一目录存放多个案例的报告？

A: 不推荐。应该按 case_id 创建子目录，每个案例一个目录，便于管理和查找。

### Q: 临时文件和缓存有什么区别？

A: 临时文件是 CLI 输出的原始 JSON 数据，完全手动管理；缓存是 API 响应数据，由 CacheManager 自动管理，有 TTL 机制。

---

## 变更历史

| 版本 | 日期 | 变更内容 |
|------|------|----------|
| v1.0 | 2026-01-27 | 初始版本：从 SKILL.md 拆分文件管理章节，创建独立文档 |
