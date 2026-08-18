# 渲染模块分析 (Rendering Module Analysis)

## 模块定位

分析渲染性能，识别渲染瓶颈。

## 分析内容

### DrawCall 分析
- DrawCall 总数
- 动态批次 DrawCall 数
- 静态批次 DrawCall 数
- DrawCall 趋势分析
- DrawCall 波动分析

### SetPass 调用分析
- SetPass 调用总数
- SetPass 切换频率
- Shader 复杂度分析

### 三角形/顶点统计
- 三角形总数
- 顶点总数
- 平均每帧三角形数
- 三角形分布

### 渲染热点函数识别
- 渲染相关热点函数
- DrawCall 相关函数
- Shader 相关函数

### 批次优化效果评估
- 动态批次 DrawCall 数
- 动态批次数
- 节省的批次数
- 批次效率

### 渲染波动分析
- 渲染时间波动
- 波动帧识别
- 波动原因分析

## 数据获取方式

### 使用命令行工具

渲染模块分析可以通过热点函数分析结合模块过滤来实现：

```bash
# 获取热点函数数据（用于筛选 Rendering 模块）
python scripts/cli.py analyze <case_id> --hotspots --top 50

# 保存到文件后，使用 AI Agent 筛选 Rendering 模块相关函数
python scripts/cli.py analyze <case_id> --hotspots --top 50 -o rendering_data.json

# 完整示例
python scripts/cli.py analyze bf909275-f505-11f0-9e9c-708bcdbcb7b1 \
  --hotspots \
  --top 50 \
  --project pcavpt6w \
  -o rendering_data.json
```

**说明**:
- 渲染模块分析主要关注 Rendering 相关的性能热点
- 通过 `--hotspots` 获取所有热点函数后，使用 AI Agent 筛选出 Rendering 相关函数
- 可以结合 `--overview` 获取 DrawCalls、三角形数等渲染概览数据

### 数据获取流程

```
1. python scripts/cli.py analyze <case_id> --overview
   ↓ 获取渲染性能概览（DrawCalls、三角形数等）
2. python scripts/cli.py analyze <case_id> --hotspots --top 50
   ↓ 获取热点函数数据
3. AI Agent 筛选 Rendering 模块相关函数
   ↓ 按函数名模式匹配（Draw、Render、Shader 等）
4. 生成渲染模块分析报告
```

## 性能阈值

### DrawCalls
- **优秀**: ≤ 100
- **良好**: ≤ 300
- **警告**: ≥ 300
- **严重**: ≥ 500

### SetPass Calls
- **优秀**: ≤ 50
- **良好**: ≤ 100
- **警告**: ≥ 100
- **严重**: ≥ 150

### 三角形数
- **优秀**: ≤ 50,000
- **良好**: ≤ 100,000
- **警告**: ≥ 100,000
- **严重**: ≥ 200,000

### 顶点数
- **优秀**: ≤ 100,000
- **良好**: ≤ 200,000
- **警告**: ≥ 200,000
- **严重**: ≥ 400,000

## 输出报告模板

```markdown
## 🎨 渲染模块深入分析

### 渲染性能概览

| 指标 | 数值 | 阈值 | 状态 | 评估 |
|------|------|------|------|------|
| DrawCalls | {draw_calls} | ≤ 100/300/500 | {status} | {evaluation} |
| SetPass Calls | {setpass_calls} | ≤ 50/100/150 | {status} | {evaluation} |
| 三角形数 | {triangles} | ≤ 50k/100k/200k | {status} | {evaluation} |
| 顶点数 | {vertices} | ≤ 100k/200k/400k | {status} | {evaluation} |

### DrawCall 分析

#### DrawCall 统计
- **DrawCall 总数**: {draw_calls}
- **动态批次**: {dynamic_batches} ({dynamic_pct}%)
- **静态批次**: {static_batches} ({static_pct}%)
- **未批次**: {unbatched} ({unbatched_pct}%)
- **状态**: {status} ⚠️/✅

#### DrawCall 趋势
{drawcall_trend}

#### 批次优化效果
| 类型 | 数量 | 节省 | 效率 |
|------|------|------|------|
| 动态批次 | {dynamic_batches} | {dynamic_saved} | {dynamic_efficiency}% |
| 静态批次 | {static_batches} | {static_saved} | {static_efficiency}% |

### SetPass 调用分析

#### SetPass 统计
- **SetPass 总数**: {setpass_calls}
- **每 DrawCall 平均**: {avg_setpass}
- **状态**: {status} ⚠️/✅

#### Shader 切换分析
{shader_analysis}

### 几何数据统计

#### 三角形/顶点
| 指标 | 数值 | 评估 |
|------|------|------|
| 三角形总数 | {triangles} | {status} |
| 平均每帧 | {triangles_per_frame} | - |
| 顶点总数 | {vertices} | {status} |
| 平均每帧 | {vertices_per_frame} | - |

#### 几何复杂度
{geometry_complexity}

### 渲染热点函数 Top 10

| 排名 | 函数名 | Self Time (ms) | Total Time (ms) | 调用次数 |
|------|--------|----------------|-----------------|----------|
| 1 | {func} | {st} | {tt} | {calls} |
| ... | ... | ... | ... | ... |

### 渲染波动分析

#### 波动统计
- **平均渲染时间**: {avg_render_time} ms
- **最大渲染时间**: {max_render_time} ms
- **标准差**: {std_dev} ms
- **波动帧数**: {volatile_frames} ({volatile_pct}%)

#### 波动帧详情
| 帧号 | 渲染时间 (ms) | DrawCalls | 三角形数 | 可能原因 |
|------|---------------|----------|----------|----------|
| {frame} | {time} | {dc} | {tris} | {cause} |

#### 波动原因分析
{volatility_causes}

### 问题描述

#### 主要问题 1: {problem_title}
- **影响**: {impact}
- **相关函数**: {related_functions}
- **详细数据**:
  - DrawCalls: {draw_calls}
  - 三角形: {triangles}
  - 渲染时间: {render_time}
- **可能原因**: {possible_reasons}
- **优化建议**: {optimization_suggestions}

#### 主要问题 2: {problem_title}
...

### 优化建议

#### 高优先级
1. {high_priority_1}
2. {high_priority_2}

#### 中优先级
1. {medium_priority_1}
2. {medium_priority_2}

#### 低优先级
1. {low_priority_1}
```

## 使用示例

### 命令行工具使用

```bash
# 获取渲染性能概览
python scripts/cli.py analyze <case_id> --overview -o overview.json

# 获取热点函数数据（用于筛选 Rendering 模块）
python scripts/cli.py analyze <case_id> --hotspots --top 50 -o rendering_data.json

# 完整示例：获取渲染模块数据并用 AI 生成报告
python scripts/cli.py analyze bf909275-f505-11f0-9e9c-708bcdbcb7b1 \
  --hotspots \
  --top 50 \
  --project pcavpt6w \
  -o rendering_data.json
```

### Python 代码调用（可选）

如果需要在 Python 代码中直接调用：

```python
from scripts.analyzer import ProfilerAnalyzer

analyzer = ProfilerAnalyzer(
    base_url="http://10.11.10.173:8080",
    project_id="your_project_id"
)

# 分析概况（包含渲染性能概览）
result_overview = analyzer.analyze_overview(case_id="uuid", use_cache=True)

# 分析热点（用于筛选 Rendering 模块函数）
result_hotspots = analyzer.analyze_hotspots(
    case_id="uuid",
    top_n=50,
    sort_by="self_time",
    exclude_unity_entry=True,
    use_cache=True
)
```

## 相关模块

- [性能概况分析](overview.md) - 渲染性能概览
- [热点函数分析](hotspot-analysis.md) - 渲染热点函数
- [物理模块分析](physics-module.md) - 物理对渲染的影响
