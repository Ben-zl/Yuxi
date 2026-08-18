# 报告对比分析 (Report Comparison)

## 模块定位

支持多个报告之间的深度对比分析，识别性能变化和具体的问题函数。

**注意**: `daily_report.get_function_trend()` 接口不可用，通过直接对比两份报告的详细数据实现。

---

## ⚠️ 前置要求：文件管理规范

**在开始报告对比分析之前，AI Agent 必须先查阅**：[📖 文件管理规范](file-management.md)

### 为什么重要？

报告对比分析会生成**临时文件**和**最终报告**，必须严格遵守统一的文件管理规范以确保：
- ✅ 临时文件正确存放和命名
- ✅ 最终报告使用规范格式（包含时间戳）
- ✅ 数据来源可追溯
- ✅ 避免文件混乱和覆盖

### 本分析类型的文件清单

**临时文件**（存放于 `.upa_temp/{case_id}/`）：
- `comparison_{case_a}_vs_{case_b}.json` - 报告对比数据

**最终报告**（存放于 `reports/{case_id}/`）：
- `comparison_{case_a}_vs_{case_b}_{timestamp}.md` - 报告对比分析
  - 时间戳格式：`YYYYMMDD_HHMMSS`
  - 示例：`comparison_case_a_vs_case_b_20260127_143110.md`

### 必须遵守的规则

1. **临时文件目录**：`.upa_temp/{case_id}/`（使用任一 case_id 或自定义名称）
2. **报告目录**：`reports/{case_id}/`（使用任一 case_id 或自定义名称）
3. **临时文件命名**：`comparison_{case_a}_vs_{case_b}.json`
4. **报告文件命名**：`comparison_{case_a}_vs_{case_b}_{timestamp}.md`
5. **报告生成完成后**：建议清理临时文件

**📖 详细规范请查阅**：[file-management.md](file-management.md)

---

## 分析内容

### 关键指标对比

对比两份报告的基础性能指标：
- 平均 FPS 变化
- 平均帧时间变化
- 物理时间变化 (physicsTime)
- 渲染时间变化 (renderingTime)
- 脚本时间变化 (scriptTime)
- UI 时间变化 (uiTime)
- 内存使用变化
- DrawCall 变化（平均/最大）
- SetPassCalls 变化（平均）
- Triangles 变化（平均/最大）
- Vertices 变化（平均）
- GC 次数变化
- GC 耗时变化 (total_GC_ms)
- GC 内存变化 (total_GC_kb)

### 模块耗时对比

对比各模块的平均耗时：
- 渲染模块耗时变化
- 脚本模块耗时变化
- 物理模块耗时变化
- 动画模块耗时变化
- GC 耗时变化

### 热点函数对比

**对比维度**:
- Top N 函数列表变化
- 新增热点函数（在报告 B 中新出现）
- 消失热点函数（在报告 A 中存在，报告 B 中消失）
- 函数耗时变化排序

### 具体性能差异问题定位

**性能退化 Top 20**:
- 找出耗时增长最多的 20 个函数
- 分析增长原因：
  - 调用次数增加导致
  - 单次耗时增加导致
  - 新增的性能瓶颈

**性能改进 Top 10**:
- 找出耗时减少最多的 10 个函数
- 评估优化效果

**新增热点函数**:
- 在报告 B 中新出现的高耗时函数
- 分析可能的原因

**消失热点函数**:
- 在报告 A 中的热点，在报告 B 中消失
- 评估优化效果

### 内存对比

- 总内存变化
- Mono 堆内存变化
- GC 堆内存变化
- GC 次数和频率变化
- GC 平均耗时变化

### 性能差异问题汇总

按问题分类汇总：
- 渲染性能差异
- CPU 性能差异
- 内存性能差异
- GC 性能差异

每个问题包含：
- 问题描述
- 影响函数列表
- 变化数据
- 可能原因
- 优化建议

## 数据获取方式

### 使用命令行工具

报告对比分析使用 `compare` 命令一键获取两个报告的对比数据：

```bash
# ========== 使用 compare 命令（推荐）==========

# 基本对比（输出 JSON 数据，包含 AI 预处理）
python scripts/cli.py compare <case_id_a> <case_id_b>

# 指定输出文件
python scripts/cli.py compare <case_id_a> <case_id_b> -o comparison.json

# 完整示例
python scripts/cli.py compare \
  bf909275-f505-11f0-9e9c-708bcdbcb7b1 \
  9198642d-f8cd-11f0-ad85-f8b156e0c103 \
  --project pcavpt6w \
  -o comparison.json

# 更多热点函数（默认 100）
python scripts/cli.py compare <case_id_a> <case_id_b> --hotspot-top 200 -o comparison.json

# 禁用缓存
python scripts/cli.py compare <case_id_a> <case_id_b> --no-cache -o comparison.json

# 详细输出
python scripts/cli.py compare <case_id_a> <case_id_b> -o comparison.json --verbose
```

**注意**:
- `compare` 命令输出 JSON 格式的对比数据
- 数据已经过 Python 预处理（统计分析、增量计算、趋势识别）
- 供 AI Agent 生成 Markdown 报告使用
- 脚本不生成 Markdown 报告，仅提供数据和表格结构

### 数据获取流程

```
# 使用 compare 命令的完整流程

1. python scripts/cli.py compare <case_id_a> <case_id_b> -o comparison.json
   ↓ 调用命令行工具

2. ProfilerAnalyzer.compare_reports(case_id_a, case_id_b)
   ↓ 初始化 ComparisonCollector

3. ComparisonCollector 收集两个报告的数据
   ├─ 使用 OverviewCollector 获取概况数据
   ├─ 使用 HotspotCollector 获取热点数据
   └─ 构建原始对比数据结构

4. ComparisonReporter 进行 Python 预处理
   ├─ ComparisonPreprocessor 统计分析
   │  ├─ 计算指标增量（FPS、内存、DrawCall 等）
   │  ├─ 计算百分比变化
   │  ├─ 识别趋势（上升/下降/稳定）
   │  ├─ 对比函数性能（退化/改进/新增/消失）
   │  ├─ 分类性能变化（按模块）
   │  └─ 生成优化建议
   └─ 构建 AI 就绪的 JSON 结构
      ├─ 元数据（case_id、时间戳）
      ├─ 指标对比数据
      ├─ 函数对比数据
      ├─ 内存对比数据
      ├─ 渲染对比数据
      ├─ 性能分类（按模块）
      ├─ 优化建议（已生成）
      └─ 表格数据（Markdown 就绪）

5. 输出到文件（comparison.json）
   ↓ 供 AI Agent 使用生成对比报告

6. AI Agent 读取 JSON 并生成 Markdown 报告
   └─ 使用本文档末尾的 AI 提示词模板
```

### 输出数据结构

`compare` 命令输出的 JSON 包含以下结构：

```json
{
  "metadata": {
    "case_id_a": "uuid_a",
    "case_id_b": "uuid_b",
    "comparison_timestamp": "2026-02-07T10:00:00Z",
    "analysis_type": "comparison_analysis"
  },
  "report_info": {
    "report_a": { "uuid": "...", "project_id": "...", "app_version": "...", "device_info": "..." },
    "report_b": { "uuid": "...", "project_id": "...", "app_version": "...", "device_info": "..." }
  },
  "metrics_delta": {
    "fps": { "value_a": 58.5, "value_b": 60.2, "delta": 1.7, "delta_pct": 2.9, "trend": "📈 上升", "status": "✅ 稳定", "evaluation": "good" },
    "frame_time": { ... },
    "physics_time": { ... },
    "rendering_time": { ... },
    "script_time": { ... },
    "ui_time": { ... },
    "memory_total": { ... },
    "draw_calls": { ... },
    "max_draw_calls": { ... },
    "setpass_calls": { ... },
    "avg_triangles": { ... },
    "max_triangles": { ... },
    "avg_vertices": { ... },
    "gc_count": { ... },
    "gc_time": { ... },
    "gc_memory_kb": { ... },
    "gc_time_ms": { ... }
  },
  "functions_delta": {
    "degraded_top_20": [
      { "name": "Function.A", "module": "Rendering", "time_a": 5.0, "time_b": 8.0, "delta": 3.0, "delta_pct": 60.0, "calls_a": 100, "calls_b": 150, "calls_delta": 50 }
    ],
    "improved_top_10": [ ... ],
    "new_hotspots": [ ... ],
    "lost_hotspots": [ ... ]
  },
  "memory_delta": {
    "memory_total": { ... },
    "memory_mono": { ... },
    "gc_count": { ... },
    "gc_time": { ... }
  },
  "rendering_delta": {
    "draw_calls": { ... },
    "triangles": { ... },
    "vertices": { ... }
  },
  "performance_categories": {
    "rendering": { "issues": [...], "affected_functions": [...] },
    "script": { ... },
    "physics": { ... },
    "animation": { ... },
    "ui": { ... },
    "other": { ... }
  },
  "recommendations": [
    { "category": "整体性能", "priority": "high", "issue": "...", "suggestion": "...", "affected_functions": [...] }
  ],
  "tables": {
    "metrics_comparison": { "headers": [...], "rows": [...] },
    "rendering_comparison": { "headers": [...], "rows": [...] },
    "memory_comparison": { "headers": [...], "rows": [...] },
    "gc_comparison": { "headers": [...], "rows": [...] },
    "degraded_functions": { "headers": [...], "rows": [...] },
    "improved_functions": { "headers": [...], "rows": [...] },
    "new_hotspots": { "headers": [...], "rows": [...] },
    "lost_hotspots": { "headers": [...], "rows": [...] }
  }
}
```

### 数据对比算法

#### 指标对比
```python
def compare_metrics(metric_a, metric_b):
    """对比指标"""
    delta = metric_b - metric_a
    delta_pct = (delta / metric_a) * 100 if metric_a > 0 else 0

    # 确定趋势
    if delta > 0:
        trend = "📈 上升"
    elif delta < 0:
        trend = "📉 下降"
    else:
        trend = "→ 持平"

    # 确定评估
    if abs(delta_pct) < 5:
        status = "✅ 稳定"
    elif abs(delta_pct) < 15:
        status = "⚠️ 轻微变化"
    else:
        status = "🔴 显著变化"

    return {
        "delta": delta,
        "delta_pct": delta_pct,
        "trend": trend,
        "status": status
    }
```

#### 函数对比
```python
def compare_functions(funcs_a, funcs_b):
    """对比函数列表"""

    # 构建函数映射
    map_a = {f["name"]: f for f in funcs_a}
    map_b = {f["name"]: f for f in funcs_b}

    # 性能退化（耗时增长）
    degraded = []
    for name in map_b:
        if name in map_a:
            delta = map_b[name]["self_time"] - map_a[name]["self_time"]
            if delta > 0:
                degraded.append({
                    "name": name,
                    "time_a": map_a[name]["self_time"],
                    "time_b": map_b[name]["self_time"],
                    "delta": delta,
                    "delta_pct": (delta / map_a[name]["self_time"]) * 100
                })

    # 按变化量排序
    degraded.sort(key=lambda x: x["delta"], reverse=True)

    # 性能改进（耗时减少）
    improved = []
    for name in map_b:
        if name in map_a:
            delta = map_b[name]["self_time"] - map_a[name]["self_time"]
            if delta < 0:
                improved.append({
                    "name": name,
                    "time_a": map_a[name]["self_time"],
                    "time_b": map_b[name]["self_time"],
                    "delta": delta,
                    "delta_pct": (delta / map_a[name]["self_time"]) * 100
                })

    # 按变化量排序
    improved.sort(key=lambda x: x["delta"])

    # 新增热点
    new_hotspots = [name for name in map_b if name not in map_a and map_b[name]["self_time"] > threshold]

    # 消失热点
    lost_hotspots = [name for name in map_a if name not in map_b and map_a[name]["self_time"] > threshold]

    return {
        "degraded": degraded[:20],  # Top 20
        "improved": improved[:10],  # Top 10
        "new_hotspots": new_hotspots,
        "lost_hotspots": lost_hotspots
    }
```

## 输出报告模板

```markdown
## 📊 报告对比分析

### 对比概览
- **报告 A**: {report_a_info}
- **报告 B**: {report_b_info}
- **对比时间**: {comparison_time}

### 关键指标对比

| 指标 | 报告 A | 报告 B | 变化值 | 变化率 | 趋势 | 评估 |
|------|--------|--------|--------|--------|------|------|
| 平均 FPS | {fps_a} | {fps_b} | {delta} | {delta_pct}% | {trend} 📈/📉 | {status} |
| 内存 (MB) | {mem_a} | {mem_b} | {delta} | {delta_pct}% | {trend} 📈/📉 | {status} |
| DrawCalls | {dc_a} | {dc_b} | {delta} | {delta_pct}% | {trend} 📈/📉 | {status} |
| 平均帧时间 | {ft_a} | {ft_b} | {delta} | {delta_pct}% | {trend} 📈/📉 | {status} |

### 模块耗时对比

| 模块 | 报告 A (ms) | 报告 B (ms) | 变化 | 变化率 | 趋势 | 影响评估 |
|------|-------------|-------------|------|--------|------|----------|
| 渲染 | {render_a} | {render_b} | {delta} | {delta_pct}% | {trend} | {impact} |
| 脚本 | {script_a} | {script_b} | {delta} | {delta_pct}% | {trend} | {impact} |
| 物理 | {physics_a} | {physics_b} | {delta} | {delta_pct}% | {trend} | {impact} |
| 动画 | {anim_a} | {anim_b} | {delta} | {delta_pct}% | {trend} | {impact} |
| GC | {gc_a} | {gc_b} | {delta} | {delta_pct}% | {trend} | {impact} |

### 性能差异函数详细分析

#### 性能退化 Top 20（耗时增长最多的函数）

| 排名 | 函数名 | 模块 | 报告A (ms) | 报告B (ms) | 变化 | 变化率 | 原因分析 |
|------|--------|------|------------|------------|------|--------|----------|
| 1 | {func} | {mod} | {time_a} | {time_b} | {delta} | {pct}% | {reason} |
| 2 | ... | ... | ... | ... | ... | ... | ... |

**原因分类**:
- 调用次数增加导致的函数：{list}
- 单次耗时增加导致的函数：{list}
- 新出现的性能瓶颈函数：{list}

#### 性能改进 Top 10（耗时减少最多的函数）

| 排名 | 函数名 | 模块 | 报告A (ms) | 报告B (ms) | 变化 | 变化率 |
|------|--------|------|------------|------------|------|--------|
| 1 | {func} | {mod} | {time_a} | {time_b} | {delta} | {pct}% |
| ... | ... | ... | ... | ... | ... | ... |

#### 新增热点函数（报告 B 中新出现的高耗时函数）

| 函数名 | 模块 | 耗时 | 调用次数 | 可能原因 |
|--------|------|------|----------|----------|
| {func} | {mod} | {time} | {calls} | {reason} |

#### 消失热点函数（报告 A 中的热点，在报告 B 中消失）

| 函数名 | 模块 | 报告A 耗时 | 优化效果 |
|--------|------|------------|----------|
| {func} | {mod} | {time} | {effect} |

### 具体性能差异问题定位

#### 问题 1: {problem_title}
- **问题描述**: {description}
- **影响**: {impact}
- **影响函数**: {affected_functions}
- **变化数据**:
  - 函数 A: {func_a} ({time_a} → {time_b}, {delta_pct}%)
  - 函数 B: {func_b} ({time_a} → {time_b}, {delta_pct}%)
- **可能原因**: {possible_reasons}
- **优化建议**: {optimization_suggestions}

#### 问题 2: {problem_title}
...

### 内存对比分析

| 内存类型 | 报告 A (MB) | 报告 B (MB) | 变化 | 变化率 | 趋势 |
|----------|-------------|-------------|------|--------|------|
| 总内存 | {total_a} | {total_b} | {delta} | {pct}% | {trend} |
| Mono 堆 | {mono_a} | {mono_b} | {delta} | {pct}% | {trend} |
| GC 堆 | {gc_a} | {gc_b} | {delta} | {pct}% | {trend} |

#### GC 性能对比
- GC 次数: {gc_count_a} → {gc_count_b} ({delta} 次, {delta_pct}%)
- GC 平均耗时: {gc_time_a} → {gc_time_b} ({delta} ms, {delta_pct}%)
- GC 频率: {gc_freq_a} → {gc_freq_b} ({delta} 次/分钟)

### 总结与建议

#### 主要变化
{main_changes}

#### 需要关注的性能问题
{attention_points}

#### 优化建议优先级
1. **高优先级**: {high_priority_items}
2. **中优先级**: {medium_priority_items}
3. **低优先级**: {low_priority_items}
```

## 使用示例

### 命令行工具使用

```bash
# ========== 基本使用 ==========

# 基本对比（输出 JSON 数据到标准输出）
python scripts/cli.py compare <case_id_a> <case_id_b>

# 保存到文件（推荐）
python scripts/cli.py compare <case_id_a> <case_id_b> -o comparison.json

# 完整示例
python scripts/cli.py compare \
  bf909275-f505-11f0-9e9c-708bcdbcb7b1 \
  9198642d-f8cd-11f0-ad85-f8b156e0c103 \
  --project pcavpt6w \
  -o comparison.json

# ========== 高级选项 ==========

# 比较更多热点函数（默认 100）
python scripts/cli.py compare <case_id_a> <case_id_b> --hotspot-top 200 -o comparison.json

# 禁用缓存
python scripts/cli.py compare <case_id_a> <case_id_b> --no-cache -o comparison.json

# 详细输出（显示处理过程）
python scripts/cli.py compare <case_id_a> <case_id_b> -o comparison.json --verbose
```

### Python 代码调用（可选）

如果需要在 Python 代码中直接调用：

```python
from scripts.analyzer import ProfilerAnalyzer

analyzer = ProfilerAnalyzer(
    base_url="http://10.11.10.173:8080",
    project_id="your_project_id"
)

# 对比两个报告（返回 AI 就绪的 JSON 数据）
result = analyzer.compare_reports(
    case_id_a="uuid_a",
    case_id_b="uuid_b",
    use_cache=True,
    hotspot_top_n=100
)

# result 包含以下字段：
# - metadata: 元数据（case_id、时间戳）
# - report_info: 报告信息
# - metrics_delta: 指标对比（FPS、内存、DrawCall 等）
# - functions_delta: 函数对比（退化、改进、新增、消失）
# - memory_delta: 内存对比
# - rendering_delta: 渲染对比
# - performance_categories: 性能分类（按模块）
# - recommendations: 优化建议（已生成）
# - tables: Markdown 表格数据（可直接使用）
```

## 相关模块

- [性能概况分析](overview.md) - 基础指标对比
- [热点函数分析](hotspot-analysis.md) - 函数级对比
- [内存专项分析](memory-analysis.md) - 内存对比

---

## AI 报告生成提示词

当用户请求报告对比分析时，使用以下提示词模板生成报告：

```
请基于以下两个 Unity Profiler 报告的对比数据，生成一份专业的 Markdown 格式报告对比分析。

**数据内容**：
{comparison_data_json}

**报告 A**：{report_a_info}
**报告 B**：{report_b_info}

**报告要求**：

1. **报告标题**：# 📊 性能报告对比分析

2. **对比概览章节**（## 📋 对比概览）
   - 报告 A 和 B 的基础信息
   - 对比时间范围

3. **关键指标对比章节**（## 📈 关键指标对比）
   - FPS 变化（值、变化率、趋势）
   - 内存变化（值、变化率、趋势）
   - DrawCalls 变化（值、变化率、趋势）
   - 平均帧时间变化（值、变化率、趋势）
   - 使用表格展示，包含趋势图标（📈上升/📉下降/→持平）

4. **性能差异函数章节**（## 🔍 性能差异分析）
   - 性能退化 Top 20（耗时增长最多的函数）
   - 性能改进 Top 10（耗时减少最多的函数）
   - 新增热点函数
   - 消失热点函数
   - 按原因分类（调用次数增加/单次耗时增加/新增瓶颈）

5. **优化建议章节**（## 💡 优化建议）
   - 针对性能退化函数的优化方案
   - 识别需要优先处理的问题
   - 保持已优化的部分

**输出格式**：纯 Markdown 格式，使用表格展示对比数据，使用图标表示趋势。
```
