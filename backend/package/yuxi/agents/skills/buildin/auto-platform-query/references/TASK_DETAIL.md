# 任务详情查询指南

任务详情查询命令返回设备执行情况和性能数据的完整分析指南。

---

## 目录

- [快速开始](#快速开始)
- [命令说明](#命令说明)
- [性能指标说明](#性能指标说明)
- [输出格式示例](#输出格式示例)
- [数据结构](#数据结构)
- [AI 分析要点](#ai-分析要点)

---

## 快速开始

#### Step 0: Task Discovery (推荐)

如果您不知道任务 ID，先使用发现工作流:

```bash
# 发现任务
python scripts/cli.py tasks --build-name "TDR" --start-time "2026-02-01" --end-time "2026-02-14" --discover
```

系统将返回精简的任务列表和推荐的分析类型，帮助您选择正确的任务/流水线 ID。

### 查询流程

```bash
# 方式1：按任务名称查询（默认最近1个月）
python scripts/cli.py tasks --build-name "TDR"

# 方式2：按时间范围查询
python scripts/cli.py tasks --build-name "TDR" --start-time "2026-01-14" --end-time "2026-02-14"

# 方式3：按流水线 ID 查询（原有方式）
python scripts/cli.py tasks --pipeline-id <流水线ID> --count 1

# 从返回结果获取 pipelineId 和 buildId，然后执行后续操作
python scripts/cli.py builds --id <BuildID> --device-executions
```

---

## 命令说明

### builds --device-executions

获取任务的完整详情，包括用例执行情况和设备性能数据。

**语法**:
```bash
python scripts/cli.py builds --id <BuildID> --device-executions [选项]
```

**选项**:
| 选项 | 说明 |
|------|------|
| `--id <BuildID>` | 构建/任务 ID（必需） |
| `--device-executions` | 显示设备执行详情（含性能数据） |
| `--device-id <ID>` | 只显示特定设备的执行情况 |
| `--output-file PATH` | 输出到文件（JSON 格式） |

**输出格式**:
- 所有输出都是 JSON 格式（专为 AI Agent 分析设计）
- JSON 数据结构包含：`task`, `summary`, `cases`, `ai_analysis_tips`

---

## 性能指标说明

性能指标详细说明见：[SHARED.md](./SHARED.md#性能指标说明)

**性能基准**：
- FPS (TP90): PC ≥ 60 FPS
- JANK: < 10 次/10min
- 峰值内存: < 设备内存的 50%

---

## 输出格式示例

### 任务概要

| 项目 | 信息 |
|------|------|
| **Build ID** | 128539 |
| **任务名称** | TDR 日常监控 release（PC）（分支）-#76 |
| **状态** | FAILED |
| **开始时间** | 2026-01-31 05:25:17 |
| **结束时间** | 2026-01-31 07:38:11 |

### 用例详细执行情况

#### 1. 主城跑图(TDR)

**用例 ID**: 12154 | **状态**: FAILED

| 设备 ID | 设备名称 | 状态 | FPS (TP90) | JANK (/10min) | 峰值内存 (MB) |
|---------|----------|------|------------|---------------|---------------|
| 1164 | i7-6700K | GTX1060 | SUCCESS | 63.89 | 2.88 | 7500.5 |
| 1166 | i3-2120 | GTX650 | SUCCESS | 34.38 | 246.13 | 6500.2 |

**性能统计** (成功执行的设备):
- **FPS (TP90)**: 平均=49.14, 最小=34.38, 最大=63.89
- **JANK (/10min)**: 平均=124.50, 最小=2.88, 最大=246.13

---

## 数据结构

### API 响应结构

任务详情通过 `GET /api/tasks/detail/{task_id}` 接口获取：

```json
{
  "buildId": 45678,
  "buildName": "TDR 日常监控 release（PC）  #2024-01-31 10:00",
  "pipelineName": "TDR 日常监控 release（PC）（分支）",
  "status": "SUCCESS",
  "startTime": "2024-01-31 10:00:00",
  "endTime": "2024-01-31 12:30:00",
  "executeTime": "2h30m",
  "caseDetails": [
    {
      "caseId": 12154,
      "caseName": "主城跑图(TDR)",
      "status": "SUCCESS",
      "deviceDetail": [
        {
          "deviceId": 513,
          "deviceName": "Mi 11 Pro",
          "deviceStatus": 1,
          "status": "SUCCESS",
          "platform": "Android",
          "systemVersion": "12",
          "startTime": "2024-01-31 10:05:00",
          "endTime": "2024-01-31 10:35:00",
          "perfeyeData": "{\"LabelFPS.TP90\":\"62.17\",\"LabelFPS.Jank(/10min)\":\"5.78\",\"LabelMemory.PeakMemoryDeposit(MB)\":\"7499.62\"}"
        }
      ]
    }
  ]
}
```

### 数据访问路径

| 数据类型 | 访问路径 |
|---------|---------|
| 任务信息 | 根字段（`buildId`, `status`, `pipelineName` 等） |
| 用例列表 | `caseDetails[]` |
| 设备列表 | `caseDetails[].deviceDetail[]` |
| 设备在线状态 | `deviceDetail[].deviceStatus`（0=离线，1=在线，默认为1） |
| 执行状态 | `deviceDetail[].status`（SUCCESS/FAILED/RUNNING/QUEUE/CANCEL） |
| 设备开始时间 | `deviceDetail[].startTime`（用于计算执行时长） |
| 设备结束时间 | `deviceDetail[].endTime`（用于计算执行时长） |
| 性能数据 | `deviceDetail[].perfeyeData` (JSON 字符串，需解析) |

**离线设备处理规则**: 见 [SHARED.md](./SHARED.md#离线设备处理规则)

---

## AI 分析要点

### 性能分析
- 检查 FPS 是否达标（PC ≥ 60 FPS）
- 分析 JANK 值，判断卡顿是否严重（< 10 次/10min）
- 了解内存峰值分布情况

### 失败分析
- 统计失败的用例和设备
- 识别普遍失败 vs 个别设备失败

### 崩溃分析

**检测 Crasheye 崩溃记录**：

JSON 数据中每个设备包含 `crasheye_url` 字段（如果有崩溃记录）。

**AI Agent 处理流程**：
1. 检测崩溃记录：检查 `crasheye_url` 字段
2. 提示用户分析：发现崩溃记录时提示用户调用 `crasheye-crash-workflow` skill

**提示模板**：
```
⚠️ 检测到崩溃记录：
- 崩溃设备数：X 台
- 崩溃 URL 列表：
  1. 设备A: https://crasheye.testplus.cn/dump/12345
  2. 设备B: https://crasheye.testplus.cn/dump/12346

💡 建议调用 crasheye-crash-workflow skill 进行崩溃分析：
  - 该 skill 可以自动下载崩溃转储文件
  - 使用 WinDbg 进行符号化分析
  - 生成崩溃分析报告（调用栈、寄存器状态、根本原因）
```

---

## 稳定性测试专项分析

如果用例名称中包含"稳定性"关键字，请使用专门的稳定性测试分析文档：
**[STABILITY_TEST_DETAIL.md](./STABILITY_TEST_DETAIL.md)**

该文档包含稳定性测试的专门分析规则，包括：
- 设备状态统计（区分在线/离线设备）
- 执行时长统计（按状态区分）
- 内存使用统计（按配置分级）
- 稳定性测试专项报告模板
