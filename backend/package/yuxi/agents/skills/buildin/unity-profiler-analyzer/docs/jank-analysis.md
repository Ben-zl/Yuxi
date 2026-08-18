# 帧率卡顿分析 (Frame Rate & Jank Analysis)

## 模块定位

分析帧率波动和卡顿情况，通过 `cpu.get_jank_frame()` API 直接获取卡顿帧列表，并对每个卡顿帧进行函数级别的详细分析。这种分析方式可以逐帧定位导致卡顿的具体函数调用，找出卡顿的根本原因。

**新增功能**：支持在卡顿帧分析中包含截图 URL，帮助视觉化分析卡顿发生时的游戏画面。

---

## ⚠️ 前置要求：文件管理规范

**在开始卡顿分析之前，AI Agent 必须先查阅**：[📖 文件管理规范](file-management.md)

### 为什么重要？

卡顿分析会生成**临时文件**和**最终报告**，必须严格遵守统一的文件管理规范以确保：
- ✅ 临时文件正确存放和命名
- ✅ 最终报告使用规范格式（包含时间戳）
- ✅ 数据来源可追溯
- ✅ 避免文件混乱和覆盖

### 本分析类型的文件清单

**临时文件**（存放于 `.upa_temp/{case_id}/`）：
- `jank_ai_ready.json` - 卡顿帧分析数据（AI-Ready 模式）

**最终报告**（存放于 `reports/{case_id}/`）：
- `jank_{case_id}_{timestamp}.md` - 卡顿分析报告
  - 时间戳格式：`YYYYMMDD_HHMMSS`
  - 示例：`jank_bf909275-f505-11f0-9e9c-708bcdbcb7b1_20260127_143102.md`

### 必须遵守的规则

1. **临时文件目录**：`.upa_temp/{case_id}/`
2. **报告目录**：`reports/{case_id}/`
3. **临时文件命名**：`jank_ai_ready.json`
4. **报告文件命名**：`jank_{case_id}_{timestamp}.md`
5. **报告生成完成后**：建议清理临时文件

**📖 详细规范请查阅**：[file-management.md](file-management.md)

---

## 工作流模式

Python 预处理 + AI 生成报告（**默认行为**）：

```
CLI 收集数据 → Python 统计分析 → 输出增强 JSON → AI 生成报告
```

**AI-Ready 模式优势**：
- ✅ **模式识别**：自动检测周期性卡顿、突发卡顿、持续卡顿
- ✅ **严重程度分级**：自动分类轻微/中等/严重/极端卡顿
- ✅ **函数聚类**：识别高频函数和高影响函数
- ✅ **自动推荐**：基于分析结果生成优化建议
- ✅ **结构化表格**：预生成 Markdown 表格数据，减少 AI 处理时间


## 分析内容

### 卡顿帧识别

使用 `cpu.get_jank_frame()` API 直接获取卡顿帧列表：

**数据来源**: `cpu.get_jank_frame(case_id)`

**返回数据**:
```python
{
    "code": 0,
    "data": {
        "jank_frames": [         # 普通卡顿帧列表（帧时间 > 33ms）
            123, 456, 789, ...
        ],
        "big_jank_frames": [      # 严重卡顿帧列表（帧时间 > 100ms）
            234, 567, ...
        ],
        "total_frames": 10000,    # 总帧数
        "jank_count": 150         # 卡顿帧数量
    }
}
```

**数据合并逻辑**：
- 系统会同时获取 `jank_frames` 和 `big_jank_frames` 两个数组
- 将两个数组合并并去重（保留原有顺序）
- 合并后的帧列表用于详细分析
- 这样可以确保同时捕获普通卡顿和严重卡顿帧

### 卡顿帧函数开销分析

对每个卡顿帧，获取其函数调用详情，定位高耗时函数：

**数据来源**: `cpu.get_frame_performance(case_id, frame_id)`

**返回数据**:
```python
{
    "code": 0,
    "data": {
        "frame": 123,
        "functions": [  # 该帧的函数列表（按耗时降序）
            {
                "name": "Render.Draw",
                "self_time": 12.5,      # 函数自身耗时（毫秒）
                "total_time": 18.3,     # 总耗时（含子函数）
                "calls": 1              # 调用次数
            },
            ...
        ]
    }
}
```

### Top N 卡顿帧分析

分析耗时最严重的 Top N 卡顿帧：

1. **按帧时间排序**: 找出耗时最长的卡顿帧
2. **逐帧分析**: 对每个卡顿帧，获取其函数调用列表
3. **定位问题函数**: 找出每个卡顿帧中耗时最高的函数
4. **统计分析**: 识别在多个卡顿帧中重复出现的高耗时函数

### 卡顿原因分类

根据函数名模式识别卡顿原因：

| 原因类型 | 特征 | 示例函数 |
|----------|------|----------|
| 渲染过载 | DrawCall 突增 | Render.Draw, Camera.Render |
| GC 触发 | GC 耗时突增 | GC.Collect, GC.Alloc |
| 资源加载 | 同步加载阻塞 | Resources.Load, AssetBundle.Load |
| 物理模拟 | 物理计算突增 | Physics.Simulate, Physics.SyncTransforms |
| 脚本执行 | 复杂逻辑触发 | MonoBehaviour.Update, Coroutine |
| 动画计算 | 动画相关 | Animator.Update, Animation.Evaluate |

### 卡顿模式分析

- **周期性卡顿**: 识别固定间隔出现的卡顿
- **突发性卡顿**: 识别偶发性的高耗时卡顿
- **持续卡顿**: 识别连续多帧的卡顿

## 数据获取方式

### 使用命令行工具

使用 `--jank` 选项，默认启用 Python 预处理：

```bash
# 基本卡顿分析（默认 AI-Ready 模式，Top 20 卡顿帧）
python scripts/cli.py analyze <case_id> --jank

# Top 30 卡顿帧 + 模式识别 + 自动推荐
python scripts/cli.py analyze <case_id> --jank --top 30

# 卡顿分析 + 截图 URL（包含每个卡顿帧的截图链接）
python scripts/cli.py analyze <case_id> --jank --with-screenshots

# 卡顿分析 + 基于时间戳的截图匹配（更精确）
python scripts/cli.py analyze <case_id> --jank --with-screenshots --use-timestamp-screenshots

# 卡顿分析 + 基于时间戳的截图匹配 + 自定义最大偏移（20秒）
python scripts/cli.py analyze <case_id> --jank --with-screenshots --use-timestamp-screenshots --max-screenshot-offset 20

# 保存增强数据到文件
python scripts/cli.py analyze <case_id> --jank -o jank_enhanced.json

# 完整示例
python scripts/cli.py analyze bf909275-f505-11f0-9e9c-708bcdbcb7b1 \
  --jank \
  --top 30 \
  --with-screenshots \
  --project pcavpt6w \
  -o jank_ai_ready.json
```

**截图 URL 格式**：
- 基础 URL：`https://minio-cluster.testplus.cn/{project_id}/{case_id}/screenshot/{timestamp}_{frame}.jpg`
- 需要提供 `--project` 参数指定项目 ID
- 使用 `--with-screenshots` 选项启用截图功能

**截图匹配方式**：

1. **帧号匹配**（默认）：使用 `--with-screenshots`
   - 通过截图 URL 中的帧号匹配最接近的卡顿帧
   - 匹配范围：200 帧内
   - 适用场景：快速截图查看

2. **时间戳匹配**（推荐）：使用 `--with-screenshots --use-timestamp-screenshots`
   - 从 `frameTime.frames` 数据计算每帧的时间戳
   - 使用 `get_original_files()` API 获取原始 profiler 数据作为校准点
   - 每 300 帧使用原始文件数据进行校准，确保时间戳准确性
   - 通过计算的时间戳匹配截图（忽略截图文件名中的帧号）
   - 匹配范围：默认 10 秒内（可通过 `--max-screenshot-offset` 自定义）
   - 适用场景：需要精确截图对应关系

**时间戳匹配原理**：
```
1. 获取原始 profiler 数据文件列表（格式：{timestamp}_{frame}.raw.zip）
   - 这些文件每隔约 300 帧生成一次
   - 作为时间戳校准点使用

2. 获取 frameTime.frames 数据（每帧耗时，单位：毫秒）

3. 计算所有帧的时间戳：
   - 起始时间：第一个原始文件的时间戳
   - 累加方式：从前一帧时间戳 + 当前帧耗时
   - 校准机制：每 300 帧使用原始文件时间戳进行校准

4. 匹配截图：
   - 计算目标帧的时间戳
   - 查找时间戳最接近的截图（默认 10 秒范围内）
   - 返回截图 URL
```

### --jank 数据结构

```json
{
  "jank_frames": [
    {
      "frame": 123,
      "frame_time": 45.2,
      "target_fps": 60
    }
  ],
  "top_jank_frames": [
    {
      "frame": 123,
      "frame_time": 45.2,
      "target_fps": 60,
      "functions": [
        {
          "name": "Render.Draw",
          "self_time": 12.5,
          "total_time": 18.3,
          "calls": 1
        }
      ],
      "top_function": {
        "name": "Render.Draw",
        "self_time": 12.5
      },
      "jank_cause": "Rendering",
      "screenshot_url": "https://minio-cluster.testplus.cn/{project_id}/{case_id}/screenshot/{timestamp}_{frame}.jpg"
    }
  ],
  "summary": {
    "jank_rate": 1.5,
    "avg_frame_time": 38.5,
    "max_frame_time": 65.2,
    "min_frame_time": 16.8
  },
  "total_frames": 10000,
  "jank_count": 150,
  "case_id": "bf909275-f505-11f0-9e9c-708bcdbcb7b1",
  "project_id": "pcavpt6w"
}
```

### 数据获取流程

```
1. python scripts/cli.py analyze <case_id> --jank --with-screenshots
   ↓ 调用命令行工具
2. ProfilerAnalyzer.analyze_janks_with_ai_ready(case_id, top_n=20, with_screenshots=True)
   ↓ 内部调用分析器
3. JankCollector.collect_with_screenshots(case_id)
   ↓ 收集器调用 API
   ├─ cpu.get_jank_frame()  # 获取卡顿帧列表
   ├─ cpu.get_frame_performance(frame_id)  # 获取每帧的函数详情（循环调用）
   └─ cpu.get_screen_list(case_id)  # 获取截图列表（如果启用 --with-screenshots）
4. 按 frame_time 降序排序获取 Top N 卡顿帧
   ↓ 每帧包含完整的函数调用详情
5. 对每帧进行卡顿原因分类
   ↓ 基于顶层耗时函数识别卡顿原因
6. 为每个卡顿帧构建截图 URL
   ↓ 格式: https://minio-cluster.testplus.cn/{project_id}/{case_id}/screenshot/{timestamp}_{frame}.jpg
7. 输出到文件或标准输出
   ↓ 供 AI Agent 使用生成卡顿分析报告（包含截图）
```

## 输出报告模板

```markdown
## 📉 帧率卡顿分析

### 卡顿帧概览

- **总帧数**: {total_frames}
- **卡顿帧数**: {jank_count}
- **卡顿率**: {jank_rate}%

### Top 20 卡顿帧详情

| 帧号 | 帧时间 (ms) | 目标帧率 | Top 1 耗时函数 | 耗时 (ms) | 模块 |
|------|-------------|----------|----------------|-----------|------|
| 123 | 45.2 | 60 FPS | Render.Draw | 12.5 | Rendering |
| 456 | 38.7 | 60 FPS | Physics.Simulate | 15.3 | Physics |
| ... | ... | ... | ... | ... | ... |

### Top 20 卡顿帧详细函数分析

#### 帧 #123 (45.2ms)

**[🖼️ 查看大图](screenshot_url)**

![帧 #123 截图](screenshot_url)

| 排名 | 函数名 | 自身耗时 (ms) | 总耗时 (ms) | 调用次数 | 模块 |
|------|--------|---------------|-------------|----------|------|
| 1 | Render.Draw | 12.5 | 18.3 | 1 | Rendering |
| 2 | Camera.Render | 8.2 | 12.5 | 1 | Rendering |
| 3 | Shader.Parse | 5.1 | 5.1 | 3 | Rendering |
| ... | ... | ... | ... | ... | ... |

**分析**: 该帧主要耗时在渲染模块，Render.Draw 函数耗时最高。

#### 帧 #456 (38.7ms)

**[🖼️ 查看大图](screenshot_url)**

![帧 #456 截图](screenshot_url)

| 排名 | 函数名 | 自身耗时 (ms) | 总耗时 (ms) | 调用次数 | 模块 |
|------|--------|---------------|-------------|----------|------|
| 1 | Physics.Simulate | 15.3 | 20.1 | 1 | Physics |
| 2 | Rigidbody.MovePosition | 8.7 | 8.7 | 15 | Physics |
| ... | ... | ... | ... | ... | ... |

**分析**: 该帧主要耗时在物理模拟，Physics.Simulate 函数耗时最高。

### 重复出现的高耗时函数

以下函数在多个卡顿帧中重复出现：

| 函数名 | 出现次数 | 平均耗时 (ms) | 最大耗时 (ms) | 模块 |
|--------|----------|---------------|---------------|------|
| Render.Draw | 8 | 11.2 | 15.3 | Rendering |
| Physics.Simulate | 5 | 14.8 | 18.2 | Physics |
| GC.Collect | 3 | 8.5 | 12.1 | Script |

### 卡顿原因分类统计

| 原因类型 | 卡顿帧数 | 占比 | 典型帧号 |
|----------|----------|------|----------|
| 渲染过载 | 8 | 40% | 123, 234, 345, ... |
| 物理模拟 | 5 | 25% | 456, 567, ... |
| GC 触发 | 3 | 15% | 789, 890, ... |
| 脚本执行 | 2 | 10% | 111, 222 |
| 其他 | 2 | 10% | 333, 444 |

### 卡顿模式分析

#### 周期性卡顿
- **周期**: 每 60 帧（约 1 秒）
- **发生帧**: 60, 120, 180, 240, ...
- **共同特征**: Physics.Simulate 耗时突增
- **可能原因**: 物理帧率不匹配，考虑调整 Fixed Timestep

#### 突发性卡顿
- **触发帧**: 123, 789, 1357
- **共同特征**: GC.Collect 触发
- **可能原因**: 内存分配频繁，触发垃圾回收

### 优化建议

1. **渲染优化** (8/20 卡顿帧)
   - 减少 DrawCall 数量
   - 优化 Shader 复杂度
   - 使用 GPU Instancing
   - 考虑使用 LOD 系统

2. **物理优化** (5/20 卡顿帧)
   - 调整 Fixed Timestep 设置
   - 减少物理检测数量
   - 使用简化的碰撞体
   - 考虑物理对象的层级管理

3. **GC 优化** (3/20 卡顿帧)
   - 减少运行时内存分配
   - 使用对象池管理频繁创建销毁的对象
   - 避免在 Update 中分配内存
   - 预分配常用数据结构
```

## 使用示例

### 命令行工具使用

```bash
# 基本卡顿分析（Top 20 卡顿帧）
python scripts/cli.py analyze <case_id> --jank

# Top 30 卡顿帧
python scripts/cli.py analyze <case_id> --jank --top 30

# 卡顿分析 + 截图 URL
python scripts/cli.py analyze <case_id> --jank --with-screenshots

# 保存到文件
python scripts/cli.py analyze <case_id> --jank -o jank_data.json

# 完整示例：获取卡顿数据并用 AI 生成报告
python scripts/cli.py analyze bf909275-f505-11f0-9e9c-708bcdbcb7b1 \
  --jank \
  --top 30 \
  --with-screenshots \
  --project pcavpt6w \
  -o jank.json
```


## 相关模块

- **[性能概况分析](overview.md)** - 整体帧率统计和 FPS 概览
- **[热点函数分析](hotspot-analysis.md)** - 全局热点函数分析
- **[CPU 模块分析](cpu-module.md)** - CPU 相关卡顿深入分析
- **[渲染模块分析](rendering-module.md)** - 渲染相关卡顿优化建议
- **[物理模块分析](physics-module.md)** - 物理相关卡顿优化建议

---

## AI 报告生成提示词

当用户请求卡顿帧分析时，使用以下提示词模板生成报告：

```
请基于以下 Unity Profiler 卡顿帧数据，生成一份专业的 Markdown 格式卡顿帧分析报告。

**数据内容**：
{jank_data_json}

**报告要求**：

1. **报告标题**：# 📉 卡顿帧分析报告

2. **卡顿概览章节**（## 📊 卡顿概览）
   - 总帧数
   - 卡顿帧数量（帧时间 >33ms 的帧）
   - 卡顿率（卡顿帧/总帧数）
   - 最卡顿帧的帧时间

3. **卡顿帧详情章节**（## 🔍 卡顿帧详情）
   - Top 20-50 卡顿帧列表
   - 每帧包含：帧序号、帧时间、主要耗时函数
   - **截图显示（默认）**：如果数据中包含 screenshot_url，为每个卡顿帧添加截图图片
     - 格式：`### 帧 #123 (45.2ms)\n\n**[🖼️ 查看大图](screenshot_url)**\n\n![帧 #123 截图](screenshot_url)\n\n| 排名 | ...`
     - 在详细函数分析表格前添加截图图片（使用 Markdown 图片语法）
     - 图片上方添加"查看大图"链接，方便在新标签页打开原图
     - 每个截图占据独立的章节，便于阅读

4. **卡顿原因分析章节**（## 🎯 卡顿原因分析）
   - 统计导致卡顿的主要函数
   - 按模块分组分析
   - 识别频繁出现的卡顿模式

5. **优化建议章节**（## 💡 优化建议）
   - 针对卡顿原因给出具体优化方案
   - 按影响程度排序

**输出格式**：纯 Markdown 格式，使用表格和列表展示分析结果。

**截图处理说明**：
- **默认行为**：如果 screenshot_url 存在且不为空/null，必须显示截图图片
- **图片格式**：使用 Markdown 图片语法 `![帧 #XXX 截图](screenshot_url)`
- **查看大图链接**：在图片上方添加可点击的链接 `[🖼️ 查看大图](screenshot_url)`
- **如果截图 URL 为空/null**：跳过截图显示，不显示任何占位符
- **截图匹配逻辑**：系统会查找最接近目标帧的截图（200 帧范围内）
  - 如果存在精确匹配的截图（帧号相同），使用精确匹配
  - 如果不存在精确匹配，使用最接近的截图，并在 URL 中添加 `#frame_offset=N` 标识偏移量
```
