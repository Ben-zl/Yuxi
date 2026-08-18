# 平台阈值支持说明

## 概述

Unity Profiler Analyzer 现已支持**手游**和**端游**两种平台的性能评估标准，使用不同的性能阈值进行评估。

---

## 修改内容

### 1. 阈值定义 (`scripts/utils/thresholds.py`)

新增 `platform` 参数到 `Thresholds` 类：

```python
from scripts.utils.thresholds import PC_THRESHOLDS, Thresholds

# 方式 1：使用预定义实例
thresholds = PC_THRESHOLDS

# 方式 2：动态创建
thresholds = Thresholds(platform="pc")

# 访问阈值
print(f"Memory warning: {thresholds.memory_total_warning} MB")  # 2048
print(f"DrawCall critical: {thresholds.drawcall_critical}")     # 2000
```

### 2. CLI 参数 (`scripts/cli.py`)

新增 `--platform` 参数：

```bash
# 手游标准（默认）
python scripts/cli.py analyze <case_id> --overview

# 端游标准
python scripts/cli.py analyze <case_id> --overview --platform pc
```

### 3. 文档更新 (`docs/overview.md`)

更新性能评估标准，分别说明手游和端游的阈值。

---

## 阈值对比

### 帧率评估

| 等级 | 手游 | 端游 |
|------|------|------|
| 优秀 | ≥ 60 FPS | ≥ 144 FPS |
| 良好 | ≥ 45 FPS | ≥ 60 FPS |
| 可接受 | ≥ 30 FPS | ≥ 30 FPS |
| 较差 | ≥ 20 FPS | ≥ 20 FPS |
| 极差 | < 20 FPS | < 20 FPS |

### 内存评估

| 等级 | 手游 | 端游 |
|------|------|------|
| 正常 | < 500 MB | < 2 GB |
| 警告 | ≥ 500 MB | ≥ 2 GB |
| 严重 | ≥ 1000 MB | ≥ 4 GB |
| 紧急 | ≥ 1500 MB | ≥ 6 GB |

### 渲染评估

| 等级 | 手游 | 端游 |
|------|------|------|
| 优秀 | ≤ 100 | ≤ 500 |
| 良好 | ≤ 300 | ≤ 1000 |
| 警告 | ≥ 300 | ≥ 1000 |
| 严重 | ≥ 500 | ≥ 2000 |

---

## 使用示例

### 基本用法

```bash
# 分析端游数据，使用端游阈值
python scripts/cli.py analyze bf909275-f505-11f0-9e9c-708bcdbcb7b1 \
  --overview \
  --platform pc \
  --project pcavpt6w \
  -o overview.json

# 查看详细输出（包含平台信息）
python scripts/cli.py analyze bf909275-f505-11f0-9e9c-708bcdbcb7b1 \
  --overview \
  --platform pc \
  --verbose
```

### Python 代码调用

```python
from scripts.analyzer import ProfilerAnalyzer

# 为端游创建分析器
analyzer = ProfilerAnalyzer(
    base_url="http://10.11.10.173:8080",
    project_id="pcavpt6w",
    platform="pc"  # 使用端游阈值
)

# 执行分析
result = analyzer.analyze_overview(case_id)
```

---

## 端游阈值说明

### 为什么端游阈值更宽松？

1. **硬件性能更强**
   - PC 通常有 8GB-32GB 内存
   - GPU 性能远超移动设备
   - CPU 核心数和频率更高

2. **散热和电源**
   - PC 不受电池限制
   - 散热条件更好
   - 可以持续高性能运行

3. **显示设备**
   - 支持高刷新率（144Hz、240Hz）
   - 更高的分辨率要求

### 仍需注意的问题

即使使用端游阈值，以下问题仍需关注：

- **内存泄漏**：长期运行可能导致内存无限增长
- **GC 频率**：每帧 GC 是极度异常的现象
- **DrawCalls 过多**：即使端游，>2000 DrawCalls 仍需优化
- **低配设备兼容性**：考虑低端 PC 玩家的体验

---

## 版本历史

| 版本 | 日期 | 变更内容 |
|------|------|----------|
| v1.0 | 2026-01-30 | 新增平台阈值支持（mobile/pc） |
