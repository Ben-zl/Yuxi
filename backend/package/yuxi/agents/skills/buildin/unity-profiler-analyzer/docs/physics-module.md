# 物理模块分析 (Physics Module Analysis)

## 模块定位

分析物理模拟性能，识别物理瓶颈。

## 分析内容

### 物理模拟耗时
- 物理模拟平均耗时
- 物理耗时占比
- 物理耗时趋势

### 碰撞检测性能
- 碰撞接触点数
- 碰撞检测耗时
- 碰撞对数量

### 刚体/关节数量统计
- 活动刚体数量
- 静态刚体数量
- 关节（Joint）数量
- 约束数量

### 物理热点函数识别
- 物理模拟相关热点函数
- 碰撞检测相关函数
- 物理更新相关函数

## 数据获取方式

### 使用命令行工具

物理模块分析可以通过热点函数分析结合模块过滤来实现：

```bash
# 获取热点函数数据（用于筛选 Physics 模块）
python scripts/cli.py analyze <case_id> --hotspots --top 50

# 保存到文件后，使用 AI Agent 筛选 Physics 模块相关函数
python scripts/cli.py analyze <case_id> --hotspots --top 50 -o physics_data.json

# 完整示例
python scripts/cli.py analyze bf909275-f505-11f0-9e9c-708bcdbcb7b1 \
  --hotspots \
  --top 50 \
  --project pcavpt6w \
  -o physics_data.json
```

**说明**: 通过 `--hotspots` 获取所有热点函数后，使用 AI Agent 筛选出 Physics 相关函数（如 Physics、Rigidbody、Collider 等）。

## 性能阈值

### 物理模拟时间 (ms)
- **优秀**: < 2 ms
- **良好**: < 3 ms
- **警告**: ≥ 3 ms
- **严重**: ≥ 5 ms

## 输出报告模板

```markdown
## ⚡ 物理模块深入分析

### 物理性能概览

| 指标 | 数值 | 状态 | 评估 |
|------|------|------|------|
| 物理模拟耗时 | {physics_time} ms | {status} | {evaluation} |
| 占比 | {physics_pct}% | - | - |
| 碰撞接触点 | {contacts} | - | - |
| 活动刚体 | {active_rigidbodies} | - | - |

### 物理模拟分析

#### 性能指标
- **平均耗时**: {physics_avg_time} ms
- **占比**: {physics_pct}%
- **状态**: {status} ⚠️/✅

#### 物理热点函数 Top 10
| 排名 | 函数名 | Self Time (ms) | Total Time (ms) | 调用次数 |
|------|--------|----------------|-----------------|----------|
| 1 | {func} | {st} | {tt} | {calls} |
| ... | ... | ... | ... | ... |

### 碰撞检测分析

#### 碰撞统计
- **碰撞接触点**: {contacts}
- **碰撞对**: {collision_pairs}
- **碰撞耗时**: {collision_time} ms

#### 碰撞性能评估
{collision_evaluation}

### 刚体/关节统计

#### 刚体统计
- **活动刚体**: {active_rigidbodies}
- **静态刚体**: {static_rigidbodies}
- **总刚体数**: {total_rigidbodies}

#### 关节统计
- **关节数量**: {joints}
- **约束数量**: {constraints}

#### 物理对象评估
{physics_objects_evaluation}

### 问题描述

#### 主要问题: {problem_title}
- **影响**: {impact}
- **相关函数**: {related_functions}
- **可能原因**: {possible_reasons}
- **优化建议**: {optimization_suggestions}

### 优化建议

1. {suggestion_1}
2. {suggestion_2}
```

## 使用示例

### 命令行工具使用

```bash
# 获取热点函数数据（用于筛选 Physics 模块）
python scripts/cli.py analyze <case_id> --hotspots --top 50 -o physics_data.json

# 完整示例
python scripts/cli.py analyze bf909275-f505-11f0-9e9c-708bcdbcb7b1 \
  --hotspots \
  --top 50 \
  --project pcavpt6w \
  -o physics_data.json
```

## 相关模块

- [性能概况分析](overview.md) - 物理时间概览
- [热点函数分析](hotspot-analysis.md) - 物理热点函数
- [CPU 模块分析](cpu-module.md) - CPU 中的物理部分
