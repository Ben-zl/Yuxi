# auto-platform-query Skill 增强说明

## 修改目标

强化 auto-platform-query skill 的描述，**强调获取任务详情或性能对比时，不管任务是何种状态都需要获取和展示相关的数据**。

## 修改内容

### 1. SKILL.md 顶部 description (第3-19行)

**修改前：**
```yaml
description: |
  查询和分析自动化测试平台数据。用于查询流水线、任务、设备、用例等信息，
  获取任务详情和性能数据（FPS、JANK、内存），以及进行性能对比分析。
  ⚡ 支持查询所有状态的任务（RUNNING、QUEUE、CANCEL、FAILED、SUCCESS）

  Use when Claude needs to:
  - Query automation testing platform data (pipelines, tasks, devices, cases)
  - Get task details with performance metrics (FPS, JANK, memory) - supports all task statuses
  - Compare performance across multiple builds to detect regressions
```

**修改后：**
```yaml
description: |
  查询和分析自动化测试平台数据。用于查询流水线、任务、设备、用例等信息，
  获取任务详情和性能数据（FPS、JANK、内存），以及进行性能对比分析。
  🔥 **核心特性**：获取任务详情和性能对比时，**不管任务状态如何（RUNNING/QUEUE/CANCEL/FAILED/SUCCESS）**
  都必须获取和展示所有相关数据，不得因任务状态而过滤或排除任何数据。

  Use when Claude needs to:
  - Query automation testing platform data (pipelines, tasks, devices, cases)
  - Get task details with performance metrics (FPS, JANK, memory) - **MUST retrieve data for ALL task statuses**
  - Compare performance across multiple builds to detect regressions - **include ALL tasks regardless of status**
  - Monitor platform status with dashboard
  - **CRITICAL**: When analyzing task details or performance, NEVER filter out tasks based on their status (RUNNING/QUEUE/CANCEL/FAILED/SUCCESS). Always retrieve and display ALL available data.
```

**改进点：**
- ✅ 使用 🔥 强调核心特性
- ✅ 明确说明"不管任务状态如何都必须获取和展示所有相关数据"
- ✅ 添加 CRITICAL 警告
- ✅ 使用 **MUST**、**NEVER** 等强调词

---

### 2. SKILL.md 核心原则说明 (第25-29行)

**修改前：**
```markdown
# Auto Platform Query

自动化测试平台数据查询工具 - 查询流水线、任务详情和性能数据。

⚡ **支持所有状态的任务**：无论任务是 RUNNING、QUEUE、CANCEL、FAILED 还是 SUCCESS，都可以获取任务详情。
```

**修改后：**
```markdown
# Auto Platform Query

自动化测试平台数据查询工具 - 查询流水线、任务详情和性能数据。

🔥 **核心原则**：获取任务详情或进行性能对比时，**必须获取和展示所有状态的任务数据**，不得因任务状态（RUNNING/QUEUE/CANCEL/FAILED/SUCCESS）而过滤、排除或隐藏任何数据。
```

**改进点：**
- ✅ 使用 "核心原则" 替代 "支持所有状态的任务"
- ✅ 明确说明"必须"和"不得"的行为
- ✅ 列出所有5种状态，强调全覆盖

---

### 3. SKILL.md 任务状态支持说明 (第104-153行)

**修改前：**
```markdown
### ⚡ 任务状态支持说明

**重要特性**：auto-platform-query skill **完全支持**获取所有状态的任务数据，不会因任务状态而过滤数据。

| 任务状态 | 说明 | 支持情况 |
|---------|------|----------|
| **RUNNING** | 运行中 | ✅ 完全支持 |
| **QUEUE** | 排队中 | ✅ 完全支持 |
| **CANCEL** | 已取消 | ✅ 完全支持 |
| **FAILED** | 失败 | ✅ 完全支持 |
| **SUCCESS** | 成功 | ✅ 完全支持 |

**使用场景**：
- ✅ 监控正在执行的任务（RUNNING）
- ✅ 查看排队等待的任务（QUEUE）
- ✅ 分析被中断的任务（CANCEL）
- ✅ 调试失败任务（FAILED）
- ✅ 查看成功任务的性能数据（SUCCESS）
```

**修改后：**
```markdown
### ⚡ 任务状态支持说明

🔥 **核心原则（必须遵守）**：

**获取任务详情或进行性能对比时，不管任务状态如何，都必须获取和展示所有相关数据！**

- ✅ **RUNNING（运行中）** → 必须获取和展示
- ✅ **QUEUE（排队中）** → 必须获取和展示
- ✅ **CANCEL（已取消）** → 必须获取和展示
- ✅ **FAILED（失败）** → 必须获取和展示
- ✅ **SUCCESS（成功）** → 必须获取和展示

**严禁行为**：
- ❌ **禁止**因任务状态而过滤或排除任何数据
- ❌ **禁止**只显示成功任务而忽略失败/取消任务
- ❌ **禁止**认为失败/取消任务的数据不重要而跳过
- ❌ **禁止**在性能对比时排除任何状态的任务

**原因说明**：
- 失败/取消的任务可能包含重要的调试信息（为什么失败、何时失败）
- 运行中的任务可以显示当前性能趋势
- 排队中的任务可以了解系统负载情况
- 完整的数据才能准确分析问题根因

| 任务状态 | 说明 | 必须包含 |
|---------|------|----------|
| **RUNNING** | 运行中 | 🔥 **必须包含** |
| **QUEUE** | 排队中 | 🔥 **必须包含** |
| **CANCEL** | 已取消 | 🔥 **必须包含** |
| **FAILED** | 失败 | 🔥 **必须包含** |
| **SUCCESS** | 成功 | 🔥 **必须包含** |

**使用场景**（所有场景都必须获取所有状态）：
- 🔥 监控正在执行的任务（RUNNING）
- 🔥 查看排队等待的任务（QUEUE）
- 🔥 分析被中断的任务（CANCEL）
- 🔥 调试失败任务（FAILED）
- 🔥 查看成功任务的性能数据（SUCCESS）
```

**改进点：**
- ✅ 新增"核心原则（必须遵守）"章节
- ✅ 新增"严禁行为"章节，明确禁止操作
- ✅ 新增"原因说明"，解释为什么需要所有状态的数据
- ✅ 表格从"支持情况"改为"必须包含"
- ✅ 使用 🔥 强调所有条目

---

### 4. SKILL.md 普通任务分析功能说明 (第170-176行)

**修改前：**
```markdown
**功能说明**：
- 查找流水线并获取流水线 ID
- 获取任务的执行列表和状态
- 查看任务的详细执行情况（含设备执行情况和性能数据）
- ⚡ **支持所有状态的任务**：无论任务是 RUNNING、QUEUE、CANCEL、FAILED 还是 SUCCESS，都可以获取任务详情
- 分析 FPS、JANK、内存峰值等性能指标
- 🔥 **崩溃检测**：自动检测 Crasheye 崩溃记录并提示用户分析
```

**修改后：**
```markdown
**功能说明**：
- 查找流水线并获取流水线 ID
- 获取任务的执行列表和状态（**必须包含所有状态的任务**）
- 查看任务的详细执行情况（含设备执行情况和性能数据）
- 🔥 **核心原则**：不管任务是 RUNNING、QUEUE、CANCEL、FAILED 还是 SUCCESS，**必须获取和展示所有相关数据**
- 分析 FPS、JANK、内存峰值等性能指标（**所有状态的任务都要分析**）
- 🔥 **崩溃检测**：自动检测 Crasheye 崩溃记录并提示用户分析
```

**改进点：**
- ✅ 在每个功能点中强调"必须包含所有状态"
- ✅ 使用 🔥 标记核心原则

---

### 5. SKILL.md 性能对比分析功能说明 (第194-221行)

**修改前：**
```markdown
### 2. 性能对比分析

📖 **完整指南**: [references/PERFORMANCE_COMPARISON.md](references/PERFORMANCE_COMPARISON.md)

**功能说明**：
- **基础对比**：对比最近 2 份数据，快速检测性能回归
- **深度对比**：对比 3-5 份数据，分析长期性能趋势
- 🆕 **自动筛查**：自动识别性能变化较大的用例和设备（FPS 变化 > 10% 或 JANK 变化 > 50%）
- 🆕 **智能建议**：根据分析结果智能建议是否需要调用 perfeye-analysis skill 或 crasheye-crash-workflow skill 进行深入分析
- 自动对比 FPS、JANK、内存峰值三项指标
- 识别性能回归、达标情况、异常数据
- 🔥 **崩溃对比**：对比不同版本的崩溃率变化，检测崩溃回归
- 生成优化建议
```

**修改后：**
```markdown
### 2. 性能对比分析

📖 **完整指南**: [references/PERFORMANCE_COMPARISON.md](references/PERFORMANCE_COMPARISON.md)

🔥 **核心原则**：

**进行性能对比分析时，不管任务状态如何，都必须包含所有状态的任务数据！**

- ❌ **禁止**只对比成功的任务
- ❌ **禁止**排除失败/取消/运行中的任务
- ✅ **必须**包含 RUNNING、QUEUE、CANCEL、FAILED、SUCCESS 所有状态的任务
- ✅ **必须**展示完整的性能数据对比

**原因**：
- 失败的任务可能已执行部分，包含有价值的性能数据
- 被取消的任务可以显示系统资源瓶颈
- 运行中的任务可以显示当前性能趋势
- 完整的数据才能准确判断性能回归

**功能说明**：
- **基础对比**：对比最近 2 份数据，快速检测性能回归（**必须包含所有状态**）
- **深度对比**：对比 3-5 份数据，分析长期性能趋势（**必须包含所有状态**）
- 🆕 **自动筛查**：自动识别性能变化较大的用例和设备（FPS 变化 > 10% 或 JANK 变化 > 50%）
- 🆕 **智能建议**：根据分析结果智能建议是否需要调用 perfeye-analysis skill 或 crasheye-crash-workflow skill 进行深入分析
- 自动对比 FPS、JANK、内存峰值三项指标（**所有状态的任务都要对比**）
- 识别性能回归、达标情况、异常数据
- 🔥 **崩溃对比**：对比不同版本的崩溃率变化，检测崩溃回归（**所有状态的任务都要统计**）
- 生成优化建议
```

**改进点：**
- ✅ 新增"核心原则"章节
- ✅ 明确禁止行为（只对比成功任务）
- ✅ 解释为什么需要所有状态的数据
- ✅ 在每个功能点中强调"必须包含所有状态"

---

### 6. scripts/commands/task.py 命令说明 (第25-62行)

**修改前：**
```python
"""
查询任务信息

示例:
    # 列出最近的任务（不指定状态，返回所有状态）
    python cli.py tasks --count 10

    # 获取任务详情
    python cli.py tasks --id 456

⚡ **重要提示**：
    - 默认情况下（不指定 --status），会返回所有状态的任务
    - 获取任务详情时，会忽略任务的运行状态，支持所有状态（RUNNING、QUEUE、CANCEL、FAILED、SUCCESS）
"""
```

**修改后：**
```python
"""
查询任务信息

🔥 **核心原则**：
    获取任务详情或性能数据时，不管任务状态如何（RUNNING/QUEUE/CANCEL/FAILED/SUCCESS），
    都必须获取和展示所有相关数据，不得因任务状态而过滤或排除任何数据！

示例:
    # 列出最近的任务（不指定状态，返回所有状态）
    python cli.py tasks --count 10

    # 获取任务详情（无论状态如何）
    python cli.py tasks --id 456

⚡ **重要提示**：
    - 🔥 **核心原则**：获取任务详情时，必须包含所有状态的任务数据
    - 默认情况下（不指定 --status），会返回所有状态的任务
    - 使用 --status 参数可以筛选特定状态的任务（仅用于查看特定状态，不代表其他状态不重要）
    - 获取任务详情时，会忽略任务的运行状态，支持所有状态（RUNNING、QUEUE、CANCEL、FAILED、SUCCESS）

❌ **严禁行为**：
    - 禁止因任务状态而过滤或排除任何数据
    - 禁止认为失败/取消任务的数据不重要
    - 禁止在性能分析时排除任何状态的任务
"""
```

**改进点：**
- ✅ 开头直接强调核心原则
- ✅ 新增"严禁行为"章节
- ✅ 在示例中强调"无论状态如何"

---

## 强化的关键要素

### 1. 使用强烈的强调符号
- 🔥 核心原则
- ❌ 严禁行为
- ✅ 必须行为

### 2. 使用强烈的措辞
- **必须** (MUST)
- **不得** (NEVER)
- **禁止** (FORBIDDEN)
- **不管** (REGARDLESS OF)

### 3. 明确禁止行为
不仅说明要做什么，还明确说明**不能做什么**：
- ❌ 禁止因任务状态而过滤或排除任何数据
- ❌ 禁止只显示成功任务而忽略失败/取消任务
- ❌ 禁止认为失败/取消任务的数据不重要
- ❌ 禁止在性能对比时排除任何状态的任务

### 4. 解释原因
说明为什么需要所有状态的数据：
- 失败/取消的任务可能包含重要的调试信息
- 运行中的任务可以显示当前性能趋势
- 排队中的任务可以了解系统负载情况
- 完整的数据才能准确分析问题根因

---

## 修改总结

| 文件 | 修改位置 | 修改内容 |
|------|----------|----------|
| SKILL.md | 第3-22行 | 顶部 description，添加核心原则和 CRITICAL 警告 |
| SKILL.md | 第25-29行 | 核心原则说明 |
| SKILL.md | 第104-153行 | 任务状态支持说明，新增严禁行为和原因说明 |
| SKILL.md | 第170-176行 | 普通任务分析功能说明，强调必须包含所有状态 |
| SKILL.md | 第194-221行 | 性能对比分析功能说明，新增核心原则章节 |
| scripts/commands/task.py | 第25-62行 | 命令说明文档，新增核心原则和严禁行为 |

---

## 效果对比

### 修改前
- ⚡ 支持查询所有状态的任务
- ✅ 完全支持

### 修改后
- 🔥 **核心原则**：不管任务状态如何，都必须获取和展示所有相关数据
- ❌ **禁止**因任务状态而过滤或排除任何数据
- ✅ **必须**包含所有状态的任务

---

## 验证方式

所有修改已完成，可以通过以下方式验证：

1. 查看 SKILL.md 顶部 description 是否包含 🔥 核心特性强调
2. 查看任务状态支持说明是否包含"严禁行为"章节
3. 查看性能对比分析是否包含"核心原则"章节
4. 查看 task.py 命令说明是否包含"核心原则"和"严禁行为"

---

## 相关文档

- SKILL.md - 主要 skill 描述文档
- scripts/commands/task.py - 任务查询命令实现
- references/TASK_DETAIL.md - 任务详情查询参考文档
- references/PERFORMANCE_COMPARISON.md - 性能对比分析参考文档
