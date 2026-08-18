# 性能概况分析 (Overview Analysis)

## 模块定位

对整个性能报告进行宏观概览，提供关键指标的快速诊断。

---

## ⚠️ 前置要求：文件管理规范

**在开始性能概况分析之前，AI Agent 必须先查阅**：[📖 文件管理规范](file-management.md)

### 为什么重要？

性能概况分析会生成**临时文件**和**最终报告**，必须严格遵守统一的文件管理规范以确保：
- ✅ 临时文件正确存放和命名
- ✅ 最终报告使用规范格式（包含时间戳）
- ✅ 数据来源可追溯
- ✅ 避免文件混乱和覆盖

### 本分析类型的文件清单

**临时文件**（存放于 `.upa_temp/{case_id}/`）：
- `overview.json` - 性能概况数据

**最终报告**（存放于 `reports/{case_id}/`）：
- `overview_{case_id}_{timestamp}.md` - 性能概况分析报告
  - 时间戳格式：`YYYYMMDD_HHMMSS`
  - 示例：`overview_bf909275-f505-11f0-9e9c-708bcdbcb7b1_20260127_143055.md`

### 必须遵守的规则

1. **临时文件目录**：`.upa_temp/{case_id}/`
2. **报告目录**：`reports/{case_id}/`
3. **临时文件命名**：`overview.json`
4. **报告文件命名**：`overview_{case_id}_{timestamp}.md`
5. **报告生成完成后**：建议清理临时文件

**📖 详细规范请查阅**：[file-management.md](file-management.md)

---

## 分析内容

### 基础信息
- **游戏名称**: 项目标识
- **案例名称**: 测试用例名称
- **游戏版本**: 版本号
- **测试设备**: 设备型号和配置
- **测试时间**: 采集时间
- **总帧数**: 采集的帧总数

### 帧率统计
| 指标 | 说明 |
|------|------|
| 平均 FPS | 整体帧率表现 |
| 最高 FPS | 最佳帧率 |
| 最低 FPS | 最差帧率 |
| FPS 标准差 | 帧率稳定性 |
| 平均帧时间 | 平均每帧耗时 (ms) |

### 帧时间分布
| 百分位 | 说明 |
|--------|------|
| P50 | 中位数帧时间 |
| P90 | 90% 的帧时间低于此值 |
| P99 | 99% 的帧时间低于此值 |

### 内存概览
| 类型 | 预留 (MB) | 已用 (MB) | 使用率 |
|------|-----------|-----------|--------|
| 总内存 | 系统分配的总内存 | 实际使用的内存 | 使用百分比 |
| Mono 堆 | Mono 堆预留大小 | Mono 堆使用大小 | 堆使用率 |

### 渲染概览
| 指标 | 说明 |
|------|------|
| DrawCalls | 渲染调用次数 |
| SetPass Calls | Shader Pass 切换次数 |
| 三角形数 | 渲染的三角形总数 |
| 顶点数 | 渲染的顶点总数 |

### CPU 时间分配
| 模块 | 时间 (ms) | 占比 |
|------|-----------|------|
| 渲染 | 渲染模块平均耗时 | 占总帧时间百分比 |
| 脚本 | 脚本执行平均耗时 | 占总帧时间百分比 |
| 物理 | 物理模拟平均耗时 | 占总帧时间百分比 |
| 动画 | 动画更新平均耗时 | 占总帧时间百分比 |
| GC | 垃圾回收平均耗时 | 占总帧时间百分比 |
| 其他 | 其他模块耗时 | 占总帧时间百分比 |

## 数据获取方式

### 使用命令行工具

使用 `scripts/cli.py` 命令行工具直接获取性能概况数据：

```bash
# 基本概况分析（输出 JSON 数据）
python scripts/cli.py analyze <case_id> --overview

# 指定输出文件
python scripts/cli.py analyze <case_id> --overview -o overview_data.json

# 不使用缓存（强制重新获取）
python scripts/cli.py analyze <case_id> --overview --no-cache

# 指定项目和 API 地址
python scripts/cli.py analyze <case_id> --overview --project <project_id> --url <api_url>

# 详细输出
python scripts/cli.py analyze <case_id> --overview --verbose
```

### 命令行参数说明

| 参数 | 说明 |
|------|------|
| `case_id` | 案例 ID（必需） |
| `--overview` | 执行性能概况分析 |
| `-o, --output` | 输出文件路径（可选） |
| `--project` | 项目 ID/APPKEY（可通过环境变量 UBOX_PROJECT_ID 设置） |
| `--url` | API 基础 URL（默认：http://10.11.10.173:8080） |
| `--no-cache` | 禁用缓存，强制重新获取数据 |
| `--verbose, -v` | 详细输出模式 |

### 数据获取流程

```
1. python scripts/cli.py analyze <case_id> --overview
   ↓ 调用命令行工具
2. ProfilerAnalyzer.analyze_overview(case_id)
   ↓ 内部调用分析器
3. OverviewCollector.collect(case_id)
   ↓ 收集器自动调用多个 API
   ├─ report.get_report_info()    # 报告元数据
   ├─ report.get_case_info()       # 案例概览
   ├─ cpu.get_cpu_performance()    # CPU 性能
   ├─ memory.get_memory_info()     # 内存信息
   └─ graphic.get_graphic_profile() # 渲染性能
4. 数据汇总和计算
   ↓ 生成 JSON 格式数据
5. 输出到文件或标准输出
   ↓ 供 AI Agent 使用生成报告
```

## 性能评估

### 平台说明

本工具支持**手游**和**端游**两种平台的性能评估标准：

- **手游**（默认）：针对移动设备（手机/平板），资源受限，阈值较严格
- **端游**：针对 PC/主机平台，硬件性能更强，阈值相对宽松

---

### 手游性能评估（Mobile）

#### 帧率评估
- **优秀** (≥ 60 FPS): 帧率表现优秀
- **良好** (≥ 45 FPS): 帧率表现良好
- **可接受** (≥ 30 FPS): 帧率基本可接受
- **较差** (≥ 20 FPS): 帧率较差，需要优化
- **极差** (< 20 FPS): 帧率极差，严重影响体验

#### 内存评估
- **正常**: 总内存 < 500 MB
- **警告**: 总内存 ≥ 500 MB
- **严重**: 总内存 ≥ 1000 MB
- **紧急**: 总内存 ≥ 1500 MB

#### 渲染评估
- **优秀**: DrawCalls ≤ 100
- **良好**: DrawCalls ≤ 300
- **警告**: DrawCalls ≥ 300
- **严重**: DrawCalls ≥ 500

---

### 端游性能评估（PC/Console）

#### 帧率评估
- **优秀** (≥ 144 FPS): 满足高刷新率显示器要求
- **良好** (≥ 60 FPS): 满足标准显示器要求
- **可接受** (≥ 30 FPS): 帧率基本可接受
- **较差** (≥ 20 FPS): 帧率较差，需要优化
- **极差** (< 20 FPS): 帧率极差，严重影响体验

#### 内存评估
- **正常**: 总内存 < 2 GB
- **警告**: 总内存 ≥ 2 GB
- **严重**: 总内存 ≥ 4 GB
- **紧急**: 总内存 ≥ 6 GB

**说明**：端游通常运行在内存更大的设备上（8GB-32GB），因此内存阈值相对宽松。但仍需注意：
- 32 位应用地址空间限制（2-4 GB）
- 与其他应用共享系统内存
- 低配设备兼容性

#### 渲染评估
- **优秀**: DrawCalls ≤ 500
- **良好**: DrawCalls ≤ 1000
- **警告**: DrawCalls ≥ 1000
- **严重**: DrawCalls ≥ 2000

**说明**：PC GPU 性能更强，可以处理更多 DrawCalls，但仍需优化以保持高帧率。

## 输出报告模板

```markdown
# Unity Profiler 性能分析报告

## 📊 性能概况

### 基础信息
- **游戏名称**: {game_name}
- **案例名称**: {case_name}
- **游戏版本**: {game_version}
- **测试设备**: {device_info}
- **测试时间**: {test_time}
- **总帧数**: {frame_count}

### 帧率统计
| 指标 | 数值 | 评估 |
|------|------|------|
| 平均 FPS | {avg_fps} | {status} |
| 最高 FPS | {max_fps} | - |
| 最低 FPS | {min_fps} | - |
| FPS 标准差 | {fps_std} | {stability} |
| 平均帧时间 | {avg_frame_time} ms | - |

### 帧时间分布
| 百分位 | 帧时间 (ms) | 评估 |
|--------|-------------|------|
| P50 | {p50} | - |
| P90 | {p90} | - |
| P99 | {p99} | {status} |

### 内存概览
| 类型 | 预留 (MB) | 已用 (MB) | 使用率 | 评估 |
|------|-----------|-----------|--------|------|
| 总内存 | {total_reserved} | {total_used} | {total_usage}% | {status} |
| Mono 堆 | {mono_reserved} | {mono_used} | {mono_usage}% | {status} |

### 渲染概览
| 指标 | 数值 | 评估 |
|------|------|------|
| DrawCalls | {draw_calls} | {status} |
| SetPass Calls | {setpass_calls} | - |
| 三角形数 | {triangles} | - |
| 顶点数 | {vertices} | - |

### CPU 时间分配
| 模块 | 时间 (ms) | 占比 | 评估 |
|------|-----------|------|------|
| 渲染 | {rendering_time} | {rendering_pct}% | {status} |
| 脚本 | {script_time} | {script_pct}% | {status} |
| 物理 | {physics_time} | {physics_pct}% | {status} |
| 动画 | {animation_time} | {animation_pct}% | {status} |
| GC | {gc_time} | {gc_pct}% | {status} |
| 其他 | {other_time} | {other_pct}% | - |
```

## 使用示例

### 命令行工具使用

```bash
# 基本概况分析（输出 JSON 到终端）
python scripts/cli.py analyze <case_id> --overview

# 保存到 JSON 文件
python scripts/cli.py analyze <case_id> --overview -o overview_data.json

# 完整示例：获取数据并用 AI 生成报告
python scripts/cli.py analyze bf909275-f505-11f0-9e9c-708bcdbcb7b1 \
  --overview \
  --project pcavpt6w \
  -o overview.json

# 不使用缓存（强制重新获取数据）
python scripts/cli.py analyze <case_id> --overview --no-cache

# 查看详细输出（包含缓存统计）
python scripts/cli.py analyze <case_id> --overview --verbose
```

### AI Agent 使用流程

当 AI Agent 需要生成性能概况报告时：

```bash
# 1. 调用命令行工具获取数据
python scripts/cli.py analyze <case_id> --overview -o overview.json

# 2. 读取 JSON 数据
# 3. 使用 AI 提示词生成 Markdown 报告
# 4. 保存报告到文件
```

### Python 代码调用（可选）

如果需要在 Python 代码中直接调用：

```python
from scripts.analyzer import ProfilerAnalyzer

analyzer = ProfilerAnalyzer(
    base_url="http://10.11.10.173:8080",
    project_id="your_project_id"
)

# 分析概况
result = analyzer.analyze_overview(case_id="uuid", use_cache=True)

# result 包含以下字段：
# - case_id: 案例 ID
# - report_info: 报告基础信息
# - metrics: 性能指标（fps, frame_time, memory_total, draw_calls, gc_count, gc_time）
# - evaluation: 评估结果（fps, memory, rendering）
```

## AI 报告生成提示词

当用户请求性能概况分析时，使用以下提示词模板生成报告：

```
请基于以下 Unity Profiler 性能数据，生成一份专业的 Markdown 格式性能概况分析报告。

**数据内容**：
{profiler_data_json}

**平台类型**：{platform_type}（mobile 或 pc）

**报告要求**：

1. **报告标题**：# Unity Profiler 性能分析报告

2. **性能概况章节**（## 📊 性能概况）
   - 游戏名称、版本、测试设备、测试时间等基础信息
   - 关键性能指标表格（FPS、帧时间、内存、DrawCalls、GC 等）

3. **性能评估章节**（## 🔍 性能评估）
   - 根据平台类型选择相应的评估标准：

   **手游标准（Mobile）**：
     * FPS: <30 严重，30-45 需优化，45-60 良好，>60 优秀
     * 帧时间: >33ms 严重，22-33ms 需优化，<22ms 良好
     * 内存: >1.5GB 紧急，1-1.5GB 严重，0.5-1GB 警告，<0.5GB 正常
     * DrawCalls: >500 严重，300-500 警告，100-300 良好，<100 优秀
     * GC: 频繁 GC 或单次 >10ms 需优化

   **端游标准（PC/Console）**：
     * FPS: <30 严重，30-60 可接受，60-144 良好，>144 优秀
     * 帧时间: >33ms 严重，22-33ms 需优化，<22ms 良好
     * 内存: >6GB 紧急，4-6GB 严重，2-4GB 警告，<2GB 正常
     * DrawCalls: >2000 严重，1000-2000 警告，500-1000 良好，<500 优秀
     * GC: 频繁 GC 或单次 >10ms 需优化

4. **优化建议章节**（## 💡 优化建议）
   - 根据性能问题，给出具体的优化建议
   - 按优先级排序（高优先级先）
   - 包含可操作的优化方向
   - 考虑平台特性（端游可以利用更强硬件，手游需更严格优化）

5. **数据来源说明**：注明数据来源、平台类型和生成时间

**输出格式**：纯 Markdown 格式，使用表格展示数据，使用表情符号增强可读性。
```

### 使用端游阈值

要在代码中使用端游阈值，可以这样：

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

## 相关模块

- [热点函数分析](hotspot-analysis.md) - 深入分析性能热点
- [帧率卡顿分析](jank-analysis.md) - 分析卡顿问题
- [内存专项分析](memory-analysis.md) - 深入分析内存使用
- [CPU 模块分析](cpu-module.md) - 深入分析 CPU 性能
- [渲染模块分析](rendering-module.md) - 深入分析渲染性能
