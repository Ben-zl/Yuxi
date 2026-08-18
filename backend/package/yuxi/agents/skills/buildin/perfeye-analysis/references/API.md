# Perfeye API 文档

## 目录

- [API 基本信息](#api-基本信息)
- [认证](#认证)
- [请求格式](#请求格式)
- [响应格式](#响应格式)
- [数据结构](#数据结构)
- [错误码](#错误码)

---

## API 基本信息

- **Base URL**: `http://perfeye.console.testplus.cn`
- **Endpoint**: `/api/show/task/{uuid}`
- **Method**: POST
- **Content-Type**: `application/json`

## 认证

**Headers**:
```
Authorization: Bearer mj6cltF&!L#yWX8k
```

## 请求格式

```bash
POST /api/show/task/{uuid}
Authorization: Bearer mj6cltF&!L#yWX8k
Content-Type: application/json
```

## 响应格式

### 成功响应

```json
{
  "status": 200,
  "data": {
    "BaseInfo": {
      "CaseName": "场景测试(TDR)完整流程_High(10.11.144.215)",
      "AppVersion": "1.0.0.5949.181684",
      "CPUType": "Intel(R) Core(TM) i7-8700K CPU @ 3.70GHz",
      "GPUType": "NVIDIA GeForce RTX 2060",
      "PictureQuality": "High",
      "Resolution": "2560x1440",
      "RAMSize": "15.9 GB",
      "OSVersion": "Windows-10-10.0.19041-SP0",
      "GraphicsAPI": "DX11",
      "Duration": "0d 0h 10m 26s",
      "ReportTime": "2026-02-02 07:07:14"
    },
    "LabelInfo": {
      "All": {
        "LabelFPS": {
          "TotalFrames": 58664,
          "AvgFPS": "81.26",
          "TP90": "65.72",
          "Jank(/10min)": "19.92",
          "BigJank(/10min)": "15.77",
          "AllJank": 3,
          "AllBigJank": 2
        },
        "LabelModuleFPS": {
          "Tp90(FPSState_1)": "66.50"
        },
        "LabelCPU": {
          "AvgApp": "35.53",
          "AvgApp(%)": "35.53",
          "AvgCTemp": "68.48",
          "MaxApp(%)": "65.84",
          "MaxCTemp": "74.0"
        },
        "LabelMemory": {
          "InitMemory(MB)": "6910.55",
          "AvgMemory(MB)": "7716.59",
          "PeakMemory(MB)": "8303.18"
        },
        "LabelGPU": {
          "Avg(GPULoad)[%]": "87.6",
          "Max(GPULoad)[%]": "98.0",
          "Avg(GPUFreq)[MHz]": "1829.28",
          "Max(GPUFreq)[MHz]": "1860.0",
          "AvgGTemp": "81.66",
          "MaxGTemp": "83.0"
        },
        "LabelRenderer": {
          "Avg(Drawcall)": "2157.05",
          "Max(Drawcall)": "6651.0",
          "Avg(PrimitiveCount)": "5809825.08",
          "Peak(PrimitiveCount)": "12561826.0",
          "Avg(VertexCount)": "17429475.37",
          "Peak(VertexCount)": "37685480.0"
        },
        "LabelNetwork": {
          "AvgRecv(KB/s)": "28.31",
          "AvgSend(KB/s)": "0.18",
          "MaxRecv(KB/s)": "17516.54",
          "MaxSend(KB/s)": "5.63"
        }
      }
    }
  }
}
```

### 错误响应

```json
{
  "message": "task not found",
  "status": 404
}
```

## 数据结构

### BaseInfo（基础信息）

| 字段 | 说明 | 示例 |
|------|------|------|
| `CaseName` | 测试案例名称 | "场景测试(TDR)完整流程_High(10.11.144.215)" |
| `AppVersion` | 应用版本号 | "1.0.0.5949.181684" |
| `CPUType` | CPU 型号 | "Intel(R) Core(TM) i7-8700K CPU @ 3.70GHz" |
| `GPUType` | GPU 型号 | "NVIDIA GeForce RTX 2060" |
| `PictureQuality` | 画质设置 | "High" |
| `Resolution` | 分辨率 | "2560x1440" |
| `RAMSize` | 内存大小 | "15.9 GB" |

### LabelInfo.All（统计数据汇总）

LabelInfo.All 包含所有统计数据的汇总，是性能分析的主要数据源。

#### 数据类别

| 类别 | 说明 | 主要字段 |
|------|------|----------|
| **LabelFPS** | FPS 统计 | AvgFPS, TP90, Jank(/10min), BigJank(/10min) |
| **LabelModuleFPS** | 模块化 FPS | Tp90(FPSState_1) |
| **LabelCPU** | CPU 统计 | AvgApp(%), MaxApp(%), AvgCTemp, MaxCTemp |
| **LabelGPU** | GPU 统计 | Avg(GPULoad)[%], Max(GPULoad)[%], AvgGTemp, MaxGTemp |
| **LabelMemory** | 内存统计 | InitMemory(MB), AvgMemory(MB), PeakMemory(MB) |
| **LabelRenderer** | 渲染统计 | Avg(Drawcall), Avg(PrimitiveCount), Avg(VertexCount) |
| **LabelNetwork** | 网络统计 | AvgRecv(KB/s), AvgSend(KB/s), MaxRecv(KB/s), MaxSend(KB/s) |

**详细指标说明和判断标准**: 请参阅 [METRICS.md](METRICS.md)

## 错误码

| 状态码 | 说明 |
|--------|------|
| 200 | 成功 |
| 401 | 认证失败 |
| 404 | 任务不存在 |
| 500 | 服务器错误 |
