# 使用工作流 (Workflows)

## 概述

本工具提供多种分析工作流，满足不同的性能分析需求。

## 工作流 1: 性能概况分析

### 功能定位

对整个性能报告进行宏观概览，提供关键指标的快速诊断。

### 使用场景

- 快速了解性能概况
- 定位主要性能问题
- 生成简要性能报告

### 执行步骤

```
步骤 1: 获取 case_id
  ↓
步骤 2: 检查缓存
  ├─ 缓存存在且未过期 → 使用缓存
  └─ 缓存不存在 → 从 API 获取数据
  ↓
步骤 3: 渐进式加载数据
  ├─ 第一层: 基础信息 (report.get_report_info)
  ├─ 第二层: 核心指标 (cpu.get_cpu_performance, memory.get_memory_info, graphic.get_graphic_profile)
  └─ 数据汇总和计算
  ↓
步骤 4: 生成概况报告
  ↓
步骤 5: 保存到缓存
  ↓
步骤 6: 输出 Markdown
```

### 命令示例

```bash
# 基本概况分析
python scripts/cli.py analyze <case_id> --overview

# 指定输出文件
python scripts/cli.py analyze <case_id> --overview -o report.md

# 不使用缓存
python scripts/cli.py analyze <case_id> --overview --no-cache

# 详细输出
python scripts/cli.py analyze <case_id> --overview --verbose
```

### 代码示例

```python
from analyzer import ProfilerAnalyzer

analyzer = ProfilerAnalyzer(
    base_url="http://10.11.10.173:8080",
    project_id="your_project_id"
)

# 分析概况
report = analyzer.analyze_overview(
    case_id="uuid",
    use_cache=True
)

# 生成 Markdown
markdown = report.to_markdown()
with open("overview.md", "w") as f:
    f.write(markdown)
```

### 输出示例

```markdown
# Unity Profiler 性能分析报告

## 📊 性能概况

### 基础信息
- **游戏名称**: 游戏A
- **案例名称**: 日常任务测试
- **游戏版本**: 1.0.0
- **测试设备**: iPhone 13
- **测试时间**: 2026-01-22 10:00:00
- **总帧数**: 10000

### 帧率统计
| 指标 | 数值 | 评估 |
|------|------|------|
| 平均 FPS | 58.5 | ⚠️ 良好 |
| 最高 FPS | 60 | - |
| 最低 FPS | 25 | 🔴 较差 |
| FPS 标准差 | 5.2 | ⚠️ 轻微波动 |
...
```

---

## 工作流 2: 热点函数分析

### 功能定位

多维度分析热点函数，识别性能瓶颈。

### 使用场景

- 定位性能瓶颈函数
- 分析函数耗时趋势
- 识别性能退化函数

### 执行步骤

```
步骤 1: 获取 case_id
  ↓
步骤 2: 检查缓存
  ↓
步骤 3: 获取函数列表
  ↓
步骤 4: 判断数据量
  ├─ 小数据 (< 1000) → 直接分析
  │   ├─ 获取函数 Top N
  │   ├─ 查询函数性能详情
  │   ├─ 获取函数趋势数据
  │   └─ 多维度排序和分组
  │
  └─ 大数据 (≥ 1000) → 预处理
      ├─ 启动预处理脚本
      ├─ 计算多维度 Top 100
      ├─ 按模块分组统计
      ├─ 提取趋势摘要
      └─ 选择关键样本
  ↓
步骤 5: 分析和排序
  ↓
步骤 6: 生成热点报告
  ↓
步骤 7: 保存到缓存
  ↓
步骤 8: 输出 Markdown
```

### 命令示例

```bash
# Top 20 按自身耗时
python scripts/cli.py analyze <case_id> --hotspots --top 20 --sort-by self_time

# Top 50 按调用次数
python scripts/cli.py analyze <case_id> --hotspots --top 50 --sort-by calls

# 完整热点分析（所有维度）
python scripts/cli.py analyze <case_id> --hotspots --all-dimensions

# 强制详细模式（禁用预处理）
python scripts/cli.py analyze <case_id> --hotspots --detailed
```

### 代码示例

```python
from analyzer import ProfilerAnalyzer

analyzer = ProfilerAnalyzer(
    base_url="http://10.11.10.173:8080",
    project_id="your_project_id"
)

# 分析热点（按自身耗时）
report = analyzer.analyze_hotspots(
    case_id="uuid",
    top_n=20,
    sort_by="self_time",
    include_trend=True,      # 包含趋势分析
    include_call_chain=True, # 包含调用链
    group_by_module=True     # 按模块分组
)

# 生成 Markdown
markdown = report.to_markdown()
```

---

## 工作流 3: 报告对比分析

### 功能定位

对比两个报告的差异，识别性能变化和问题函数。

### 使用场景

- 对比优化前后的性能
- 识别性能退化
- 验证优化效果

### 执行步骤

```
步骤 1: 获取两个 case_id
  ↓
步骤 2: 并行检查缓存
  ├─ 报告 A 缓存检查
  │   ├─ 存在 → 加载
  │   └─ 不存在 → 获取并缓存
  │
  └─ 报告 B 缓存检查
      ├─ 存在 → 加载
      └─ 不存在 → 获取并缓存
  ↓
步骤 3: 加载两份报告的缓存数据
  ↓
步骤 4: 逐模块对比
  ├─ 关键指标对比
  ├─ 模块耗时对比
  └─ 函数级对比
  ↓
步骤 5: 识别性能差异
  ├─ 性能退化 Top 20
  ├─ 性能改进 Top 10
  ├─ 新增热点
  └─ 消失热点
  ↓
步骤 6: 分析差异原因
  ↓
步骤 7: 生成对比报告
  ↓
步骤 8: 输出 Markdown
```

### 命令示例

```bash
# 基本对比
python scripts/cli.py compare <case_id_a> <case_id_b>

# 指定输出文件
python scripts/cli.py compare <case_id_a> <case_id_b> -o comparison.md

# 设置变化阈值
python scripts/cli.py compare <case_id_a> <case_id_b> --delta-threshold 10

# 强制刷新缓存
python scripts/cli.py compare <case_id_a> <case_id_b> --refresh-cache
```

### 代码示例

```python
from analyzer import ProfilerAnalyzer

analyzer = ProfilerAnalyzer(
    base_url="http://10.11.10.173:8080",
    project_id="your_project_id"
)

# 对比两个报告
report = analyzer.compare_reports(
    case_id_a="uuid_a",
    case_id_b="uuid_b",
    delta_threshold=10,  # 变化阈值 10%
    use_cache=True
)

# 生成 Markdown
markdown = report.to_markdown()
```

---

## 工作流 4: 完整分析

### 功能定位

执行所有分析模块，生成完整的性能分析报告。

### 使用场景

- 全面性能诊断
- 生成详细分析报告
- 性能回归测试

### 执行步骤

```
步骤 1: 获取 case_id
  ↓
步骤 2: 按顺序执行所有分析模块
  ├─ 性能概况分析
  ├─ CPU 模块分析
  ├─ 渲染模块分析
  ├─ 物理模块分析
  ├─ 动画模块分析
  ├─ UI 模块分析
  ├─ 热点函数分析
  ├─ 帧率卡顿分析
  └─ 内存专项分析
  ↓
步骤 3: 汇总所有分析结果
  ↓
步骤 4: 生成完整报告
  ↓
步骤 5: 输出 Markdown
```

### 命令示例

```bash
# 完整分析
python scripts/cli.py analyze <case_id> --all

# 指定输出目录
python scripts/cli.py analyze <case_id> --all --output-dir ./reports
```

---

## 常见使用场景

### 场景 1: 日常性能检查

```bash
# 快速概况
python scripts/cli.py analyze <case_id> --overview

# 如果发现问题，深入分析
python scripts/cli.py analyze <case_id> --hotspots
```

### 场景 2: 性能优化验证

```bash
# 优化前
python scripts/cli.py analyze <case_id_before> --all -o before.md

# 优化后
python scripts/cli.py analyze <case_id_after> --all -o after.md

# 对比验证
python scripts/cli.py compare <case_id_before> <case_id_after> -o comparison.md
```

### 场景 3: 卡顿问题诊断

```bash
# 分析卡顿
python scripts/cli.py analyze <case_id> --jank --top 20

# 查看卡顿帧的热点函数
python scripts/cli.py analyze <case_id> --jank --include-hotspots
```

### 场景 4: 内存问题诊断

```bash
# 内存分析
python scripts/cli.py analyze <case_id> --memory --include-trend

# 如果怀疑内存泄漏
python scripts/cli.py analyze <case_id> --memory --detailed
```

---

## 缓存管理

### 查看缓存状态

```bash
# 查看所有缓存
python scripts/cli.py cache --list

# 查看特定案例缓存
python scripts/cli.py cache --show <case_id>
```

### 清理缓存

```bash
# 清理特定案例缓存
python scripts/cli.py clear-cache <case_id>

# 清理所有缓存
python scripts/cli.py clear-cache all

# 清理过期缓存
python scripts/cli.py clear-cache --expired
```

---

## 输出格式

### Markdown 格式（默认）

所有报告默认输出为 Markdown 格式，易于阅读和版本控制。

### JSON 格式

用于程序化处理和数据导入。

```bash
# 输出 JSON
python scripts/cli.py analyze <case_id> --overview --format json -o report.json
```

### HTML 格式

用于网页展示和分享（未来实现）。

```bash
# 输出 HTML（未来）
python scripts/cli.py analyze <case_id> --overview --format html -o report.html
```

---

## 批量分析

### 批量概况分析

```bash
# 从文件读取案例 ID 列表
cat case_ids.txt | xargs -I {} python scripts/cli.py analyze {} --overview -o reports/{}.md
```

### 批量对比分析

```bash
# 对比基准与多个版本
baseline="baseline_uuid"
for version in v1 v2 v3; do
    python scripts/cli.py compare $baseline $version -o comparisons/$version.md
done
```

---

## 相关文档

- **[SKILL.md](../SKILL.md)** - Agent Skill 定义文档（主要文档）
- **[数据策略](data-strategy.md)** - 数据获取和缓存策略
