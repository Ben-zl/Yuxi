# 完整性能分析 (Complete Performance Analysis)

## 模块定位

提供一份全面的 Unity Profiler 性能分析报告，通过整合所有分析模块（概况、热点函数、卡顿帧、内存），为用户提供一站式的性能诊断和优化建议。

**核心价值**：
- **一站式分析**：单次请求获取所有维度的性能分析
- **全局视角**：从整体到细节，全面了解游戏性能状况
- **优先级明确**：综合各模块问题，按影响程度排序优化建议

---

## ⚠️ 前置要求：文件管理规范

**在开始完整性能分析之前，AI Agent 必须先查阅**：[📖 文件管理规范](file-management.md)

### 为什么重要？

完整性能分析会生成**多个临时文件**和**一份最终报告**，必须严格遵守统一的文件管理规范以确保：
- ✅ 临时文件正确存放和命名
- ✅ 最终报告使用规范格式（包含时间戳）
- ✅ 数据来源可追溯
- ✅ 避免文件混乱和覆盖

### 本分析类型的文件清单

**临时文件**（存放于 `.upa_temp/{case_id}/`）：
- `overview.json` - 性能概况数据
- `hotspots_self_time.json` - 热点函数（按自身耗时排序）
- `hotspots_total_time.json` - 热点函数（按总耗时排序）
- `hotspots_valid_frame_avg.json` - 热点函数（按有效帧平均耗时排序）
- `hotspots_high_cost_frames.json` - 热点函数（按高耗时帧数排序）
- `merged_hotspots.json` - 合并后的热点函数数据
- `jank_ai_ready.json` - 卡顿帧分析数据（AI-Ready 模式）
- `memory.json` - 内存分析数据

**最终报告**（存放于 `reports/{case_id}/`）：
- `complete_{case_id}_{timestamp}.md` - 完整性能分析报告
  - 时间戳格式：`YYYYMMDD_HHMMSS`
  - 示例：`complete_bf909275-f505-11f0-9e9c-708bcdbcb7b1_20260127_143052.md`

### 必须遵守的规则

1. **临时文件目录**：`.upa_temp/{case_id}/`
2. **报告目录**：`reports/{case_id}/`
3. **临时文件命名**：`{data_type}_{dimension?}.json`
4. **报告文件命名**：`complete_{case_id}_{timestamp}.md`
5. **报告生成完成后**：建议清理临时文件

**📖 详细规范请查阅**：[file-management.md](file-management.md)

---

## 包含的分析模块

完整性能分析整合以下 4 个核心模块：

| 模块 | 说明 | 详细内容 | AI 提示词 |
|------|------|----------|-----------|
| **📊 性能概况** | 整体性能指标评估 | [→ 详细内容](overview.md#分析内容) | [→ 提示词](overview.md#ai-报告生成提示词) |
| **🔥 热点函数** | 4 维度函数瓶颈分析 | [→ 详细内容](hotspot-analysis.md#分析内容) | [→ 提示词](hotspot-analysis.md#ai-报告生成提示词) |
| **📉 卡顿帧** | 帧率波动和卡顿定位 | [→ 详细内容](jank-analysis.md#分析内容) | [→ 提示词](jank-analysis.md#ai-报告生成提示词) |
| **💾 内存分析** | 内存使用和 GC 性能 | [→ 详细内容](memory-analysis.md#分析内容) | [→ 提示词](memory-analysis.md#ai-报告生成提示词) |

---

## 工作流程

### 完整分析流程图

```
开始完整性能分析
    ↓
┌─────────────────────────────────────────────┐
│  数据获取阶段（使用命令行工具）               │
├─────────────────────────────────────────────┤
│ 1. 性能概况数据                              │
│    └─ python scripts/cli.py analyze --overview  │
│                                              │
│ 2. 热点函数数据（4个维度）                   │
│    └─ python scripts/cli.py analyze --hotspots  │
│       (self_time, total_time, valid_frame_avg,  │
│        high_cost_frames)                     │
│                                              │
│ 3. 卡顿帧数据                                │
│    └─ 通过 high_cost_frames 排序获取         │
└─────────────────────────────────────────────┘
    ↓
┌─────────────────────────────────────────────┐
│  数据处理阶段                                 │
├─────────────────────────────────────────────┤
│ 1. AI Agent 读取 JSON 数据                   │
│ 2. 数据整理和计算                            │
│ 3. 按模块组织分析结果                        │
└─────────────────────────────────────────────┘
    ↓
┌─────────────────────────────────────────────┐
│  报告生成阶段                                 │
├─────────────────────────────────────────────┤
│ 使用完整分析 AI 提示词模板                    │
│    ↓                                         │
│ 生成包含所有章节的 Markdown 报告              │
│    ↓                                         │
│ 保存报告到文件                               │
└─────────────────────────────────────────────┘
```

---

## 数据获取方式

### 使用命令行工具

完整性能分析需要多次调用命令行工具获取不同维度的数据：

```bash
# ========== 1. 获取性能概况数据 ==========
python scripts/cli.py analyze <case_id> --overview -o overview.json

# ========== 2. 获取热点函数数据（4个维度）==========

# 按自身耗时排序
python scripts/cli.py analyze <case_id> --hotspots --sort-by self_time --top 50 -o hotspots_self.json

# 按总耗时排序
python scripts/cli.py analyze <case_id> --hotspots --sort-by total_time --top 50 -o hotspots_total.json

# 按有效帧平均耗时排序
python scripts/cli.py analyze <case_id> --hotspots --sort-by valid_frame_avg --top 50 -o hotspots_valid.json

# 按高耗时帧数排序（卡顿分析）
python scripts/cli.py analyze <case_id> --hotspots --sort-by high_cost_frames --top 50 -o hotspots_jank.json

# ========== 3. 完整示例 ==========
python scripts/cli.py analyze bf909275-f505-11f0-9e9c-708bcdbcb7b1 \
  --overview \
  --project pcavpt6w \
  -o overview.json

python scripts/cli.py analyze bf909275-f505-11f0-9e9c-708bcdbcb7b1 \
  --hotspots \
  --sort-by self_time \
  --top 50 \
  --project pcavpt6w \
  -o hotspots.json
```

### 数据获取流程

```
1. python scripts/cli.py analyze <case_id> --overview -o overview.json
   ↓ 获取性能概况数据（FPS、内存、DrawCalls、GC 等）

2. python scripts/cli.py analyze <case_id> --hotspots --sort-by self_time --top 50
   ↓ 获取按自身耗时排序的热点函数

3. python scripts/cli.py analyze <case_id> --hotspots --sort-by total_time --top 50
   ↓ 获取按总耗时排序的热点函数

4. python scripts/cli.py analyze <case_id> --hotspots --sort-by valid_frame_avg --top 50
   ↓ 获取按有效帧平均耗时排序的热点函数

5. python scripts/cli.py analyze <case_id> --hotspots --sort-by high_cost_frames --top 50
   ↓ 获取按高耗时帧数排序的热点函数（卡顿分析）

6. AI Agent 读取所有 JSON 数据
   ↓ 使用完整分析 AI 提示词模板生成报告

7. 保存报告到文件
   ↓ 文件名格式：complete_{case_id}.md
```

---

## 报告结构

完整分析报告包含以下章节，点击可查看各模块详细说明：

### 第一部分：📊 性能概况

**内容来源**：[性能概况分析模块](overview.md)

**包含章节**：
- 基础信息（游戏名称、版本、设备等）
- 帧率统计（平均 FPS、最高/最低 FPS、FPS 标准差）
- 帧时间分布（P50、P90、P99）
- 内存概览（总内存、Mono 堆）
- 渲染概览（DrawCalls、SetPass Calls、三角形数）
- CPU 时间分配（渲染、脚本、物理、动画、GC）
- 性能评估（按阈值评估各项指标）

**详细内容**：[→ 查看完整说明](overview.md#分析内容)

---

### 第二部分：🔥 热点函数分析

**内容来源**：[热点函数分析模块](hotspot-analysis.md)

**包含章节**：
- 分析摘要（总函数数、排序维度、是否过滤 Unity Entry）
- Top 20 按自身耗时 (Self Time)
- Top 20 按总耗时 (Total Time)
- Top 20 按有效帧平均耗时 (Valid Frame Avg)
- Top 20 按高耗时帧数 (High Cost Frames)
- 各模块 Top 10（Rendering、Script、Physics、Animation、UI）

**详细内容**：[→ 查看完整说明](hotspot-analysis.md#分析内容)

---

### 第三部分：📉 卡顿帧分析

**内容来源**：[卡顿帧分析模块](jank-analysis.md)

**包含章节**：
- 卡顿概览（总帧数、卡顿帧数、卡顿率）
- Top 20 卡顿帧详情
- Top 5 卡顿帧的详细函数分析
- 重复出现的高耗时函数
- 卡顿原因分类统计
- 卡顿模式分析

**详细内容**：[→ 查看完整说明](jank-analysis.md#分析内容)

---

### 第四部分：💾 内存专项分析

**内容来源**：[内存分析模块](memory-analysis.md)

**包含章节**：
- 内存总览（各类内存使用情况）
- Mono 堆分析
- GC 性能分析（GC 次数、平均耗时、频率）
- 内存增长趋势
- 内存泄漏风险评估

**详细内容**：[→ 查看完整说明](memory-analysis.md#分析内容)

---

### 第五部分：💡 综合优化建议

**内容来源**：基于以上 4 个模块的分析结果，汇总生成综合优化建议

**包含章节**：
- **高优先级**（影响最大的 2-3 个问题）
  - 每个问题包含：影响描述、优化方案、预期效果
- **中优先级**（各模块的通用优化建议）
  - 渲染优化
  - 脚本优化
  - 物理优化
  - 动画优化
- **低优先级**（长期优化建议）
  - GC 优化
  - 内存优化

---

## AI 报告生成提示词

### 完整分析报告提示词模板

```
请基于以下 Unity Profiler 完整性能数据，生成一份专业的 Markdown 格式完整性能分析报告。

**数据内容**：
{
  // 基础信息
  "report_info": { ... },
  "case_info": { ... },

  // 性能概况数据
  "cpu_performance": { ... },
  "memory_info": { ... },
  "graphic_profile": { ... },

  // 热点函数数据（4 个维度）
  "hotspots": {
    "by_self_time": [...],
    "by_total_time": [...],
    "by_valid_frame_avg": [...],
    "by_high_cost_frames": [...],
    "by_module": {
      "Rendering": [...],
      "Script": [...],
      "Physics": [...],
      "Animation": [...],
      "UI": [...]
    }
  },

  // 卡顿帧数据
  "jank_frames": { ... },
}

**报告结构要求**：

请严格按照以下结构生成报告：

## 1. 报告标题
# Unity Profiler 完整性能分析报告

## 2. 性能概况章节（## 📊 性能概况）

参考 [性能概况分析模块](docs/overview.md) 的内容：

### 2.1 基础信息
- 游戏名称、案例名称、游戏版本、测试设备、测试时间、总帧数

### 2.2 帧率统计表
| 指标 | 数值 | 评估 |
|------|------|------|
| 平均 FPS | {value} | {status} |
| 最高 FPS | {value} | - |
| 最低 FPS | {value} | - |
| FPS 标准差 | {value} | {stability} |
| 平均帧时间 | {value} ms | {status} |

**性能评估标准**：
- FPS: <30 严重问题，30-50 需要优化，50-60 良好，>60 优秀
- 帧时间: >33ms 严重问题，20-33ms 需要优化，<20ms 良好

### 2.3 帧时间分布表
| 百分位 | 帧时间 (ms) | 评估 |
|--------|-------------|------|
| P50 | {value} | - |
| P90 | {value} | - |
| P99 | {value} | {status} |

### 2.4 内存概览表
| 类型 | 预留 (MB) | 已用 (MB) | 使用率 | 评估 |
|------|-----------|-----------|--------|------|
| 总内存 | {value} | {value} | {value}% | {status} |
| Mono 堆 | {value} | {value} | {value}% | {status} |

**性能评估标准**：
- 内存: >2GB 严重问题，1-2GB 需要关注，<1GB 良好

### 2.5 渲染概览表
| 指标 | 数值 | 评估 |
|------|------|------|
| DrawCalls | {value} | {status} |
| SetPass Calls | {value} | - |
| 三角形数 | {value} | - |
| 顶点数 | {value} | - |

**性能评估标准**：
- DrawCalls: >200 严重问题，100-200 需要优化，<100 良好

### 2.6 CPU 时间分配表
| 模块 | 时间 (ms) | 占比 | 评估 |
|------|-----------|------|------|
| 渲染 | {value} | {value}% | {status} |
| 脚本 | {value} | {value}% | {status} |
| 物理 | {value} | {value}% | {status} |
| 动画 | {value} | {value}% | {status} |
| GC | {value} | {value}% | {status} |

## 3. 热点函数分析章节（## 🔥 热点函数分析）

参考 [热点函数分析模块](docs/hotspot-analysis.md) 的内容：

### 3.1 分析摘要
- 总函数数: {value}
- Unity Entry 过滤: {true/false}
- 分析维度: 4 个维度（self_time、total_time、valid_frame_avg、high_cost_frames）

### 3.2 Top 20 按自身耗时 (Self Time)
表头：| 排名 | 函数名 | 模块 | Self Time (ms) | Total Time (ms) | 调用次数 |

### 3.3 Top 20 按总耗时 (Total Time)
表头：| 排名 | 函数名 | 模块 | Total Time (ms) | Self Time (ms) | 调用次数 |

### 3.4 Top 20 按有效帧平均耗时 (Valid Frame Avg)
表头：| 排名 | 函数名 | 有效帧数 | 平均耗时 (ms) | 最大耗时 (ms) | 高耗时帧数 |

### 3.5 Top 20 按高耗时帧数 (High Cost Frames)
表头：| 排名 | 函数名 | 高耗时帧数 | 最大耗时 (ms) | 总调用次数 | 占比 |

### 3.6 各模块 Top 10
为每个模块（Rendering、Script、Physics、Animation、UI）生成 Top 10 表格

### 3.7 问题函数分析
- 识别最耗时的前 5-10 个函数
- 分析可能的性能瓶颈原因
- 标注游戏代码函数（非 Unity 引擎函数）

## 4. 卡顿帧分析章节（## 📉 卡顿帧分析）

参考 [卡顿帧分析模块](docs/jank-analysis.md) 的内容：

### 4.1 卡顿概览
- 总帧数: {value}
- 卡顿帧数: {value}
- 卡顿率: {value}%
- 最卡顿帧: 帧 {value}，{value} ms

**注意**：如果没有卡顿帧（卡顿率 < 1%），说明帧率稳定，可跳过详细分析

### 4.2 Top 20 卡顿帧详情
表头：| 帧号 | 帧时间 (ms) | 目标帧率 | Top 1 耗时函数 | 耗时 (ms) | 模块 |

### 4.3 Top 5 卡顿帧详细函数分析
为每个卡顿帧生成详细的函数耗时表格（Top 5-10 函数）
表头：| 排名 | 函数名 | 自身耗时 (ms) | 总耗时 (ms) | 调用次数 | 模块 |

### 4.4 重复出现的高耗时函数
表头：| 函数名 | 出现次数 | 平均耗时 (ms) | 最大耗时 (ms) | 模块 |

### 4.5 卡顿原因分类统计
表头：| 原因类型 | 卡顿帧数 | 占比 | 典型帧号 |

## 5. 内存分析章节（## 💾 内存专项分析）

参考 [内存分析模块](docs/memory-analysis.md) 的内容：

### 5.1 内存总览表
表头：| 类型 | 预留 (MB) | 已用 (MB) | 使用率 | 评估 |

### 5.2 Mono 堆分析
- 堆大小: {value} MB
- GC 堆内存: {value} MB
- 已用内存: {value} MB
- 碎片化: {value}%

### 5.3 GC 性能分析表
表头：| 指标 | 数值 | 阈值 | 状态 |
包含：总 GC 次数、Loading GC、运行时 GC、平均 GC 耗时、GC 频率、GC 占比

**性能评估标准**：
- GC 耗时: >= 10ms 严重问题，>= 5ms 需要优化，< 5ms 良好
- GC 频率: >= 30 次/分钟 严重问题，>= 10 次/分钟 需要优化，< 10 次/分钟 良好

### 5.4 内存增长趋势
- 起始内存: {value} MB
- 结束内存: {value} MB
- 增长量: {value} MB
- 增长率: {value} MB/分钟

### 5.5 内存泄漏风险评估
{评估结果}

## 6. 综合优化建议章节（## 💡 综合优化建议）

**重要**：这是报告的核心价值部分，需要综合前 4 个模块的分析结果，按优先级组织优化建议。

### 6.1 高优先级（影响最大）
找出影响最大的 2-3 个问题，每个问题包含：
- **问题标题**: {简洁的问题描述}
  - **影响**: {具体影响说明}
  - **相关数据**: {支持数据，如 FPS 降低了 X%，某函数耗时 Y ms}
  - **优化方案**: {具体、可操作的优化建议}
  - **预期效果**: {优化后预期达到的效果}

### 6.2 中优先级
按模块分组提供通用优化建议：
1. **渲染优化**
   - 减少 DrawCall 数量
   - 使用 GPU Instancing
   - 优化 Shader 复杂度
   - 使用 LOD 系统

2. **脚本优化**
   - {基于热点函数分析的建议}

3. **物理优化**
   - {基于卡顿帧和热点函数的建议}

4. **动画优化**
   - {基于热点函数的建议}

### 6.3 低优先级（长期优化）
1. **GC 优化**
   - 减少运行时内存分配
   - 使用对象池管理频繁创建销毁的对象
   - 避免在 Update 中分配内存
   - 预分配常用数据结构

2. **内存优化**
   - {基于内存分析的建议}

## 7. 数据来源说明（## 📋 数据来源说明）
- 数据来源: ubox-api 模块
- Case ID: {value}
- 报告生成时间: {timestamp}

---

**输出格式要求**：
1. 使用标准 Markdown 格式
2. 所有表格使用 Markdown 表格语法
3. 函数名使用代码格式：`function_name`
4. 使用表情符号增强可读性：📊🔥📉💾💡🎯
5. 优化建议要具体、可操作，避免空泛
6. 按优先级从高到低组织优化建议
7. 使用准确的性能评估阈值

请基于以上要求和提供的数据，生成一份专业、完整、易读的性能分析报告。
```

---

## 使用示例

### 通过 AI Agent（推荐）

#### 方式一：直接提供 ubox.testplus.cn URL（最便捷）

直接从浏览器复制粘贴 URL：

```
用户: 分析这个链接的性能：https://ubox.testplus.cn/project/starsandisland/appKey/pcavpt6w/detail/bf909275-f505-11f0-9e9c-708bcdbcb7b1/summaryHome

AI: 正在解析 URL...
    ✅ 提取成功！
    - project_id: pcavpt6w
    - case_id: bf909275-f505-11f0-9e9c-708bcdbcb7b1

    开始生成完整性能分析报告...
    [分析过程...]
```

**支持的 URL 格式**：
- 标准格式：`/appKey/{project_id}/detail/{caseid}/summaryHome`
- 带参数：`?tab=overview` 等查询参数不影响解析
- HTTP/HTTPS 均支持

#### 方式二：提供 project_id 和 case_id 参数

```
"生成完整分析报告，project_id 是 pcavpt6w，case_id 是 bf909275-f505-11f0-9e9c-708bcdbcb7b1"

"全面分析 Unity 性能数据"

"生成包含概况、热点、卡顿、内存的完整报告"
```

#### 方式三：提供已导出的 JSON 数据文件

```
"基于这个数据文件生成分析报告：/path/to/data.json"
```

---

### URL 解析工具函数

**Python 实现**（已集成到 AI Agent）：

```python
import re

def parse_ubox_url(url: str) -> dict:
    """从 ubox.testplus.cn URL 提取 project_id 和 case_id"""
    pattern = r'/appKey/(?P<project_id>[^/]+)/detail/(?P<case_id>[^/]+)/summaryHome'
    match = re.search(pattern, url)
    if match:
        return {
            'project_id': match.group('project_id'),
            'case_id': match.group('case_id')
        }
    raise ValueError(f"Invalid ubox URL format: {url}")
```

---

### 代码调用

```python
from ubox_profiler import UboxProfilerClient
import re

# 方式一：从 URL 解析
url = "https://ubox.testplus.cn/project/starsandisland/appKey/pcavpt6w/detail/bf909275-f505-11f0-9e9c-708bcdbcb7b1/summaryHome"

# 解析 URL
def parse_ubox_url(url: str) -> dict:
    pattern = r'/appKey/(?P<project_id>[^/]+)/detail/(?P<case_id>[^/]+)/summaryHome'
    match = re.search(pattern, url)
    if match:
        return {
            'project_id': match.group('project_id'),
            'case_id': match.group('case_id')
        }
    raise ValueError(f"Invalid URL: {url}")

parsed = parse_ubox_url(url)
project_id = parsed['project_id']  # pcavpt6w
case_id = parsed['case_id']        # bf909275-f505-11f0-9e9c-708bcdbcb7b1

# 初始化客户端
client = UboxProfilerClient(
    base_url="http://10.11.10.173:8080",
    project_id=project_id
)

# 并行获取所有数据（推荐）
data = {
    # 基础信息
    "report_info": client.report.get_report_info(case_id),
    "case_info": client.report.get_case_info(case_id),

    # 性能概况
    "cpu_performance": client.cpu.get_cpu_performance(case_id),
    "memory_info": client.memory.get_memory_info(case_id),
    "graphic_profile": client.graphic.get_graphic_profile(case_id),

    # 热点函数
    "func_data_summary": client.cpu.get_func_data_summary(case_id, "all"),
    "fun_top": client.report.get_fun_top(case_id, 0, 0),

    # 卡顿帧
    "jank_frames": client.cpu.get_jank_frame(case_id),
}

# 将数据提供给 AI Agent 生成报告
# AI 会使用完整分析提示词模板生成报告
```

---

## 相关模块

完整性能分析整合以下模块，点击查看详细文档：

### 核心分析模块
- **[性能概况分析](overview.md)** - 整体性能概览和关键指标诊断
- **[热点函数分析](hotspot-analysis.md)** - 多维度热点函数排序和分析
- **[帧率卡顿分析](jank-analysis.md)** - 掉帧分析和卡顿定位
- **[内存专项分析](memory-analysis.md)** - 内存使用和 GC 性能分析

### 模块深入分析
- **[CPU 模块分析](cpu-module.md)** - CPU 相关热点和性能问题
- **[渲染模块分析](rendering-module.md)** - 渲染相关热点和 DrawCall 分析
- **[物理模块分析](physics-module.md)** - 物理相关热点和性能问题
- **[动画模块分析](animation-module.md)** - 动画相关热点和性能问题
- **[UI 模块分析](ui-module.md)** - UI 相关热点和性能问题

### 支撑系统
- **[数据获取策略](data-strategy.md)** - API 数据获取和处理策略
- **[架构设计](architecture.md)** - 工具架构和模块设计

---

## 优势与特点

| 特性 | 说明 |
|------|------|
| **📦 一站式** | 单次请求获取所有维度的性能分析 |
| **🔍 全局视角** | 从整体到细节，全面了解游戏性能 |
| **🎯 优先级明确** | 综合各模块问题，按影响程度排序 |
| **📊 结构清晰** | 5 大章节，层次分明 |
| **💡 可操作** | 提供具体、可执行的优化方案 |
| **🔄 易维护** | 引用各模块文档，保持内容一致性 |
