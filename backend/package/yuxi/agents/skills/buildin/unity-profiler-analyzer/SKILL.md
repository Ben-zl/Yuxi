---
name: unity-profiler-analyzer
description: "Unity 游戏性能分析工具。使用 unity_profiler_analyzer 命令行工具获取数据，进行性能问题分析，使用 AI 生成 Markdown 格式分析报告。支持性能概况、热点函数分析、帧率卡顿分析、内存分析、报告对比等功能。"
---

# Unity Profiler 性能分析器

## 概述

使用 `unity_profiler_analyzer` 命令行工具获取 Unity Profiler 性能数据，实现智能化的 Unity 游戏性能问题分析工具。通过 AI Agent 调用命令行工具获取数据，使用 AI 提示词生成专业的 Markdown 格式分析报告。

> **注意**：由于包结构限制，请使用 `python scripts/cli.py` 直接调用，而不是 `python -m unity_profiler_analyzer`。
>
> **便捷调用**：如果已将 skill 目录添加到 PATH，可以直接使用 `upa.bat`（Windows）或 `./upa.sh`（Linux/Mac）快捷调用。

> **⚠️ 重要要求**：AI Agent 在执行任何分析之前，**必须先查阅对应分析类型的 md 文档**！
>
> 不同分析类型有不同的：
> - 数据获取方式（CLI 参数、API 调用）
> - 分析维度和指标
> - AI 提示词模板
> - 报告结构要求
>
> 详见下方的"分析工作流"和"分析类型详解"部分。

**核心特性：**
- **多维度分析**：性能概况、热点函数、帧率卡顿、内存分析、报告对比
- **智能排序**：支持按自身耗时、总耗时、有效帧平均耗时、高耗时帧数等维度排序
- **模块分组**：按 Rendering、Script、Physics、Animation、UI 模块分组分析热点函数
- **Unity Entry 过滤**：默认过滤 Unity 引擎包装函数，直接定位游戏代码性能问题
- **AI 驱动报告**：使用 AI 提示词自动生成结构化 Markdown 分析报告
- **URL 解析**：支持从 ubox.testplus.cn URL 自动提取 project_id 和 case_id
- **自动保存**：分析完成后自动保存报告到本地文件系统

> **⚠️ URL 配置**：外部 API `https://ubox.testplus.cn/api` 返回 404 不可用，请直接使用默认内部 API（无需 `--url` 参数）

---

## 使用场景

在以下情况下激活此 skill：

- **完整性能分析**（推荐）: 用户需要一份全面的性能分析报告，包含概况、热点函数、卡顿、内存等所有分析维度
- **性能概况分析**：用户需要分析 Unity 游戏性能数据，获取关键指标的快速概览
- **热点函数分析**：用户需要定位性能瓶颈函数，分析函数耗时趋势
- **帧率卡顿分析**：用户需要分析帧率波动和卡顿问题，定位卡顿帧
- **内存专项分析**：用户需要分析内存使用情况、GC 性能
- **报告对比分析**：用户需要对比两个性能报告，识别性能变化和问题函数

**支持输入方式**：
- **URL 方式**（推荐）：直接提供 ubox.testplus.cn 页面 URL，自动提取 project_id 和 case_id
- **参数方式**：提供 project_id 和 case_id 参数
- **JSON 文件**：提供已导出的 JSON 数据文件

---

## 分析工作流

### ⚠️ 关键要求：必须使用对应类型的 md 文档

**AI Agent 在执行任何分析之前，必须先查阅以下两个文档！**

1. **📖 文件管理规范**：[docs/file-management.md](docs/file-management.md)（所有分析类型通用）
2. **📖 分析类型文档**：对应分析类型的详细文档（见下表）

不同分析类型有各自独立的：
- **数据获取方式**（CLI 参数、API 调用）
- **分析维度和指标**
- **AI 提示词模板**
- **报告结构要求**

但**所有分析类型都必须遵守统一的文件管理规范**！

### 工作流概览

```
用户请求分析
    ↓
解析输入（URL 或参数）
    ├─ URL 方式：从 ubox.testplus.cn URL 提取 project_id 和 case_id
    ├─ 参数方式：直接使用提供的 project_id 和 case_id
    └─ JSON 文件：读取已导出的数据
    ↓
确定分析类型（概况/热点/卡顿/内存/对比/完整）
    ↓
📖 【关键步骤 1】查阅文件管理规范
    └─ docs/file-management.md（所有分析类型必须查阅）
    ↓
📖 【关键步骤 2】查阅对应模块的 md 文档
    ├─ 完整分析 → docs/complete-analysis.md
    ├─ 概况分析 → docs/overview.md
    ├─ 热点函数 → docs/hotspot-analysis.md
    ├─ 卡顿分析 → docs/jank-analysis.md
    ├─ 内存分析 → docs/memory-analysis.md
    └─ 报告对比 → docs/comparison-analysis.md
    ↓
根据文档指引：
    ├─ 遵守文件管理规范（临时文件、报告命名、存放位置）
    ├─ 使用正确的 CLI 命令获取数据
    ├─ 使用对应的 AI 提示词模板
    └─ 按照要求的报告结构生成内容
    ↓
创建临时文件目录
    └─ .upa_temp/{case_id}/
    ↓
调用 unity_profiler_analyzer 命令行工具获取数据（保存到临时目录）
    ↓
读取 JSON 数据（从临时文件）
    ↓
使用对应的 AI 提示词模板
    ↓
生成 Markdown 格式分析报告
    ↓
创建报告目录并保存报告
    ├─ 目录：reports/{case_id}/
    ├─ 文件名：{report_type}_{case_id}_{timestamp}.md
    └─ 遵守文件管理规范
    ↓
清理临时文件（可选但推荐）
    ↓
返回报告文件路径给用户
```

### 命令行工具基础

`unity_profiler_analyzer` 是核心数据获取工具，支持以下主要功能：

```bash
# 基础调用格式（从 skill 目录）
python scripts/cli.py <command> [options]

```

**常用参数**：
- `--overview`: 性能概况分析
- `--hotspots`: 热点函数分析
- `--jank`: 卡顿帧分析（Jank Frame Analysis）
- `--top N`: 返回 Top N 函数/帧（默认 20）
- `--sort-by`: 排序维度（self_time/total_time/valid_frame_avg/high_cost_frames）
- `--include-unity-entry`: 包含 Unity 入口函数（默认过滤）
- `--with-screenshots`: 包含截图 URL（仅用于卡顿分析）
- `-o, --output`: 输出到文件
- `--project`: 项目 ID/APPKEY
- `--url`: API 基础 URL
- `--no-cache`: 禁用缓存
- `--verbose, -v`: 详细输出

---

## 获取 project_id 和 case_id

性能分析需要两个关键参数：**project_id**（项目 ID）和 **case_id**（测试案例 ID）。

### 方式 1：从 URL 自动提取（推荐）

如果你有 ubox.testplus.cn 的报告链接，可以直接从 URL 中提取参数。

**URL 格式**：
```
https://ubox.testplus.cn/project/*/appKey/{project_id}/detail/{caseid}/summaryHome
```

**示例**：
```
输入 URL:
https://ubox.testplus.cn/project/starsandisland/appKey/pcavpt6w/detail/bf909275-f505-11f0-9e9c-708bcdbcb7b1/summaryHome

自动提取:
  project_id = pcavpt6w
  case_id = bf909275-f505-11f0-9e9c-708bcdbcb7b1
```

> **注意**：支持 HTTP/HTTPS、带 query 参数（如 `?tab=overview`）

---

### 方式 2：使用 list-reports 搜索报告

如果没有 URL，可以使用 `list-reports` 命令搜索报告并获取 case_id。

#### 基础用法

```bash
# 按时间范围获取最新报告
python scripts/cli.py list-reports --project pcavpt6w \
  --start-time "2026-02-01 00:00:00" \
  --end-time "2026-02-10 23:59:59"
```

#### 高级过滤

| 场景 | 命令示例 |
|------|----------|
| 按案例名搜索 | `--case-name "TDR"` 或 `--case-name "登录"` |
| 按设备名搜索 | `--device-name "i7-8700k"` 或 `--device-name "iPhone"` |
| 组合搜索 | `--case-name "TDR" --device-name "i7-8700k"` |
| 正则表达式 | `--case-name ".*TDR.*Rain.*" --regex` |
| 分页查看 | `--page 2 --page-size 20` |
| 按时间排序 | `--order-by createTime --order-type desc` |
| 输出 JSON | `--format json -o reports.json` |

#### 匹配方式

1. **智能关键词匹配**（默认，推荐）：
   - 自动中英文关键词映射（如"雨天"→"Rain"，"载具"→"Vehicle"）
   - 所有关键词必须同时存在于案例名中
   - 示例：`--case-name "载具跑图（TDR）（雨天）"` → 匹配 "Vehicle Run (TDR) - Rain"

2. **正则表达式匹配**（需 `--regex` 参数）：
   - 使用 Python 正则表达式
   - 支持复杂模式匹配

#### 完整工作流示例

```bash
# 步骤 1：搜索报告
python scripts/cli.py list-reports --project pcavpt6w \
  --start-time "2026-02-01 00:00:00" \
  --case-name "TDR"

# 输出示例：
# [*] Fetched 146 reports from API
# [*] Filtered to 134 matching reports
# 1    bf909275-f505-11f0-9e9c-708bcdbcb7b1    载具跑图(TDR)    iPhone 14    2026-02-09
# 2    fb7c6670-05b5-11f1-a946-e0d55ea9a1cf    载具跑图(TDR)    i7-8700k     2026-02-09

# 步骤 2：复制 Case ID 进行分析
python scripts/cli.py analyze bf909275-f505-11f0-9e9c-708bcdbcb7b1 \
  --overview --project pcavpt6w
```

#### 注意事项

- ⚠️ **必须使用时间范围过滤**：避免获取过多数据
- 🔄 **过滤在本地进行**：API 先返回全部报告，再在本地过滤
- 📌 **默认使用模糊匹配**：更简单，适用于大多数场景

---

### 方式 3：直接提供参数

如果已知 project_id 和 case_id，直接使用：

```bash
python scripts/cli.py analyze <case_id> --project <project_id> [options]
```

---

## 选择分析类型

⚠️ **重要：确定分析类型后，AI Agent 必须立即查阅对应的详细文档！**

根据用户请求，选择相应的分析模块并**立即阅读对应的 md 文档**：

| 用户请求示例 | 分析类型 | 📖 必须查阅的文档 |
|-------------|---------|------------------|
| URL 方式：<br>- "分析这个链接的性能" + URL<br>- "生成这个报告的完整分析" + URL | 完整性能分析 | [docs/complete-analysis.md](docs/complete-analysis.md) ⭐ |
| 参数方式：<br>- "生成完整分析报告" + project_id + case_id<br>- "全面性能分析" + project_id + case_id | | |
| URL/参数方式：<br>- "分析性能概况" + URL/参数<br>- "生成性能概览报告" | 性能概况分析 | [docs/overview.md](docs/overview.md) |
| URL/参数方式：<br>- "分析热点函数" + URL/参数<br>- "找出性能瓶颈函数" | 热点函数分析 | [docs/hotspot-analysis.md](docs/hotspot-analysis.md) |
| URL/参数方式：<br>- "分析卡顿帧" + URL/参数<br>- "找出帧率波动问题" | 帧率卡顿分析 | [docs/jank-analysis.md](docs/jank-analysis.md) |
| URL/参数方式：<br>- "分析内存使用" + URL/参数<br>- "检查内存问题" | 内存专项分析 | [docs/memory-analysis.md](docs/memory-analysis.md) |
| 参数方式：<br>- "对比两个性能报告" + case_id_a + case_id_b | 报告对比分析 | [docs/comparison-analysis.md](docs/comparison-analysis.md) |

**查阅文档后，你需要：**
1. ✅ 了解该分析类型的具体数据获取方式（CLI 参数、API 调用）
2. ✅ 获取该分析类型的 AI 提示词模板（在文档末尾）
3. ✅ 按照文档要求的报告结构生成内容
4. ✅ 使用文档中指定的数据字段和指标

---

## 文件管理规范

> **⚠️ 强制要求**：所有分析类型的文件管理**必须严格遵守** [docs/file-management.md](docs/file-management.md) 规范！

### 📖 必须查阅

AI Agent 在执行任何分析之前，**必须先查阅** [文件管理规范文档](docs/file-management.md)！

该规范包含：
- 📁 **临时文件管理**：临时文件类型、存放规则、命名规范、清理策略
- 📄 **报告文件管理**：报告命名规则、存放位置、版本管理、归档策略
- 🔄 **完整工作流**：从数据获取到报告保存的 8 步完整流程
- 📋 **各分析类型清单**：每种分析类型需要的临时文件和最终报告清单

### 核心规则速查

| 项目 | 规则 |
|------|------|
| **临时文件目录** | `.upa_temp/{case_id}/` |
| **报告目录** | `reports/{case_id}/` |
| **临时文件命名** | `{data_type}_{dimension?}.json` |
| **报告文件命名** | `{report_type}_{case_id}_{timestamp}.md` |
| **时间戳格式** | `YYYYMMDD_HHMMSS` |

> **💡 提示**：您可以使用 **配置文件**自定义这些目录的位置，详见下方"自定义目录配置"章节。

### 为什么重要？

- ✅ **统一规范**：确保所有分析类型的文件管理一致
- ✅ **版本追踪**：时间戳确保报告版本唯一性
- ✅ **便于维护**：按案例分组，便于管理和查找
- ✅ **避免混乱**：明确的临时文件和报告分离

### ⚙️ 自定义目录配置

**支持配置文件**：通过 `.upa-config.json` 自定义临时文件、报告和缓存目录。

#### 快速开始

1. **复制配置文件示例**：
   ```bash
   cp .upa-config.json.example .upa-config.json
   ```

2. **编辑配置文件**：
   ```json
   {
     "directories": {
       "temp": "D:/upa_temp",      // 自定义临时文件目录
       "reports": "D:/upa_reports", // 自定义报告目录
       "cache": "./cache"           // 缓存目录
     }
   }
   ```

3. **运行分析**：CLI 工具会自动读取配置并使用自定义目录

#### 配置文件搜索顺序

系统会按以下顺序查找配置文件（找到第一个即使用）：
1. 当前工作目录：`.upa-config.json`
2. 用户主目录：`~/.upa/config.json`
3. Skill 目录：`unity-profiler-analyzer/.upa-config.json`

#### 环境变量方式

也可以使用环境变量设置目录：
- `UPA_TEMP_DIR`：临时文件目录
- `UPA_REPORTS_DIR`：报告目录
- `UBOX_CACHE_DIR`：缓存目录

**优先级**：配置文件 > 环境变量 > 默认值

**📖 详细配置说明**：[docs/file-management.md](docs/file-management.md#⚙️-自定义目录配置)

---

## 分析类型详解

⚠️ **执行任何分析前，AI Agent 必须先查阅对应的详细文档！**

### 0. 完整性能分析 ⭐ 推荐

**📖 必须先查阅**: [docs/complete-analysis.md](docs/complete-analysis.md)

**包含内容**：
- 📊 **性能概况**：FPS、帧时间、内存、DrawCalls、GC 等关键指标评估
- 🔥 **热点函数分析**：4 个维度的 Top N 热点函数（self_time、total_time、valid_frame_avg、high_cost_frames）
- 📉 **卡顿帧分析**：Top 卡顿帧详情和原因分析（如有卡顿）
- 💾 **内存分析**：内存总览、GC 性能分析、内存增长趋势
- 🎯 **模块分组热点**：Rendering、Script、Physics、Animation、UI 模块 Top 10
- 💡 **综合优化建议**：按优先级排序的整体优化方案

**输出**：一份包含所有分析维度的完整 Markdown 报告

---

### 1. 性能概况分析

**📖 必须先查阅**: [docs/overview.md](docs/overview.md)

**输出**：包含 FPS、帧时间、内存、DrawCalls、GC 等关键指标的评估报告

---

### 2. 热点函数分析

**📖 必须先查阅**: [docs/hotspot-analysis.md](docs/hotspot-analysis.md)

**⚠️ 重要要求：必须获取所有维度的数据！**

热点函数分析**不能只获取单一维度**的数据，AI Agent 必须获取**全部 4 个排序维度**的数据：

| 排序维度 | 参数值 | 用途 | 命令示例 |
|---------|--------|------|----------|
| 按自身耗时 | `--sort-by self_time` | 识别函数内部代码性能问题 | `--hotspots --sort-by self_time` |
| 按总耗时 | `--sort-by total_time` | 识别整体耗时最长的调用链路 | `--hotspots --sort-by total_time` |
| 按有效帧平均耗时 | `--sort-by valid_frame_avg` | 找出调用次数多且耗时高的函数 ⭐ | `--hotspots --sort-by valid_frame_avg` |
| 按高耗时帧数 | `--sort-by high_cost_frames` | 定位导致帧率波动的罪魁祸首 | `--hotspots --sort-by high_cost_frames` |

**数据获取流程**：
```bash
# 必须执行 4 次命令，获取所有维度的数据
python scripts/cli.py analyze <case_id> --hotspots --top 20 --sort-by self_time --project <project_id> -o self_time.json
python scripts/cli.py analyze <case_id> --hotspots --top 20 --sort-by total_time --project <project_id> -o total_time.json
python scripts/cli.py analyze <case_id> --hotspots --top 20 --sort-by valid_frame_avg --project <project_id> -o valid_frame_avg.json
python scripts/cli.py analyze <case_id> --hotspots --top 20 --sort-by high_cost_frames --project <project_id> -o high_cost_frames.json
```

**报告生成**：
- 合并所有 4 个维度的数据
- 生成包含所有维度的综合热点函数分析报告
- 每个维度提供不同的性能问题视角

---

### 3. 帧率卡顿分析

**📖 必须先查阅**: [docs/jank-analysis.md](docs/jank-analysis.md)

**输出**：Top N 卡顿帧详情，包含帧号、帧时间、主要耗时函数、卡顿原因分类等

**新增功能**：支持在卡顿帧分析中显示截图图片（使用 `--with-screenshots` 选项）

**分析模式**：
- AI-Ready 模式（默认）：Python 预处理 + 模式识别 + 严重程度分类 + 优化建议
- RAW 模式：原始数据输出，无预处理

**截图功能**（默认启用图片显示）：
- 使用 `--with-screenshots` 选项为每个卡顿帧添加截图图片
- 需要提供 `--project` 参数指定项目 ID
- **报告格式**：每个卡顿帧会显示：
  - 可点击的"查看大图"链接（在新标签页打开原图）
  - 截图图片（使用 Markdown 图片语法直接显示）
  - 详细的函数耗时分析表格
- 截图 URL 格式：`https://minio-cluster.testplus.cn/{project_id}/{case_id}/screenshot/{timestamp}_{frame}.jpg`

---

### 4. 内存专项分析

**📖 必须先查阅**: [docs/memory-analysis.md](docs/memory-analysis.md)

**输出**：内存总览、GC 性能分析、内存增长趋势、优化建议

---

### 5. 报告对比分析

**📖 必须先查阅**: [docs/comparison-analysis.md](docs/comparison-analysis.md)

**输出**：关键指标对比、性能退化 Top 20、性能改进 Top 10、新增/消失热点函数、优化建议

---

## 高级选项

### Unity Entry 函数过滤

默认过滤 Unity 引擎包装函数（如 PlayerLoop、BehaviourUpdate、Profiler.FlushMemoryCounters 等），直接定位游戏代码性能问题。

- **默认行为**：过滤 Unity Entry 函数
- **可选**：包含 Unity Entry（完整分析时）

**适用场景**：
- ✅ 深入分析游戏自定义代码性能
- ✅ 排查具体的性能瓶颈问题
- ❌ 分析整体性能分布（不过滤）
- ❌ 检查 Unity 引擎某子系统的性能（不过滤）

---

## 参考文档

### ⚠️ 使用前必读

**AI Agent 在执行任何分析之前，必须先查阅以下文档！**

#### 📖 必读文档（所有分析类型）

- **[文件管理规范](docs/file-management.md)** ⭐⭐⭐ - **所有分析类型必须遵守！**
  - 临时文件管理规则（类型、存放、命名、清理）
  - 报告文件管理规则（命名、存放、版本、归档）
  - 完整的 8 步报告生成工作流
  - 各分析类型所需文件清单

#### 分析类型文档（根据需要查阅）

每个分析类型文档包含：
- 📋 **分析目标和范围**
- 🔧 **数据获取方式**（CLI 命令、API 调用）
- 📊 **分析维度和指标说明**
- 🤖 **AI 报告生成提示词模板**（在文档末尾）
- 📝 **报告结构要求**

**核心分析文档**：

- **[完整性能分析](docs/complete-analysis.md)** ⭐ - 全面性能分析，包含所有分析维度（推荐）
- **[性能概况分析](docs/overview.md)** - 整体性能概览和关键指标诊断
- **[热点函数分析](docs/hotspot-analysis.md)** - 多维度热点函数排序和分析
- **[帧率卡顿分析](docs/jank-analysis.md)** - 掉帧分析和卡顿定位
- **[内存专项分析](docs/memory-analysis.md)** - 内存使用和 GC 性能分析
- **[报告对比分析](docs/comparison-analysis.md)** - 多报告对比和性能差异识别

#### 模块深入分析

- **[CPU 模块分析](docs/cpu-module.md)** - CPU 相关热点和性能问题
- **[渲染模块分析](docs/rendering-module.md)** - 渲染相关热点和 DrawCall 分析
- **[物理模块分析](docs/physics-module.md)** - 物理相关热点和性能问题
- **[动画模块分析](docs/animation-module.md)** - 动画相关热点和性能问题
- **[UI 模块分析](docs/ui-module.md)** - UI 相关热点和性能问题

#### 支撑系统

- **[架构设计](docs/architecture.md)** - 工具架构和模块设计
- **[数据策略](docs/data-strategy.md)** - 渐进式数据获取和缓存机制

---

## 变更历史

完整的版本变更历史请查阅：[**docs/CHANGELOG.md**](docs/CHANGELOG.md)

**最新版本**：v3.12 (2026-01-28) - 合并 jank_frames 和 big_jank_frames
