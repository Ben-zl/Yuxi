# CPU 模块分析 (CPU Module Analysis)

## 模块定位

深入分析 CPU 性能，识别 CPU 瓶颈。

## 分析内容

### 脚本执行时间分析
- 平均脚本执行耗时
- 脚本耗时占比
- 脚本耗时趋势
- 脚本热点函数识别

### 物理模拟时间分析
- 物理模拟平均耗时
- 物理耗时占比
- 碰撞接触点数
- 活动刚体数量
- 物理热点函数识别

### 动画更新时间分析
- 动画更新平均耗时
- 动画耗时占比
- Animator 组件数量
- 蒙皮计算耗时
- 动画热点函数识别

### GC 性能分析
- 总 GC 次数
- Loading 阶段 GC 次数
- 平均 GC 耗时
- GC 频率（次/分钟）
- GC 占比

### 线程性能分析
- 主线程耗时
- 渲染线程耗时
- 工作线程耗时
- 线程负载均衡分析

## 数据获取方式

### 使用命令行工具

CPU 模块分析可以通过热点函数分析结合模块过滤来实现：

```bash
# 获取 Script 模块的热点函数（CPU 主要关注脚本执行）
python scripts/cli.py analyze <case_id> --hotspots --sort-by self_time

# 保存到文件后，使用 AI Agent 筛选 Script 模块相关函数
python scripts/cli.py analyze <case_id> --hotspots --top 50 -o cpu_data.json

# 完整示例
python scripts/cli.py analyze bf909275-f505-11f0-9e9c-708bcdbcb7b1 \
  --hotspots \
  --top 50 \
  --project pcavpt6w \
  -o cpu_data.json
```

**说明**:
- CPU 模块分析主要关注 Script 模块的性能热点
- 通过 `--hotspots` 获取所有热点函数后，使用 AI Agent 筛选出 Script 相关函数
- 可以结合 `--overview` 获取 CPU 时间分配概览

### 数据获取流程

```
1. python scripts/cli.py analyze <case_id> --overview
   ↓ 获取 CPU 时间分配概览
2. python scripts/cli.py analyze <case_id> --hotspots --top 50
   ↓ 获取热点函数数据
3. AI Agent 筛选 Script 模块相关函数
   ↓ 按函数名模式匹配
4. 生成 CPU 模块分析报告
   ↓ 包含脚本执行、GC 等分析
```

## 性能阈值

### 脚本执行时间 (ms)
- **优秀**: < 3 ms
- **良好**: < 5 ms
- **警告**: ≥ 5 ms
- **严重**: ≥ 10 ms

### 物理模拟时间 (ms)
- **优秀**: < 2 ms
- **良好**: < 3 ms
- **警告**: ≥ 3 ms
- **严重**: ≥ 5 ms

### 动画更新时间 (ms)
- **优秀**: < 1 ms
- **良好**: < 2 ms
- **警告**: ≥ 2 ms
- **严重**: ≥ 3 ms

### GC 性能
- **GC 频率警告**: ≥ 10 次/分钟
- **GC 频率严重**: ≥ 30 次/分钟
- **GC 耗时警告**: ≥ 5 ms
- **GC 耗时严重**: ≥ 10 ms

## 输出报告模板

```markdown
## 🔍 CPU 模块深入分析

### CPU 时间分配概览

| 模块 | 时间 (ms) | 占比 | 状态 | 评估 |
|------|-----------|------|------|------|
| 渲染 | {rendering_time} | {rendering_pct}% | {status} | {evaluation} |
| 脚本 | {script_time} | {script_pct}% | {status} | {evaluation} |
| 物理 | {physics_time} | {physics_pct}% | {status} | {evaluation} |
| 动画 | {animation_time} | {animation_pct}% | {status} | {evaluation} |
| GC | {gc_time} | {gc_pct}% | {status} | {evaluation} |
| 其他 | {other_time} | {other_pct}% | - | - |

### 脚本执行分析

#### 脚本性能指标
- **平均耗时**: {script_avg_time} ms
- **占比**: {script_pct}%
- **状态**: {status} ⚠️/✅

#### 脚本热点函数 Top 10
| 排名 | 函数名 | Self Time (ms) | Total Time (ms) | 调用次数 |
|------|--------|----------------|-----------------|----------|
| 1 | {func} | {st} | {tt} | {calls} |
| ... | ... | ... | ... | ... |

#### 问题描述
{issue_description}

#### 优化建议
{optimization_suggestions}

### 物理模拟分析

#### 物理性能指标
- **平均耗时**: {physics_avg_time} ms
- **占比**: {physics_pct}%
- **碰撞接触点**: {contacts}
- **活动刚体**: {active_rigidbodies}
- **状态**: {status} ⚠️/✅

#### 物理热点函数 Top 10
{physics_hotspots}

#### 问题描述
{issue_description}

#### 优化建议
{optimization_suggestions}

### 动画更新分析

#### 动画性能指标
- **平均耗时**: {animation_avg_time} ms
- **占比**: {animation_pct}%
- **Animator 数量**: {animator_count}
- **蒙皮耗时**: {skinning_time} ms
- **状态**: {status} ⚠️/✅

#### 动画热点函数 Top 10
{animation_hotspots}

### GC 性能分析

#### GC 统计
| 指标 | 数值 | 评估 |
|------|------|------|
| 总 GC 次数 | {total_gc} | {status} |
| Loading GC | {loading_gc} | - |
| 运行时 GC | {runtime_gc} | - |
| 平均 GC 耗时 | {gc_avg_time} ms | {status} |
| GC 频率 | {gc_frequency} 次/分钟 | {status} |
| GC 占比 | {gc_pct}% | - |

#### GC 性能评估
{gc_evaluation}

#### 优化建议
{gc_suggestions}

### 线程性能分析

#### 线程负载
| 线程 | 时间 (ms) | 占比 | 负载均衡 |
|------|-----------|------|----------|
| 主线程 | {main_time} | {main_pct}% | {balance} |
| 渲染线程 | {render_time} | {render_pct}% | {balance} |
| 工作线程 | {worker_time} | {worker_pct}% | {balance} |

#### 负载均衡评估
{balance_evaluation}
```

## 使用示例

### 命令行工具使用

```bash
# 获取 CPU 时间分配概览
python scripts/cli.py analyze <case_id> --overview -o overview.json

# 获取热点函数数据（用于筛选 Script 模块）
python scripts/cli.py analyze <case_id> --hotspots --top 50 -o hotspots.json

# 完整示例：获取 CPU 模块数据并用 AI 生成报告
python scripts/cli.py analyze bf909275-f505-11f0-9e9c-708bcdbcb7b1 \
  --hotspots \
  --top 50 \
  --project pcavpt6w \
  -o cpu_data.json
```

### Python 代码调用（可选）

如果需要在 Python 代码中直接调用：

```python
from scripts.analyzer import ProfilerAnalyzer

analyzer = ProfilerAnalyzer(
    base_url="http://10.11.10.173:8080",
    project_id="your_project_id"
)

# 分析概况（包含 CPU 时间分配）
result_overview = analyzer.analyze_overview(case_id="uuid", use_cache=True)

# 分析热点（用于筛选 Script 模块函数）
result_hotspots = analyzer.analyze_hotspots(
    case_id="uuid",
    top_n=50,
    sort_by="self_time",
    exclude_unity_entry=True,
    use_cache=True
)
```

## 相关模块

- [性能概况分析](overview.md) - CPU 时间分配概览
- [热点函数分析](hotspot-analysis.md) - CPU 热点函数
- [物理模块分析](physics-module.md) - 物理详细分析
- [动画模块分析](animation-module.md) - 动画详细分析
