---
name: perfeye-analysis
description: |
  perfeye 平台性能数据分析工具。用于从 perfeye 平台获取和分析游戏性能数据，支持单任务性能分析和多任务性能对比。

  Use when Claude needs to work with perfeye performance data:
  - Analyze single task performance metrics (FPS, JANK, CPU, GPU, Memory, Renderer, Network)
  - Compare performance across multiple tasks to detect regressions
  - Generate performance analysis or comparison reports
  - Identify performance bottlenecks and provide optimization recommendations

  Prerequisites: pip install requests
allowed-tools: Bash, Read
---

## 环境要求

```bash
pip install requests
```

## 快速开始

### 单任务性能分析

```bash
# 方式 1: 直接使用 UUID
python3 <skill-dir>/scripts/perfeye_cli.py --uuid <task_uuid> --metrics

# 方式 2: 从 URL 提取 UUID（AI Agent 自动处理）
# URL 格式: https://perfeye.testplus.cn/case/{uuid}/report?appKey={appkey}
# AI Agent 会自动从 URL 中提取 {uuid} 部分，然后执行上述命令

# 示例工作流程：
# 用户提供: https://perfeye.testplus.cn/case/69816de7864483992eeb120d/report?appKey=test
# AI Agent: 提取 UUID -> 69816de7864483992eeb120d
# AI Agent: 执行 -> python3 <skill-dir>/scripts/perfeye_cli.py --uuid 69816de7864483992eeb120d --metrics

# 然后告诉 AI Agent: "分析上述性能数据"
```

**详细指南**: [references/PERFORMANCE_ANALYSIS.md](references/PERFORMANCE_ANALYSIS.md)

### 多任务性能对比

```bash
# 获取多个任务数据
python3 <skill-dir>/scripts/perfeye_cli.py --uuid <uuid1> --metrics -o /workspace/outputs/tmp/task1.json
python3 <skill-dir>/scripts/perfeye_cli.py --uuid <uuid2> --metrics -o /workspace/outputs/tmp/task2.json

# 或者用户直接提供多个 URL，AI Agent 自动提取 UUID 并执行上述命令

# 然后告诉 AI Agent: "对比上述性能数据，生成对比报告"
```

**详细指南**: [references/PERFORMANCE_COMPARISON.md](references/PERFORMANCE_COMPARISON.md)

如果确实需要临时编写脚本，脚本必须写入 `/workspace/outputs/tmp/`，执行时使用绝对路径，
例如 `python3 /workspace/outputs/tmp/analyze.py`。Bash 当前目录不是稳定协议，不要执行
依赖当前目录的 `python3 analyze.py`；能直接用 Read 读取 JSON 并由 Agent 统计时，不要额外写脚本。

## 参考文档

分析时请查阅对应的详细文档：

| 文档 | 用途 | 何时查阅 |
|------|------|----------|
| **[PERFORMANCE_ANALYSIS.md](references/PERFORMANCE_ANALYSIS.md)** | 单任务性能分析完整指南 | 分析单个任务时 |
| **[PERFORMANCE_COMPARISON.md](references/PERFORMANCE_COMPARISON.md)** | 多任务性能对比完整指南 | 对比多个任务时 |
| **[API.md](references/API.md)** | perfeye API 详细文档 | 需要了解 API 详情时 |
| **[METRICS.md](references/METRICS.md)** | 性能指标详细说明 | 需要了解指标含义时 |

## 关键概念

### 数据源

- **LabelInfo.All**: 包含所有统计数据的汇总，是性能分析的主要数据源
- **BaseInfo**: 测试环境信息（CPU、GPU、版本、画质、案例名等）

### 核心指标类别

| 类别 | 主要指标 |
|------|----------|
| **FPS** | AvgFPS, TP90, Tp90(FPSState_1), Jank(/10min), BigJank(/10min) |
| **CPU** | AvgApp(%), MaxApp(%), AvgCTemp, MaxCTemp |
| **GPU** | Avg(GPULoad)%、Max(GPULoad)%、AvgGTemp、MaxGTemp |
| **Memory** | InitMemory(MB), AvgMemory(MB), PeakMemory(MB) |
| **Renderer** | Avg(Drawcall), Avg(PrimitiveCount), Avg(VertexCount) |
| **Network** | AvgRecv(KB/s), AvgSend(KB/s), MaxRecv(KB/s), MaxSend(KB/s) |

### 达标标准（按画质分级）

DrawCall 和面数达标标准根据画质设置有所不同：

| 画质 | DrawCall 标准 | 面数标准 |
|------|---------------|----------|
| 极高 | < 5000 | < 1000万 |
| 高 | < 4000 | < 700万 |
| 中 | < 2500 | < 400万 |
| 低 | < 1600 | < 200万 |
| 极低 | < 1000 | < 120万 |

### 画质级别识别

- "Ultra"、"Very High"、"极高" → 极高
- "High"、"高" → 高
- "Medium"、"中" → 中
- "Low"、"低" → 低
- "Very Low"、"极低" → 极低

## CLI 工具

```bash
# 获取性能指标
python3 <skill-dir>/scripts/perfeye_cli.py --uuid <task_uuid> --metrics

# 输出到文件
python3 <skill-dir>/scripts/perfeye_cli.py --uuid <task_uuid> --metrics -o /workspace/outputs/tmp/output.json

# 检查 API 连接
python3 <skill-dir>/scripts/perfeye_cli.py --check
```

### URL 格式支持

当用户提供 perfeye 报告 URL 时，AI Agent 需要先从 URL 中提取 UUID，然后再执行 CLI 命令。

**支持的 URL 格式**:
```
https://perfeye.testplus.cn/case/{uuid}/report?appKey={appkey}
http://perfeye.console.testplus.cn/case/{uuid}/report
```

**UUID 提取规则**:
- 从 URL 路径中提取 `/case/{uuid}/report` 部分
- UUID 通常是 16-32 位的十六进制字符串
- 示例: `https://perfeye.testplus.cn/case/69816de7864483992eeb120d/report?appKey=test`
  - 提取的 UUID: `69816de7864483992eeb120d`

**AI Agent 处理流程**:
1. 用户输入: `https://perfeye.testplus.cn/case/{uuid}/report?appKey={appkey}`
2. AI Agent: 使用正则表达式提取 UUID 部分
3. AI Agent: 执行 `python3 <skill-dir>/scripts/perfeye_cli.py --uuid {uuid} --metrics`
4. AI Agent: 分析返回的性能数据
