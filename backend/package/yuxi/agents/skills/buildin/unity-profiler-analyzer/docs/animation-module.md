# 动画模块分析 (Animation Module Analysis)

## 模块定位

分析动画系统性能，识别动画瓶颈。

## 分析内容

### 动画更新耗时
- 动画更新平均耗时
- 动画耗时占比
- 动画耗时趋势

### 蒙皮计算性能
- 蒙皮计算耗时
- 蒙皮顶点数
- 蒙皮网格数量

### Animator 组件数量
- Animator 组件总数
- 活动 Animator 数量
- Animator Controller 数量

### 动画热点函数识别
- 动画更新相关热点函数
- 蒙皮计算相关热点函数

## 数据获取方式

### 使用命令行工具

动画模块分析可以通过热点函数分析结合模块过滤来实现：

```bash
# 获取热点函数数据（用于筛选 Animation 模块）
python scripts/cli.py analyze <case_id> --hotspots --top 50

# 保存到文件后，使用 AI Agent 筛选 Animation 模块相关函数
python scripts/cli.py analyze <case_id> --hotspots --top 50 -o animation_data.json

# 完整示例
python scripts/cli.py analyze bf909275-f505-11f0-9e9c-708bcdbcb7b1 \
  --hotspots \
  --top 50 \
  --project pcavpt6w \
  -o animation_data.json
```

**说明**: 通过 `--hotspots` 获取所有热点函数后，使用 AI Agent 筛选出 Animation 相关函数（如 Animator、Animation、StateMachine 等）。

## 性能阈值

### 动画更新时间 (ms)
- **优秀**: < 1 ms
- **良好**: < 2 ms
- **警告**: ≥ 2 ms
- **严重**: ≥ 3 ms

## 输出报告模板

```markdown
## 🎭 动画模块深入分析

### 动画性能概览

| 指标 | 数值 | 状态 | 评估 |
|------|------|------|------|
| 动画更新耗时 | {animation_time} ms | {status} | {evaluation} |
| 占比 | {animation_pct}% | - | - |

### 动画更新分析

#### 性能指标
- **平均耗时**: {animation_avg_time} ms
- **占比**: {animation_pct}%
- **状态**: {status} ⚠️/✅

#### 动画热点函数 Top 10
{animation_hotspots}

### 蒙皮计算分析

#### 蒙皮统计
- **蒙皮耗时**: {skinning_time} ms
- **蒙皮顶点数**: {skinning_vertices}
- **蒙皮网格数**: {skinning_meshes}

#### 蒙皮性能评估
{skinning_evaluation}

### Animator 组件统计

#### Animator 统计
- **Animator 总数**: {total_animators}
- **活动 Animator**: {active_animators}
- **Animator Controllers**: {controllers}

#### Animator 评估
{animator_evaluation}

### 优化建议

1. {suggestion_1}
2. {suggestion_2}
```

## 使用示例

### 命令行工具使用

```bash
# 获取热点函数数据（用于筛选 Animation 模块）
python scripts/cli.py analyze <case_id> --hotspots --top 50 -o animation_data.json

# 完整示例
python scripts/cli.py analyze bf909275-f505-11f0-9e9c-708bcdbcb7b1 \
  --hotspots \
  --top 50 \
  --project pcavpt6w \
  -o animation_data.json
```

## 相关模块

- [性能概况分析](overview.md) - 动画时间概览
- [热点函数分析](hotspot-analysis.md) - 动画热点函数
