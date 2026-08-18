# 热点函数分析 (Hotspot Function Analysis)

## ⚠️ 重要要求：必须获取所有维度的数据！

**AI Agent 执行热点函数分析时，必须获取全部 4 个排序维度的数据，不能只获取单一维度！**

### 为什么需要所有维度？

不同排序维度揭示不同的性能问题：

| 排序维度 | 揭示的问题 | 如果缺失会漏掉 |
|---------|-----------|--------------|
| **按自身耗时** | 函数内部代码效率低 | 内部低效但调用少的函数 |
| **按总耗时** | 整体调用链路长 | 子函数调用链长的函数 |
| **按有效帧平均耗时** ⭐ | 频繁调用且慢的关键函数 | **最关键的性能瓶颈** |
| **按高耗时帧数** | 偶发性卡顿 | 导致帧率波动的函数 |

### 数据获取要求

**必须执行 4 次命令**，获取所有维度的数据：

```bash
# 维度 1：按自身耗时
python scripts/cli.py analyze <case_id> --hotspots --top 20 --sort-by self_time --project <project_id> -o self_time.json

# 维度 2：按总耗时
python scripts/cli.py analyze <case_id> --hotspots --top 20 --sort-by total_time --project <project_id> -o total_time.json

# 维度 3：按有效帧平均耗时 ⭐ 最重要
python scripts/cli.py analyze <case_id> --hotspots --top 20 --sort-by valid_frame_avg --project <project_id> -o valid_frame_avg.json

# 维度 4：按高耗时帧数
python scripts/cli.py analyze <case_id> --hotspots --top 20 --sort-by high_cost_frames --project <project_id> -o high_cost_frames.json
```

### 报告生成要求

**生成的报告必须包含所有 4 个维度的分析**：
- ✅ Top 20 按自身耗时
- ✅ Top 20 按总耗时
- ✅ Top 20 按有效帧平均耗时（重点）
- ✅ Top 20 按高耗时帧数
- ✅ 跨维度对比分析
- ✅ 综合优化建议

---

## 模块定位

识别和排序性能热点函数，提供函数级别的性能诊断。支持多维度排行、趋势分析和调用链分析。

---

## ⚠️ 前置要求：文件管理规范

**在开始热点函数分析之前，AI Agent 必须先查阅**：[📖 文件管理规范](file-management.md)

### 为什么重要？

热点函数分析会生成**多个临时文件**（4个维度的数据）和**一份最终报告**，必须严格遵守统一的文件管理规范以确保：
- ✅ 多个临时文件正确存放和命名
- ✅ 最终报告使用规范格式（包含时间戳）
- ✅ 数据来源可追溯
- ✅ 避免文件混乱和覆盖

### 本分析类型的文件清单

**临时文件**（存放于 `.upa_temp/{case_id}/`）：
- `hotspots_self_time.json` - 热点函数（按自身耗时排序）
- `hotspots_total_time.json` - 热点函数（按总耗时排序）
- `hotspots_valid_frame_avg.json` - 热点函数（按有效帧平均耗时排序）
- `hotspots_high_cost_frames.json` - 热点函数（按高耗时帧数排序）
- `merged_hotspots.json` - 合并后的热点函数数据

**最终报告**（存放于 `reports/{case_id}/`）：
- `hotspot_{case_id}_{timestamp}.md` - 热点函数分析报告
  - 时间戳格式：`YYYYMMDD_HHMMSS`
  - 示例：`hotspot_bf909275-f505-11f0-9e9c-708bcdbcb7b1_20260127_143058.md`

### 必须遵守的规则

1. **临时文件目录**：`.upa_temp/{case_id}/`
2. **报告目录**：`reports/{case_id}/`
3. **临时文件命名**：`hotspots_{dimension}.json`（dimension: self_time/total_time/valid_frame_avg/high_cost_frames）
4. **合并文件命名**：`merged_hotspots.json`
5. **报告文件命名**：`hotspot_{case_id}_{timestamp}.md`
6. **报告生成完成后**：建议清理临时文件

**📖 详细规范请查阅**：[file-management.md](file-management.md)

---

## 分析内容

### 多维度 Top N 排行

#### 1. 按自身耗时 (Self Time)
识别函数自身执行耗时最长的函数（不包含子函数调用时间）
- **适用场景**: 定位函数内部代码性能问题

#### 2. 按总耗时 (Total Time)
识别函数及其子函数总耗时最长的函数
- **适用场景**: 定位整体耗时最长的调用链路

#### 3. 按有效帧平均耗时 (Valid Frame Avg) ⭐
识别调用次数多且耗时较高的函数（两步筛选法）
- **第一步**: 按 `valid_frame_count`（有效帧数）降序排序，筛选出前 40% 的函数
- **第二步**: 在前 40% 的函数中，按 `selftime_avg`（有效帧自身平均耗时）降序排序
- **有效帧定义**: 函数被调用的帧数（`valid_frame_count`），直接来自 API 统计
- **平均耗时说明**: `selftime_avg` 直接使用 `fun_top` API 返回值，无需计算
- **数据来源**: 使用 `fun_top` API 返回的 `valid_frame_count` 和 `selftime_avg` 字段
- **特点**: 先筛选频繁调用的函数（前 40%），再从中找出自身平均耗时最高的函数
- **适用场景**: 识别那些被频繁调用且每次调用自身都很慢的函数，优先级最高

#### 4. 按高耗时帧数 (High Cost Frames) ⭐
识别出现高耗时帧次数最多的函数
- **排序规则**: 按 `high_frame`（高耗时帧次数）降序排序
- **数据来源**: 使用 `fun_top` API 返回的 `high_frame` 和 `high_selftime` 字段
- **高耗时帧平均耗时**: 使用 `high_selftime` 字段表示高耗时帧的自身平均耗时
- **特点**: 基于 fun_top API 的统计，更准确反映高耗时帧分布
- **适用场景**: 定位导致帧率波动的罪魁祸首，识别偶发性高耗时函数

### 热点函数整体趋势

- **耗时随帧数变化**: 识别函数耗时在不同帧之间的变化模式
- **调用频率变化**: 识别函数调用频率的变化趋势
- **热点分布**: 分析热点函数在不同时间段的分布
- **性能退化识别**: 找出突然出现的高耗时函数

### 函数调用链分析

- **调用树结构**: 展示函数调用层次关系
- **父子关系**: 分析函数调用者和被调用者
- **调用深度**: 识别调用栈过深的函数

### 按模块分组

按模块对热点函数进行分类和排序，快速定位各模块的性能瓶颈。

**支持的模块分类**:
- **Rendering（渲染模块）**: Draw、Render、Camera、Light、Shadow、Shader、Mesh、Material、Texture、Graphics、GPU、Canvas、Particle 等
- **Script（脚本模块）**: Mono、Script、Behaviour、Coroutine、GameObject、Transform、Component、SceneManager 等
- **Physics（物理模块）**: Physics、Rigidbody、Collider、Joint、PhysX、Collision、Raycast 等
- **Animation（动画模块）**: Animation、Animator、State、Clip、BlendTree、Avatar、IK、StateMachine 等
- **UI（界面模块）**: EventSystem、GraphicRaycaster、Pointer、Input、Scroll、Layout、CanvasGroup、Image、Text、Button 等

**数据来源**: 使用 `get_func_data_summary` 接口获取的函数树形数据，提取每个模块的 Top N 函数（默认 Top 10，按 self_time 降序排序）

**使用方法**:
```bash
# 通过 CLI 使用（需要集成到 CLI）
python scripts/cli.py analyze <case_id> --module-hotspots

# 代码调用
analyzer.analyze_modules_by_hotspots(case_id, top_n=10, exclude_unity_entry=True)
```

**输出示例**:
```python
{
    "case_id": "xxx",
    "total_functions": 5234,
    "modules": {
        "Rendering": {
            "top_count": 10,
            "total_self_time": 1234.56,
            "total_total_time": 3456.78,
            "total_calls": 12345,
            "top_functions": [...]
        },
        ...
    }
}
```

### Unity Entry 函数过滤 ⭐ 新增

**功能说明**: 过滤 Unity 常见的上层函数入口和 Profiler 开销函数，以便更深入地分析实际问题。

**默认行为**: **默认开启过滤**（`exclude_unity_entry=True`），如需查看完整函数列表（包含 Unity Entry），请使用 `--include-unity-entry` 参数。

**过滤的函数类型**:
- **Profiler 函数**: `Profiler.FlushMemoryCounters`, `Profiler.*` 等（Unity Profiler 开销）
- **主循环入口**: `PlayerLoop`, `BehaviourUpdate`, `LateBehaviourUpdate` 等
- **渲染入口**: `ScriptableRenderer.Execute`, `ForwardRenderer` 等
- **物理入口**: `Physics.SyncTransforms`, `Physics.Update` 等
- **脚本运行入口**: `RunBehaviourUpdate`, `ScriptRunBehaviourUpdate` 等

**使用方法**:
```bash
# 默认过滤 Unity Entry 函数（推荐）
python scripts/cli.py analyze <case_id> --hotspots

# 包含 Unity Entry 函数（完整分析）
python scripts/cli.py analyze <case_id> --hotspots --include-unity-entry

# 组合使用：过滤 + 有效帧平均耗时排序
python scripts/cli.py analyze <case_id> --hotspots --sort-by valid_frame_avg
```

**适用场景**:
- ✅ 深入分析游戏自定义代码性能
- ✅ 排查具体的性能瓶颈问题
- ✅ 希望跳过 Unity 引擎层和 Profiler 开销，直接看实际耗时函数
- ❌ 分析整体性能分布（不过滤）
- ❌ 检查 Unity 引擎某子系统的性能（不过滤）

**价值**:
1. **直接定位实际问题** - 默认跳过包装函数和 Profiler 开销，直达核心
2. **节省分析时间** - 减少手动展开子函数的步骤
3. **提高分析准确性** - 关注真正需要优化的游戏代码
4. **排除分析干扰** - Profiler.FlushMemoryCounters 通常占用首位（~98秒），默认过滤后更容易看到真实问题
5. **更好的默认体验** - 大多数情况下用户关心的是游戏代码性能，默认过滤更符合需求

详细对比分析请参考: [Unity Entry 过滤功能对比报告](../../test_output_bf909275/EXCLUDE_UNITY_ENTRY_REPORT.md)

## 数据获取方式

### 命令行工具参数说明

| 参数 | 说明 |
|------|------|
| `case_id` | 案例 ID（必需） |
| `--hotspots` | 执行热点函数分析 |
| `--top N` | 返回 Top N 函数（默认：20） |
| `--sort-by` | 排序维度：self_time（默认）/ total_time / valid_frame_avg / high_cost_frames |
| `--include-unity-entry` | 包含 Unity 入口函数（默认：过滤） |
| `-o, --output` | 输出文件路径（可选） |
| `--project` | 项目 ID/APPKEY |
| `--url` | API 基础 URL |
| `--no-cache` | 禁用缓存 |
| `--verbose, -v` | 详细输出模式 |

### 数据获取流程

```
1. python scripts/cli.py analyze <case_id> --hotspots
   ↓ 调用命令行工具
2. ProfilerAnalyzer.analyze_hotspots(case_id, top_n, sort_by)
   ↓ 内部调用分析器
3. HotspotCollector.collect(case_id)
   ↓ 收集器自动调用多个 API
   ├─ cpu.get_func_data_summary()  # 函数树形数据（self_time, total_time 等）
   └─ report.get_fun_top()          # 函数 Top 排行（valid_frame_avg, high_frame 等）
4. 根据 sort_by 参数排序和筛选
   ↓ 生成 JSON 格式数据
5. 输出到文件或标准输出
   ↓ 供 AI Agent 使用生成报告
```

## 数据来源说明

热点函数分析使用**两个 API** 的数据：

### 1. get_func_data_summary（主要数据源）
- **接口**: `cpu.get_func_data_summary(case_id, "all")`
- **返回**: MinIO URL，下载后获得完整的函数树形结构
- **用途**: 提供函数的**基础性能数据**
  - `self_time`: 函数自身执行时间
  - `total_time`: 函数及其子函数总时间
  - `calls`: 调用次数
  - `avg_total_time`: 平均总耗时
  - `max_total_time`: 最大总耗时
  - 完整的函数调用层级关系

### 2. fun_top（增强数据源）
- **接口**: `report.get_fun_top(case_id, 0, 0)`
- **返回**: 函数 Top 排行数据（扁平化列表）
- **用途**: 提供**有效帧分析数据**
  - `valid_frame_count`: 有效帧数量
  - `selftime_avg`: 有效帧自身平均耗时（API 直接返回，无需计算）
  - `high_frame`: 高耗时帧数
  - `high_selftime`: 高耗时帧自身平均耗时（高耗时帧的自身耗时总和 / 高耗时帧数）
  - `totaltime_avg`: 有效帧总平均耗时
  - `totaltime_max`: 最大总耗时

**有效帧 (valid_frame_count) 说明**：
- **定义**: 函数被调用的帧数
- **统计方式**: 由 `fun_top` API 直接统计返回
- **无需阈值**: 不需要通过调用次数阈值筛选，API 已完成统计

**有效帧平均耗时 (selftime_avg) 说明**：
- **数据来源**: `fun_top` API 直接返回值
- **无需计算**: 不需要手动计算，API 已完成平均耗时统计
- **使用方式**: 直接使用 API 返回的 `selftime_avg` 字段进行排序

### 数据使用策略

| 分析维度 | 主要数据来源 | 说明 |
|----------|-------------|------|
| **按自身耗时** | get_func_data_summary | 使用 `self_time` 字段 |
| **按总耗时** | get_func_data_summary | 使用 `total_time` 字段 |
| **按有效帧平均耗时** | fun_top API | 使用 `selftime_avg` 字段（两步筛选：前 40% valid_frame_count → selftime_avg） |
| **按高耗时帧数** | fun_top API | 使用 `high_frame` 字段排序，显示 `high_selftime` 字段 |
| **按模块分组** | get_func_data_summary | 使用函数名模式匹配分类，按 `self_time` 排序 |

**注意**:
- `get_func_data_summary` 提供**完整、准确**的基础性能数据
- `fun_top API` 提供**增强的统计信息**（有效帧分析）
- 两个数据源通过**函数名称**进行关联和增强

## 大数据处理

当函数列表超过 **1000 个**时，启用预处理模式：

### 预处理流程
1. **检测大数据**: 函数数量 > 1000
2. **预处理脚本**:
   - 计算多维度 Top 100
   - 按模块分组统计
   - 提取趋势摘要
   - 选择关键样本（Top 20 详细数据）
3. **AI 分析**: 基于摘要数据进行分析

### 预处理输出
```python
{
    "total_functions": 5000,
    "top_by_self_time": [...],      # Top 100
    "top_by_total_time": [...],     # Top 100
    "top_by_calls": [...],          # Top 100
    "top_by_avg_time": [...],       # Top 100
    "top_by_variance": [...],       # Top 100
    "by_module": {                   # 按模块分组
        "Rendering": {...},
        "Script": {...},
        ...
    },
    "trend_summary": {...},          # 趋势摘要
    "key_samples": [...]             # 关键样本（Top 20）
}
```

## API 调用流程

### 正常模式（< 1000 函数）
```
1. report.get_fun_top(case_id, threshold)
   ↓ 获取 Top 函数
2. report.get_func_list(case_id)
   ↓ 获取完整函数列表
3. cpu.query_func_performance(case_id, func_name)
   ↓ 查询热点函数性能
4. cpu.query_funtime_diagram(case_id, func_name)
   ↓ 获取函数趋势数据
5. 多维度排序和分组
   ↓ 生成热点报告
```

### 大数据模式（≥ 1000 函数）
```
1. report.get_fun_top(case_id)
   ↓ 获取 Top 函数
2. 检测数据量
   ↓ 函数数 ≥ 1000
3. 启动预处理脚本
   ↓
4. 多维度 Top N 计算
   ↓
5. 按模块分组统计
   ↓
6. 提取趋势摘要
   ↓
7. 选择关键样本
   ↓
8. AI 分析摘要数据
   ↓ 生成热点报告
```

## 排序指标说明

| 指标 | 参数值 | 数据来源 | 公式 | 说明 |
|------|--------|----------|------|------|
| Self Time | `self_time` | get_func_data_summary | 函数自身执行时间 | 不包含子函数调用时间 |
| Total Time | `total_time` | get_func_data_summary | Self Time + 子函数时间 | 函数总耗时 |
| Valid Frame Avg | `valid_frame_avg` | fun_top API | 两步筛选：前 40% valid_frame_count → selftime_avg | 找出频繁调用且自身耗时高的函数 ⭐ |
| High Cost Frames | `high_cost_frames` | fun_top API | 按 `high_frame`（高耗时帧次数）排序，显示 `high_selftime` | 定位卡顿元凶 ⭐ |

**有效帧平均耗时 (Valid Frame Avg) 两步筛选说明**：
- **第一步**: 按 `valid_frame_count`（有效帧数）降序排序，取前 40% 的函数
  - 目的：筛选出频繁调用的函数
- **第二步**: 在前 40% 函数中，按 `selftime_avg`（有效帧自身平均耗时）降序排序
  - 目的：找出频繁调用函数中自身平均耗时最高的
- **数据来源**: `selftime_avg` 直接使用 `fun_top` API 返回值，无需计算
- **有效帧定义**: `valid_frame_count` = 函数被调用的帧数，由 API 直接统计

## 输出报告模板

```markdown
## 🔥 热点函数分析

### 多维度 Top N 排行

#### Top 20 按自身耗时 (Self Time)

| 排名 | 函数名 | 模块 | Self Time (ms) | Total Time (ms) | 调用次数 |
|------|--------|------|----------------|-----------------|----------|
| 1 | {func_name} | {module} | {self_time} | {total_time} | {calls} |
| 2 | ... | ... | ... | ... | ... |

#### Top 20 按总耗时 (Total Time)

| 排名 | 函数名 | 模块 | Total Time (ms) | Self Time (ms) | 调用次数 |
|------|--------|------|-----------------|----------------|----------|
| 1 | {func_name} | {module} | {total_time} | {self_time} | {calls} |
| 2 | ... | ... | ... | ... | ... |

#### Top 20 按有效帧平均耗时 (Valid Frame Avg)

| 排名 | 函数名 | 有效帧数 | 自身平均耗时 (ms) | 最大耗时 (ms) | 高耗时帧数 |
|------|--------|----------|------------------|---------------|------------|
| 1 | {func_name} | {valid_frame_count} | {selftime_avg} | {totaltime_max} | {high_frame} |
| 2 | ... | ... | ... | ... | ... |

#### Top 20 按高耗时帧数 (High Cost Frames)

| 排名 | 函数名 | 高耗时帧数 | 高耗时帧均 (ms) | 总调用次数 |
|------|--------|------------|----------------|------------|
| 1 | {func_name} | {high_frame} | {high_selftime} | {calls} |
| 2 | ... | ... | ... | ... |

### 热点函数趋势分析

#### 函数耗时变化趋势
{trend_analysis}
- 耗时随帧数变化的函数：{list}
- 突然出现的高耗时函数：{list}
- 耗时逐渐增加的函数：{list}

#### 热点函数分布统计
- 渲染模块热点占比：{pct}%
- 脚本模块热点占比：{pct}%
- 物理模块热点占比：{pct}%
- 动画模块热点占比：{pct}%

### 各模块 Top 10

#### 渲染模块热点
| 排名 | 函数名 | Self Time | Total Time | 调用次数 |
|------|--------|-----------|------------|----------|
| 1 | {func} | {st} | {tt} | {calls} |
| ... | ... | ... | ... | ... |

#### 物理模块热点
{physics_top10}

#### 脚本模块热点
{script_top10}

#### 动画模块热点
{animation_top10}

### 函数调用链分析

#### 调用深度 Top 10
| 排名 | 函数名 | 调用深度 | 总耗时 | 父函数数 |
|------|--------|----------|--------|----------|
| 1 | {func} | {depth} | {time} | {parents} |
| ... | ... | ... | ... | ... |

#### 热点调用路径
{call_path_analysis}
```

## 使用示例

### 命令行工具完整示例

**基本用法**：

```bash
# 查看所有可用的排序维度选项
python scripts/cli.py analyze <case_id> --hotspots --help

# 获取 Top 20 按自身耗时（默认）
python scripts/cli.py analyze <case_id> --hotspots --top 20
```

**完整示例（带 project_id 和输出文件）**：

```bash
# 分析热点函数并保存到文件
python scripts/cli.py analyze bf909275-f505-11f0-9e9c-708bcdbcb7b1 \
  --hotspots \
  --top 20 \
  --sort-by self_time \
  --project pcavpt6w \
  -o hotspots.json
```

**获取所有 4 个维度的数据（AI Agent 使用）**：

```bash
# 维度 1：按自身耗时
python scripts/cli.py analyze bf909275-f505-11f0-9e9c-708bcdbcb7b1 \
  --hotspots --top 20 --sort-by self_time --project pcavpt6w \
  -o self_time.json

# 维度 2：按总耗时
python scripts/cli.py analyze bf909275-f505-11f0-9e9c-708bcdbcb7b1 \
  --hotspots --top 20 --sort-by total_time --project pcavpt6w \
  -o total_time.json

# 维度 3：按有效帧平均耗时
python scripts/cli.py analyze bf909275-f505-11f0-9e9c-708bcdbcb7b1 \
  --hotspots --top 20 --sort-by valid_frame_avg --project pcavpt6w \
  -o valid_frame_avg.json

# 维度 4：按高耗时帧数
python scripts/cli.py analyze bf909275-f505-11f0-9e9c-708bcdbcb7b1 \
  --hotspots --top 20 --sort-by high_cost_frames --project pcavpt6w \
  -o high_cost_frames.json
```

**更多排序维度选项**：

```bash
# 按调用次数排序
python scripts/cli.py analyze <case_id> --hotspots --sort-by calls

# 按影响分数排序
python scripts/cli.py analyze <case_id> --hotspots --sort-by impact_score

# 按平均耗时排序
python scripts/cli.py analyze <case_id> --hotspots --sort-by avg_time

# 按高耗时帧占比排序
python scripts/cli.py analyze <case_id> --hotspots --sort-by high_cost_frame_ratio
```

**Unity Entry 过滤**：

```bash
# 包含 Unity Entry 函数（完整分析）
python scripts/cli.py analyze <case_id> --hotspots --include-unity-entry

# 有效帧平均耗时 + 包含 Unity Entry
python scripts/cli.py analyze <case_id> --hotspots \
  --sort-by valid_frame_avg --include-unity-entry
```

### Python 代码调用（可选）

如果需要在 Python 代码中直接调用：

```python
from scripts.analyzer import ProfilerAnalyzer

analyzer = ProfilerAnalyzer(
    base_url="http://10.11.10.173:8080",
    project_id="your_project_id"
)

# 分析热点（按自身耗时）
result = analyzer.analyze_hotspots(
    case_id="uuid",
    top_n=20,
    sort_by="self_time",
    exclude_unity_entry=True,
    use_cache=True
)

# result 包含以下字段：
# - total_functions: 总函数数量
# - is_big_data: 是否为大数据
# - top_functions: Top N 函数列表
# - summary: 统计摘要
```

## AI 报告生成提示词

⚠️ **重要：AI Agent 必须基于所有 4 个维度的数据生成综合报告！**

当用户请求热点函数分析时：

1. **首先获取所有 4 个维度的数据**（见上方数据获取要求）
2. **合并所有维度的数据**
3. **使用以下提示词模板生成综合报告**：

```
请基于以下 Unity Profiler 热点函数数据（包含 4 个排序维度），生成一份专业的 Markdown 格式热点函数分析报告。

**数据内容**（包含所有 4 个维度）：
- self_time 维度数据：{self_time_json}
- total_time 维度数据：{total_time_json}
- valid_frame_avg 维度数据：{valid_frame_avg_json}
- high_cost_frames 维度数据：{high_cost_frames_json}

**报告要求**：

1. **报告标题**：# 🔥 热点函数分析报告（全维度）

2. **分析摘要章节**（## 📋 分析摘要）
   - 总函数数量
   - 说明包含全部 4 个排序维度
   - 是否过滤 Unity Entry 函数

3. **四个维度的 Top 热点函数章节**（## 🎯 Top 热点函数（全维度））

   ### 3.1 Top 20 按自身耗时 (Self Time)
   - 表格展示，包含排名、函数名、模块、Self Time、Total Time、调用次数

   ### 3.2 Top 20 按总耗时 (Total Time)
   - 表格展示，包含排名、函数名、模块、Total Time、Self Time、调用次数

   ### 3.3 Top 20 按有效帧平均耗时 (Valid Frame Avg) ⭐
   - 表格展示，包含排名、函数名、有效帧数、平均耗时、最大耗时
   - **重点分析这个维度**，因为它揭示最关键的性能瓶颈

   ### 3.4 Top 20 按高耗时帧数 (High Cost Frames)
   - 表格展示，包含排名、函数名、高耗时帧数、高耗时帧均、总调用次数

4. **跨维度对比分析章节**（## 🔍 跨维度对比分析）
   - 识别在多个维度中都出现的函数（这些是最需要优化的）
   - 分析各维度 Top 10 的重叠情况
   - 标注出游戏代码函数（非 Unity 引擎函数）

5. **问题函数分析章节**（## ⚠️ 核心问题函数）
   - 基于所有维度，识别出最需要优化的前 5-10 个函数
   - 分析可能的性能瓶颈原因
   - 标注严重程度和优化优先级

6. **优化建议章节**（## 💡 优化建议）
   - 针对核心问题函数给出具体优化建议
   - 按模块分组建议（Rendering/Script/Physics 等）
   - 包含优化优先级（短期/中期/长期）

**输出格式**：纯 Markdown 格式，使用表格展示函数数据，代码格式展示函数名。

**重要提醒**：
- 必须包含所有 4 个维度的分析，不能遗漏任何一个
- valid_frame_avg 维度最重要，需要重点分析
- 跨维度对比是关键，找出在多个维度都出现的问题函数
```

### 单一维度的简化报告（不推荐）

**⚠️ 警告**：只有在用户明确要求只分析某个维度时，才使用简化提示词：

```
请基于以下 Unity Profiler 热点函数数据，生成一份专业的 Markdown 格式热点函数分析报告。

⚠️ **注意**：此报告仅包含 {sort_by} 维度的分析，不包含其他维度。建议获取所有 4 个维度的数据以获得完整的性能分析。

**数据内容**：
{hotspot_data_json}

**分析维度**：{sort_by}

...（其余部分同上）
```

## 相关模块

- [性能概况分析](overview.md) - 整体性能概览
- [CPU 模块分析](cpu-module.md) - CPU 相关热点
- [渲染模块分析](rendering-module.md) - 渲染相关热点
- [物理模块分析](physics-module.md) - 物理相关热点
