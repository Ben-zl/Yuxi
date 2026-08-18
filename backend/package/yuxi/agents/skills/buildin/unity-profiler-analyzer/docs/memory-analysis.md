# 内存专项分析 (Memory Analysis)

## 模块定位

专门针对内存使用进行深入分析，识别内存瓶颈和内存泄漏。

---

## ⚠️ 前置要求：文件管理规范

**在开始内存分析之前，AI Agent 必须先查阅**：[📖 文件管理规范](file-management.md)

### 为什么重要？

内存分析会生成**临时文件**和**最终报告**，必须严格遵守统一的文件管理规范以确保：
- ✅ 临时文件正确存放和命名
- ✅ 最终报告使用规范格式（包含时间戳）
- ✅ 数据来源可追溯
- ✅ 避免文件混乱和覆盖

### 本分析类型的文件清单

**临时文件**（存放于 `.upa_temp/{case_id}/`）：
- `memory.json` - 内存分析数据

**最终报告**（存放于 `reports/{case_id}/`）：
- `memory_{case_id}_{timestamp}.md` - 内存分析报告
  - 时间戳格式：`YYYYMMDD_HHMMSS`
  - 示例：`memory_bf909275-f505-11f0-9e9c-708bcdbcb7b1_20260127_143105.md`

### 必须遵守的规则

1. **临时文件目录**：`.upa_temp/{case_id}/`
2. **报告目录**：`reports/{case_id}/`
3. **临时文件命名**：`memory.json`
4. **报告文件命名**：`memory_{case_id}_{timestamp}.md`
5. **报告生成完成后**：建议清理临时文件

**📖 详细规范请查阅**：[file-management.md](file-management.md)

---

## 分析内容

### 内存总览
- 总内存：预留和已用
- Mono 堆：预留和已用
- GC 堆内存
- 其他内存类型

### Mono 堆分析
- 堆大小
- GC 堆内存使用
- 堆碎片化程度

### GC 详细分析
- GC 总次数
- Loading GC 次数
- GC 平均耗时
- GC 频率（次/分钟）
- GC 趋势

### 内存增长趋势
- 内存随时间变化
- 内存增长速率
- 内存泄漏风险评估

### 帧级内存变化
- 帧之间内存变化
- 内存波动分析

## 数据获取方式

### 使用命令行工具

内存分析数据可以通过 `--overview` 命令获取，其中包含内存和 GC 相关信息：

```bash
# 获取性能概况数据（包含内存和 GC 信息）
python scripts/cli.py analyze <case_id> --overview

# 保存到文件
python scripts/cli.py analyze <case_id> --overview -o overview.json

# 完整示例
python scripts/cli.py analyze bf909275-f505-11f0-9e9c-708bcdbcb7b1 \
  --overview \
  --project pcavpt6w \
  -o memory_data.json
```

### 数据获取流程

```
1. python scripts/cli.py analyze <case_id> --overview
   ↓ 调用命令行工具
2. ProfilerAnalyzer.analyze_overview(case_id)
   ↓ 内部调用分析器
3. OverviewCollector.collect(case_id)
   ↓ 收集器自动调用多个 API
   ├─ memory.get_memory_info()   # 内存信息（总内存、Mono 堆等）
   └─ cpu.get_cpu_performance()   # CPU 性能（包含 GC 数据）
4. 数据汇总和计算
   ↓ 生成 JSON 格式数据
5. 输出到文件或标准输出
   ↓ 供 AI Agent 使用生成内存分析报告
```

### 返回的内存相关数据

`--overview` 返回的 JSON 数据包含以下内存相关字段：

```json
{
  "metrics": {
    "memory_total": 512.0,      // 总内存使用（MB）
    "memory_mono": 256.0,       // Mono 堆使用（MB）
    "gc_count": 10,             // GC 总次数
    "gc_time": 5.5              // GC 总耗时（ms）
  },
  "evaluation": {
    "memory": "良好"            // 内存使用评估
  }
}
```

## 性能阈值

### 总内存 (MB)
- **正常**: < 500 MB
- **警告**: ≥ 500 MB
- **严重**: ≥ 1000 MB
- **紧急**: ≥ 1500 MB

### Mono 堆 (MB)
- **正常**: < 200 MB
- **警告**: ≥ 200 MB
- **严重**: ≥ 400 MB

### GC 性能
- **GC 频率警告**: ≥ 10 次/分钟
- **GC 频率严重**: ≥ 30 次/分钟
- **GC 耗时警告**: ≥ 5 ms
- **GC 耗时严重**: ≥ 10 ms

## 输出报告模板

```markdown
## 💾 内存专项分析

### 内存总览

| 类型 | 预留 (MB) | 已用 (MB) | 使用率 | 评估 |
|------|-----------|-----------|--------|------|
| 总内存 | {total_reserved} | {total_used} | {total_usage}% | {status} |
| Mono 堆 | {mono_reserved} | {mono_used} | {mono_usage}% | {status} |
| GfxDriver | {gfx_reserved} | {gfx_used} | {gfx_usage}% | - |
| Audio | {audio_reserved} | {audio_used} | {audio_usage}% | - |
| Video | {video_reserved} | {video_used} | {video_usage}% | - |
| Profiler | {profiler_reserved} | {profiler_used} | {profiler_usage}% | - |

### Mono 堆分析

#### 堆统计
- **堆大小**: {heap_size} MB
- **GC 堆内存**: {gc_heap} MB
- **已用内存**: {used_memory} MB
- **碎片化**: {fragmentation}%

#### 堆性能评估
{heap_evaluation}

### GC 性能分析

#### GC 统计
| 指标 | 数值 | 阈值 | 状态 |
|------|------|------|------|
| 总 GC 次数 | {total_gc} | - | - |
| Loading GC | {loading_gc} | - | - |
| 运行时 GC | {runtime_gc} | - | - |
| 平均 GC 耗时 | {gc_avg_time} ms | < 5/10 | {status} |
| GC 频率 | {gc_frequency} 次/分钟 | < 10/30 | {status} |
| GC 占比 | {gc_pct}% | - | - |

#### GC 性能评估
{gc_evaluation}

#### GC 趋势分析
{gc_trend}

### 内存增长趋势

#### 内存变化
- **起始内存**: {start_memory} MB
- **结束内存**: {end_memory} MB
- **增长量**: {growth} MB
- **增长率**: {growth_rate} MB/分钟

#### 内存泄漏风险评估
{leak_risk}

### 帧级内存变化

#### 内存波动
- **平均波动**: {avg_fluctuation} MB
- **最大波动**: {max_fluctuation} MB
- **波动频率**: {fluctuation_freq}

#### 波动帧分析
{fluctuation_frames}

### 问题描述

#### 主要问题: {problem_title}
- **影响**: {impact}
- **详细数据**: {details}
- **可能原因**: {possible_reasons}
- **优化建议**: {optimization_suggestions}

### 优化建议

#### 内存优化
1. {memory_optimization_1}
2. {memory_optimization_2}

#### GC 优化
1. {gc_optimization_1}
2. {gc_optimization_2}
```

## 使用示例

### 命令行工具使用

```bash
# 获取性能概况数据（包含内存和 GC 信息）
python scripts/cli.py analyze <case_id> --overview

# 保存到文件
python scripts/cli.py analyze <case_id> --overview -o overview.json

# 完整示例：获取内存数据并用 AI 生成报告
python scripts/cli.py analyze bf909275-f505-11f0-9e9c-708bcdbcb7b1 \
  --overview \
  --project pcavpt6w \
  -o memory_data.json
```

### Python 代码调用（可选）

如果需要在 Python 代码中直接调用：

```python
from scripts.analyzer import ProfilerAnalyzer

analyzer = ProfilerAnalyzer(
    base_url="http://10.11.10.173:8080",
    project_id="your_project_id"
)

# 分析概况（包含内存数据）
result = analyzer.analyze_overview(case_id="uuid", use_cache=True)

# result 包含以下内存相关字段：
# - metrics.memory_total: 总内存使用（MB）
# - metrics.memory_mono: Mono 堆使用（MB）
# - metrics.gc_count: GC 总次数
# - metrics.gc_time: GC 总耗时（ms）
# - evaluation.memory: 内存使用评估
```

## 相关模块

- [性能概况分析](overview.md) - 内存概览
- [报告对比分析](comparison-analysis.md) - 内存对比

---

## AI 报告生成提示词

当用户请求内存分析时，使用以下提示词模板生成报告：

```
请基于以下 Unity Profiler 内存数据，生成一份专业的 Markdown 格式内存分析报告。

**数据内容**：
{memory_data_json}

**报告要求**：

1. **报告标题**：# 💾 内存分析报告

2. **内存概览章节**（## 📊 内存概览）
   - 总内存使用量
   - 各类内存占比（堆内存、图形内存、托管内存等）
   - 内存使用趋势

3. **GC 性能章节**（## ♻️ GC 性能分析）
   - GC 总次数
   - GC 平均耗时
   - GC 总耗时占比
   - 单次最大 GC 耗时
   - GC 评估（是否频繁、是否耗时过长）

4. **内存问题分析章节**（## 🔍 内存问题分析）
   - 识别内存泄漏风险
   - 识别高内存消耗对象
   - 分析 GC 频率是否合理

5. **优化建议章节**（## 💡 优化建议）
   - 内存优化具体方案
   - GC 优化建议
   - 对象池使用建议

**输出格式**：纯 Markdown 格式，使用表格展示内存数据，可使用百分比条等可视化元素。
```
