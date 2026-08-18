---
name: auto-platform-query
description: |
  Query automation testing platform for pipelines, tasks, devices, and cases.
  Get task details with performance metrics (FPS, JANK, memory), perform performance comparison, and analyze performance trends.
  CRITICAL: When analyzing tasks, MUST retrieve data for ALL statuses (RUNNING/QUEUE/CANCEL/FAILED/SUCCESS).

  Use when Claude needs to:
  - Query platform data (pipelines, tasks, devices, cases)
  - Get task details with performance metrics - include ALL task statuses
  - Compare performance across builds - include ALL tasks regardless of status
  - Analyze performance trend over a time range using get_pipeline_performance_trend

  Prerequisites: Configure AUTOMATION_PROJECT_ID and AUTOMATION_USER_ID
allowed-tools: Bash, Read, Write
---

# 自动化测试平台查询工具

自动化测试平台数据查询工具 - 查询流水线、任务详情和性能数据。

## 🔥 核心原则

**获取任务详情或进行性能对比时，不管任务和用例的状态如何，都必须获取和展示所有相关数据！**

**任务状态**: RUNNING, QUEUE, CANCEL, FAILED, SUCCESS
**用例状态**: SUCCESS, FAILED, QUEUE, RUNNING, CANCEL

**禁止**: ❌ 按任务/用例状态过滤数据（例如只显示 SUCCESS）
**必须**: ✅ 包含所有任务/用例状态，即使是 FAILED/CANCEL 数据

---

## 快速开始

### 环境配置

```bash
# 安装依赖
pip install -r requirements.txt

# 配置环境变量（在 .env 或系统环境中）
AUTOMATION_BASE_URL=https://automation-api.testplus.cn
AUTOMATION_PROJECT_ID=your_project_id
AUTOMATION_USER_ID=your_user_id
```

## 工作流决策指南

**重要**: 根据用户请求类型，必须加载对应的 reference 文档执行详细工作流。

### 决策树

```
用户请求类型判断：
├─ "任务详情" / "获取任务" / "查询任务" / "任务执行情况"
│  └─ 加载 references/TASK_DETAIL.md
│
├─ "稳定性测试" / 用例名称包含"稳定性"
│  └─ 加载 references/STABILITY_TEST_DETAIL.md
│
├─ "性能对比" / "对比性能" / "比较多次执行" / "性能变化"
│  └─ 加载 references/PERFORMANCE_COMPARISON.md
│
├─ "性能趋势" / "趋势分析" / "最近N天" / "时间段性能"
│  └─ 加载 references/PERFORMANCE_TREND.md
│
└─ "发现任务" / "查找流水线" / 不知道任务ID
   └─ 使用下方任务发现功能
```

---

## 核心工作流

### 0. 任务发现（统一入口）

**用途**: 在进行详细分析之前，先找到正确的任务或流水线

**何时使用**:
- 开始任何分析但不知道任务/流水线 ID
- 验证哪些任务匹配您的查询
- 从多个匹配任务中选择

**命令**:
```bash
# 根据任务名+时间范围发现任务
python scripts/cli.py tasks --build-name "TDR" --start-time "2026-02-01" --end-time "2026-02-14" --discover
```

**输出**: 精简的任务列表 + 智能推荐

### 1. 查询任务详情

**触发条件**: 用户请求包含 "任务详情"、"获取任务"、"查询任务"、"执行情况" 等关键词

**操作**: 加载 [references/TASK_DETAIL.md](references/TASK_DETAIL.md) 并执行其中的工作流

查询任务执行数据和性能指标。根据任务/用例名称自动路由到适当的工作流。

**稳定性测试专项**: 如果用例名称包含"稳定性"，加载 [references/STABILITY_TEST_DETAIL.md](references/STABILITY_TEST_DETAIL.md)

### 2. 性能对比

**触发条件**: 用户请求包含 "性能对比"、"对比性能"、"比较多次执行"、"性能变化" 等关键词

**操作**: 加载 [references/PERFORMANCE_COMPARISON.md](references/PERFORMANCE_COMPARISON.md) 并执行其中的工作流

对比多次构建的性能指标（FPS、JANK、内存）。支持基础对比和深度对比，自动筛查显著变化。

### 3. 性能趋势分析

**触发条件**: 用户请求包含 "性能趋势"、"趋势分析"、"最近N天"、"时间段" 等关键词，或请求获取流水线在时间范围内的性能变化

**操作**: 加载 [references/PERFORMANCE_TREND.md](references/PERFORMANCE_TREND.md) 并执行其中的工作流

分析时间范围内的性能趋势。生成包含回归检测、异常识别和优化建议的综合报告。

**⚠️ 重要：直接生成报告**

当需要生成性能趋势分析报告时，必须按照以下流程直接生成报告：

1. **获取数据**：使用 CLI 获取趋势数据
2. **AI 直接分析**：不写脚本，让 AI Agent 直接分析 JSON 数据
3. **保存报告**：使用 write 工具将报告保存到本地，文件名格式：`TDR_performance_trend_{天数}_{时间戳}.md`
4. **发送到群里**：使用 message 工具发送报告文件到群聊

**文件名格式**：`TDR_performance_trend_{天数}_{YYYYMMDD_HHMMSS}.md`
例如：`TDR_performance_trend_3days_20260317_013500.md`

---

## 输出格式

所有 CLI 输出均为 **JSON 格式**（专为 AI Agent 分析设计）：

```json
{
  "type": "device_executions",
  "task": {
    "build_id": 128639,
    "build_name": "...",
    "status": "FAILED",
    "execute_time": 24359,
    "total_cases": 7
  },
  "summary": {
    "case_status_distribution": {"FAILED": 7},
    "device_stats": {
      "total_devices": 9,
      "success_devices": 6,
      "failed_devices": 9
    }
  },
  "cases": [...]
}
```

**完整字段说明**:
- 任务详情: 参见 references/TASK_DETAIL.md
- 性能对比: 参见 references/PERFORMANCE_COMPARISON.md
- 性能趋势: 参见 references/PERFORMANCE_TREND.md

---

### 路径格式（Windows/Git Bash）

**Windows 路径**在 Git Bash 中需要使用 Unix 格式：

| 错误 | 正确 |
|--------|----------|-------------|
| `C:\Users\...` | `/c/Users/...` |
| `cd "C:\Users\..."` | `cd "/c/Users/..."` |

---

## 常见问题

**问: 可以查询 RUNNING/CANCEL 状态的任务详情吗？**
答: **可以！** 获取任务详情时不考虑状态（RUNNING/QUEUE/CANCEL/FAILED/SUCCESS 都可以）。

**问: 为什么要读取 references 文档？**
答: References 包含完整的工作流、数据结构、性能标准和最佳实践。

**问: 性能报告是如何生成的？**
答: 由 AI Agent 分析 JSON 数据生成 - 不是由 Python 脚本生成。AI 提供灵活、智能的分析。

**问: 输出格式是什么？**
答: 仅 JSON（专为 AI Agent 设计）。AI Agent 解析 JSON 并生成 Markdown 报告。
