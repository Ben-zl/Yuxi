# Unity Profiler 性能分析 Skill 实现规划

## 文档信息
- **创建日期**: 2026-01-22
- **版本**: v1.0
- **状态**: 规划阶段

---

## 1. 概述

### 1.1 Skill 名称
**unity-profiler-analyzer** - Unity 游戏性能分析器

### 1.2 功能定位
基于 `utils/ubox-api` 模块提供的 Unity Profiler 数据接口，实现智能化的性能问题分析工具。通过调用 API 获取性能数据，进行深度分析，生成易读的 Markdown 格式分析报告。

### 1.3 目标用户
- Unity 游戏性能优化工程师
- 游戏客户端开发人员
- QA 性能测试人员
- 技术美术

---

## 2. 核心功能需求

### 2.1 性能概况汇总 (Performance Overview)
**功能描述**: 对整个性能报告进行宏观概览，提供关键指标的快速诊断。

**实现内容**:
- 基础信息汇总：游戏版本、设备信息、测试时间、帧数统计
- 帧率统计：平均 FPS、最高/最低 FPS、FPS 稳定性分析
- 帧时间分析：平均帧时间、P50/P90/P99 分位数
- 内存概览：总内存、Mono 堆内存、GC 内存
- 渲染概览：DrawCall、三角形数、顶点数
- 各模块时间占比：CPU 时间分布饼图数据

**API 调用**:
- `report.get_report_info()` - 报告元数据
- `report.get_case_info()` - 案例概览
- `cpu.get_cpu_performance()` - CPU 性能
- `memory.get_memory_info()` - 内存信息
- `graphic.get_graphic_profile()` - 图形性能

### 2.2 各模块性能问题深入分析 (Module Deep Dive)
**功能描述**: 针对每个性能模块进行深入分析，识别性能瓶颈。

**模块划分**:

#### 2.2.1 CPU 模块分析
- 脚本执行时间分析
- 物理模拟时间分析
- 动画更新时间分析
- GC 性能分析（次数、耗时、频率）
- 线程性能分析（主线程 vs 渲染线程）

#### 2.2.2 渲染模块分析
- DrawCall 分析（总数、动态批次、静态批次）
- SetPass 调用分析
- 三角形/顶点统计
- 渲染热点函数识别
- 批次优化效果评估

#### 2.2.3 物理模块分析
- 物理模拟耗时
- 碰撞检测性能
- 刚体/关节数量统计
- 物理热点函数识别

#### 2.2.4 动画模块分析
- 动画更新耗时
- 蒙皮计算性能
- Animator 组件数量
- 动画热点函数识别

#### 2.2.5 粒子系统分析
- 粒子数量统计
- 粒子更新耗时
- 发射器性能评估

#### 2.2.6 UI 模块分析
- UI 重绘次数
- Canvas 性能
- UI 热点函数识别

#### 2.2.7 资源加载分析
- 加载耗时统计
- 资源大小分析
- 加载瓶颈识别

**API 调用**:
- `cpu.get_cputime()` - CPU 时间统计
- `cpu.query_module_performance()` - 模块性能查询
- `cpu.query_module_diagram()` - 模块时间分布图表
- `hot_module.get_render_module()` - 渲染热点
- `hot_module.get_physics_module()` - 物理热点
- `hot_module.get_animation_module()` - 动画热点
- `hot_module.get_particle_system()` - 粒子热点
- `hot_module.get_ui_module()` - UI 热点
- `hot_module.get_load_module()` - 加载热点
- `graphic.get_graphic_batche()` - 批次数据

### 2.3 热点函数性能分析 (Hotspot Function Analysis)
**功能描述**: 识别和排序性能热点函数，提供函数级别的性能诊断，并支持多维度趋势分析。

**实现内容**:
- **多维度 Top N 排行**:
  - 按 self_time 排行（自身耗时）
  - 按 total_time 排行（总耗时）
  - 按调用次数排行
  - 按平均每次耗时排行 (self_time / calls)
  - 按时间方差排行（稳定性分析）
- **热点函数整体趋势**:
  - 函数耗时随帧数变化趋势
  - 函数调用频率变化
  - 热点函数在不同时间段的分布
  - 识别突然出现的性能退化函数
- **函数调用链分析**:
  - 调用树结构展示
  - 父子函数关系分析
  - 调用深度分析
- **阈值过滤**:
  - 仅显示超过阈值的函数
  - 支持多个指标组合过滤
- **按模块分组显示**:
  - 各模块独立 Top N
  - 跨模块热点对比

**API 调用**:
- `report.get_fun_top()` - 函数 Top 排行
- `report.get_func_list()` - 函数列表
- `cpu.query_func_performance()` - 函数性能查询
- `cpu.query_funtime_diagram()` - 函数时间图表（获取趋势数据）
- `cpu.query_funcinfo()` - 函数详细信息
- `cpu.get_func_data_summary()` - 函数数据摘要统计

### 2.4 帧率卡顿分析 (Frame Rate & Jank Analysis)
**功能描述**: 分析帧率波动和卡顿情况，定位卡顿发生的具体帧。

**实现内容**:
- 掉帧统计：普通掉帧 vs 严重掉帧
- 掉帧帧号列表
- 掉帧时间点分布
- 卡顿严重程度评估
- 掉帧帧的性能详情（Top N 掉帧帧的深入分析）
- 帧率波动趋势分析

**API 调用**:
- `cpu.get_jank_frame()` - 掉帧数据
- `cpu.get_frame_performance()` - 帧性能详情
- `graphic.get_volatility()` - 渲染波动数据

### 2.5 报告对比分析 (Report Comparison)
**功能描述**: 支持多个报告之间的深度对比分析，识别性能变化和具体的问题函数。

**注意**: `daily_report.get_function_trend()` 和 `daily_report.get_memory_trend()` 接口不可用（返回 500 错误），因此通过直接对比两份报告的详细数据来实现。

**实现内容**:
- **关键指标对比**:
  - FPS、内存、DrawCall 等基础指标
  - 性能变化百分比计算
  - 性能退化/改进标记（📈/📉）
- **模块耗时对比**:
  - 各模块平均耗时变化
  - 模块耗时占比变化
  - 识别性能退化/改进最大的模块
- **热点函数对比**:
  - Top N 函数列表变化
  - 新增热点函数识别
  - 消失热点函数识别
  - 函数耗时变化排序
- **具体性能差异问题定位**:
  - 对比两份报告的函数详细数据
  - 找出耗时增长最快的函数
  - 分析增长原因（调用次数增加 vs 单次耗时增加）
  - 给出具体的差异函数列表和变化数据
- **内存对比**:
  - 总内存变化
  - 各模块内存变化
  - GC 频率/耗时变化
- **可视化对比**:
  - 对比表格
  - 变化趋势图（ASCII 图表）

**API 调用**:
- `report.get_report_list()` - 报告列表
- `report.get_case_info()` - 案例信息（用于两份报告的基础对比）
- `report.get_fun_top()` - 函数 Top 排行（两份报告分别获取后对比）
- `cpu.get_cpu_performance()` - CPU 性能对比
- `cpu.get_cputime()` - CPU 时间对比
- `cpu.query_func_performance()` - 具体函数性能对比
- `memory.get_memory_info()` - 内存对比
- `graphic.get_graphic_profile()` - 渲染性能对比

**对比实现策略**:
```
1. 获取报告 A 的所有关键数据（缓存到本地）
2. 获取报告 B 的所有关键数据（缓存到本地）
3. 加载两份报告的缓存数据进行对比
4. 计算差异并排序
5. 识别显著变化（超过阈值的变化）
6. 生成对比报告
```

### 2.6 内存专项分析 (Memory Analysis)
**功能描述**: 专门针对内存使用进行深入分析。

**实现内容**:
- 内存总览：总内存、已用内存、预留内存
- Mono 堆分析：堆大小、GC 堆内存
- GC 详细分析：GC 次数、GC 耗时、GC 频率
- 内存增长趋势分析
- 帧级内存变化
- 内存泄漏风险评估

**API 调用**:
- `memory.get_memory_info()` - Unity 内存信息
- `memory.get_frame_info()` - 帧内存信息
- `cpu.get_cpu_performance()` - 包含 GC 数据

### 2.7 高级分析功能
**功能描述**: 提供更专业的性能分析能力。

#### 2.7.1 Speedscope 数据导出
- 导出 Speedscope 格式的性能分析数据
- 支持可视化工具导入分析

#### 2.7.2 自定义模块查询
- 支持按模块名称查询性能
- 支持按函数名称查询性能
- 支持阈值过滤

#### 2.7.3 GPU 性能分析
- GPU 使用率
- GPU 时间分析
- GPU 体积数据
- KActor 组件性能（Unreal）

**API 调用**:
- `cpu.get_speedscope()` - Speedscope 数据
- `cpu.query_module_performance()` - 自定义模块查询
- `cpu.query_func_performance()` - 自定义函数查询
- `custom.get_gpu_graph_data()` - GPU 图表
- `custom.query_gpu_summary()` - GPU 汇总
- `custom.query_gpu_volume_data()` - GPU 体积数据
- `custom.get_kactor_graph_data()` - KActor 数据

---

## 3. 技术架构设计

### 3.1 目录结构
```
unity-profiler-analyzer/
├── SKILL.md                    # Skill 定义文档（供 Claude 调用）
├── PLAN.md                     # 实现规划文档（本文档）
├── README.md                   # 使用说明文档
├── requirements.txt            # Python 依赖
├── scripts/                    # 核心脚本目录
│   ├── __init__.py
│   ├── analyzer.py             # 主分析器类
│   ├── cli.py                  # 命令行入口
│   ├── preprocess.py           # 大数据预处理脚本
│   ├── collectors/             # 数据收集器
│   │   ├── __init__.py
│   │   ├── base_collector.py   # 基础收集器
│   │   ├── overview_collector.py    # 概览数据收集
│   │   ├── module_collector.py      # 模块数据收集
│   │   ├── hotspot_collector.py     # 热点数据收集
│   │   ├── jank_collector.py        # 卡顿数据收集
│   │   └── memory_collector.py      # 内存数据收集
│   ├── analyzers/              # 分析器
│   │   ├── __init__.py
│   │   ├── base_analyzer.py    # 基础分析器
│   │   ├── cpu_analyzer.py     # CPU 分析器
│   │   ├── rendering_analyzer.py    # 渲染分析器
│   │   ├── physics_analyzer.py      # 物理分析器
│   │   ├── animation_analyzer.py    # 动画分析器
│   │   ├── memory_analyzer.py  # 内存分析器
│   │   ├── jank_analyzer.py    # 卡顿分析器
│   │   └── hotspot_analyzer.py # 热点分析器（新增）
│   ├── reporters/              # 报告生成器
│   │   ├── __init__.py
│   │   ├── base_reporter.py    # 基础报告器
│   │   ├── overview_reporter.py     # 概况报告
│   │   ├── module_reporter.py       # 模块报告
│   │   ├── hotspot_reporter.py      # 热点报告
│   │   ├── jank_reporter.py         # 卡顿报告
│   │   ├── comparison_reporter.py   # 对比报告
│   │   └── markdown_formatter.py    # Markdown 格式化器
│   ├── comparers/              # 对比器
│   │   ├── __init__.py
│   │   ├── report_comparer.py  # 报告对比器
│   │   └── metrics_comparer.py # 指标对比器
│   ├── preprocessors/          # 预处理器（新增）
│   │   ├── __init__.py
│   │   ├── base_preprocessor.py
│   │   ├── hotspot_preprocessor.py  # 热点函数预处理
│   │   ├── jank_preprocessor.py     # 卡顿数据预处理
│   │   └── comparison_preprocessor.py  # 对比数据预处理
│   ├── cache/                  # 缓存管理（新增）
│   │   ├── __init__.py
│   │   ├── cache_manager.py    # 缓存管理器
│   │   ├── cache_store.py      # 缓存存储（JSON文件）
│   │   └── cache_key.py        # 缓存键生成
│   └── utils/                  # 工具函数
│       ├── __init__.py
│       ├── config.py           # 配置管理
│       ├── helpers.py          # 辅助函数
│       ├── thresholds.py       # 阈值定义
│       └── data_filter.py      # 数据过滤（新增）
├── cache/                      # 缓存数据目录（新增）
│   ├── .gitkeep
│   └── {case_id}/              # 按案例 ID 分组
│       ├── overview.json
│       ├── modules.json
│       ├── hotspots.json
│       ├── jank.json
│       └── memory.json
└── tests/                      # 测试
    ├── __init__.py
    └── test_analyzer.py
```

### 3.2 核心类设计

#### 3.2.1 ProfilerAnalyzer (主分析器)
```python
class ProfilerAnalyzer:
    """Unity Profiler 性能分析主类

    支持渐进式数据获取和本地缓存
    """

    def __init__(self, base_url: str, project_id: str, cache_dir: str = "./cache"):
        self.client = UboxProfilerClient(base_url, project_id)
        self.cache_manager = CacheManager(cache_dir)  # 缓存管理器

    def analyze_overview(self, case_id: str, use_cache: bool = True) -> OverviewReport:
        """分析性能概况

        Args:
            case_id: 案例 ID
            use_cache: 是否使用缓存（默认 True）
        """
        pass

    def analyze_hotspots(self, case_id: str, top_n: int = 20,
                         sort_by: str = "self_time") -> HotspotReport:
        """分析热点函数

        Args:
            case_id: 案例 ID
            top_n: Top N 数量
            sort_by: 排序指标 (self_time/total_time/calls/avg_time/variance)
        """
        pass

    def compare_reports(self, case_id_a: str, case_id_b: str) -> ComparisonReport:
        """对比两个报告

        使用缓存数据进行对比，避免重复请求
        """
        pass
```

#### 3.2.2 CacheManager (缓存管理器) - 新增
```python
class CacheManager:
    """数据缓存管理器

    实现:
    - 按案例 ID 分组的缓存存储
    - JSON 格式持久化
    - 缓存有效期管理
    - 缓存命中率统计
    """

    def __init__(self, cache_dir: str, ttl: int = 86400):
        """初始化缓存管理器

        Args:
            cache_dir: 缓存目录
            ttl: 缓存有效期（秒），默认 24 小时
        """

    def get(self, case_id: str, data_type: str) -> Optional[dict]:
        """获取缓存数据"""

    def set(self, case_id: str, data_type: str, data: dict):
        """保存缓存数据"""

    def is_expired(self, case_id: str, data_type: str) -> bool:
        """检查缓存是否过期"""

    def clear(self, case_id: Optional[str] = None):
        """清除缓存"""
```

#### 3.2.3 数据收集器 (Collectors) - 增强版
```python
class BaseCollector:
    """基础收集器

    支持:
    - 渐进式数据获取（按需加载）
    - 自动缓存管理
    - 错误重试
    """

    def __init__(self, client: UboxProfilerClient, cache_manager: CacheManager):
        self.client = client
        self.cache = cache_manager

    def collect(self, case_id: str, use_cache: bool = True) -> dict:
        """收集数据（优先使用缓存）"""

    def _fetch_from_api(self, case_id: str) -> dict:
        """从 API 获取数据（子类实现）"""

    def _save_to_cache(self, case_id: str, data: dict):
        """保存到缓存"""
```

#### 3.2.4 预处理器 (Preprocessors) - 新增
```python
class BasePreprocessor:
    """大数据预处理器

    对于数据量过大的数据（如函数列表、掉帧列表）:
    1. 先通过脚本进行初步分析
    2. 提取关键统计信息
    3. 生成摘要数据
    4. 然后交给 AI 进行深度分析
    """

    def preprocess(self, raw_data: dict) -> dict:
        """预处理原始数据

        Returns:
            摘要数据，包含统计信息和关键样本
        """

class HotspotPreprocessor(BasePreprocessor):
    """热点函数数据预处理器"""

    def preprocess(self, raw_data: dict) -> dict:
        """处理热点函数数据

        生成:
        - 多维度 Top N 排行
        - 趋势统计摘要
        - 关键样本数据
        """

class JankPreprocessor(BasePreprocessor):
    """卡顿数据预处理器"""

    def preprocess(self, raw_data: dict) -> dict:
        """处理卡顿数据

        生成:
        - 掉帧统计摘要
        - Top 掉帧帧样本
        - 掉帧分布统计
        """
```

### 3.3 渐进式数据获取策略 - 新增

#### 3.3.1 设计原则
1. **按需加载**: 只获取当前分析需要的数据
2. **缓存优先**: 优先使用本地缓存，减少 API 请求
3. **分页处理**: 大数据量采用分页获取
4. **后台预取**: 预测可能需要的数据，提前获取

#### 3.3.2 渐进式加载流程
```
用户请求分析
    ↓
检查缓存 (cache_manager.get())
    ↓
    ├─ 缓存命中 → 直接使用缓存数据
    │                  ↓
    │              返回结果
    │
    └─ 缓存未命中 → 从 API 获取数据
                        ↓
                    保存到缓存
                        ↓
                    返回结果
```

#### 3.3.3 分层加载策略
```python
# 第一层：基础信息（立即加载）
- report.get_report_info()        # 基础元数据
- report.get_case_info()          # 案例概览

# 第二层：核心指标（按需加载）
- cpu.get_cpu_performance()       # CPU 性能
- memory.get_memory_info()        # 内存信息
- graphic.get_graphic_profile()   # 渲染性能

# 第三层：详细数据（深度分析时加载）
- report.get_fun_top()            # 函数 Top N
- cpu.get_jank_frame()            # 掉帧数据
- hot_module.*()                  # 模块热点

# 第四层：原始数据（需要时加载）
- cpu.query_func_performance()    # 具体函数性能
- cpu.get_frame_performance()     # 帧性能详情
```

### 3.4 大数据处理流程 - 新增

#### 3.4.1 判断标准
```python
BIG_DATA_THRESHOLDS = {
    "function_list": 1000,      # 函数列表 > 1000 个
    "jank_frames": 100,         # 掉帧数 > 100 个
    "frame_samples": 50,        # 帧样本 > 50 个
}
```

#### 3.4.2 处理流程
```
大数据检测
    ↓
    ├─ 小数据 → 直接分析 → 生成报告
    │
    └─ 大数据 → 预处理脚本 → 摘要数据 → AI 分析 → 生成报告
                      ↓
                  保存摘要到缓存
```

#### 3.4.3 预处理示例
```python
# 热点函数预处理
原始数据: 5000+ 函数
    ↓ 预处理脚本
摘要数据:
  - Top 100 函数（按多个指标）
  - 按模块分组的 Top 10
  - 趋势统计（耗时分布、调用分布）
  - 关键样本（Top 20 的详细数据）
    ↓ AI 分析
识别性能瓶颈、生成优化建议

# 卡顿数据预处理
原始数据: 500+ 掉帧
    ↓ 预处理脚本
摘要数据:
  - 掉帧统计（总数、分布、频率）
  - Top 50 掉帧帧详情
  - 掉帧原因分类
  - 时间分布统计
    ↓ AI 分析
识别卡顿模式、定位原因
```

### 3.5 数据流设计 - 更新

```
用户输入 (case_id / options)
    ↓
CLI 入口解析
    ↓
ProfilerAnalyzer (主分析器)
    ↓
检查缓存 (CacheManager)
    ↓
    ├─ 缓存命中 ──────────────────────┐
    │                                  ↓
    └─ 缓存未命中 → 数据收集器    数据加载
                          ↓            ↓
                     渐进式 API 调用  (from cache)
                          ↓            ↓
                      大数据判断        ↓
                          ↓            ↓
                     ┌─ 小数据 ────────┤
                     │                 ↓
                     └─ 大数据 → 预处理器 → 摘要数据
                                        ↓
                                   分析器 (Analyzers)
                                        ↓
                                   性能问题识别
                                        ↓
                                   报告器 (Reporters)
                                        ↓
                                   Markdown 输出
                                        ↓
                                   保存缓存
                                        ↓
                                   用户查看报告
```

---

## 4. Markdown 报告模板设计（更新）

### 4.1 概况报告模板
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
| 指标 | 数值 |
|------|------|
| 平均 FPS | {avg_fps} |
| 最高 FPS | {max_fps} |
| 最低 FPS | {min_fps} |
| FPS 标准差 | {fps_std} |
| 平均帧时间 | {avg_frame_time} ms |

### 帧时间分布
| 百分位 | 帧时间 (ms) |
|--------|-------------|
| P50 | {p50} |
| P90 | {p90} |
| P99 | {p99} |

### 内存概览
| 类型 | 预留 (MB) | 已用 (MB) | 使用率 |
|------|-----------|-----------|--------|
| 总内存 | {total_reserved} | {total_used} | {total_usage}% |
| Mono 堆 | {mono_reserved} | {mono_used} | {mono_usage}% |

### 渲染概览
| 指标 | 数值 |
|------|------|
| DrawCalls | {draw_calls} |
| SetPass Calls | {setpass_calls} |
| 三角形数 | {triangles} |
| 顶点数 | {vertices} |

### CPU 时间分配
| 模块 | 时间 (ms) | 占比 |
|------|-----------|------|
| 渲染 | {rendering_time} | {rendering_pct}% |
| 脚本 | {script_time} | {script_pct}% |
| 物理 | {physics_time} | {physics_pct}% |
| 动画 | {animation_time} | {animation_pct}% |
```

### 4.2 模块分析报告模板
```markdown
## 🔍 模块深入分析

### CPU 模块
#### 脚本执行
- 平均耗时: {script_avg_time} ms
- 占比: {script_pct}%
- 状态: {status} ⚠️/✅
- 问题描述: {issue_description}

#### 物理模拟
- 平均耗时: {physics_avg_time} ms
- 占比: {physics_pct}%
- 碰撞接触点: {contacts}
- 活动刚体: {active_rigidbodies}

#### GC 性能
- 总 GC 次数: {total_gc}
- Loading GC: {loading_gc}
- 平均 GC 耗时: {gc_avg_time} ms
- GC 频率: {gc_frequency} 次/分钟

### 渲染模块
- DrawCalls: {draw_calls} {status}
- 动态批次: {dynamic_batches}
- 节省批次数: {saved_batches}
- 三角形数: {triangles}
- 顶点数: {vertices}

#### 渲染热点函数
1. {hotspot_1}: {time} ms
2. {hotspot_2}: {time} ms
```

### 4.3 热点函数报告模板（增强版）
```markdown
## 🔥 热点函数分析

### 多维度 Top N 排行

#### Top 20 按自身耗时 (Self Time)

| 排名 | 函数名 | 模块 | Self Time (ms) | Total Time (ms) | 调用次数 | 平均耗时 (ms) |
|------|--------|------|----------------|-----------------|----------|---------------|
| 1 | {func_name} | {module} | {self_time} | {total_time} | {calls} | {avg_time} |
| 2 | ... | ... | ... | ... | ... | ... |

#### Top 20 按总耗时 (Total Time)

| 排名 | 函数名 | 模块 | Total Time (ms) | Self Time (ms) | 调用次数 |
|------|--------|------|-----------------|----------------|----------|
| 1 | {func_name} | {module} | {total_time} | {self_time} | {calls} |
| 2 | ... | ... | ... | ... | ... |

#### Top 20 按调用次数

| 排名 | 函数名 | 模块 | 调用次数 | Total Time (ms) | 平均耗时 (ms) |
|------|--------|------|----------|----------------|---------------|
| 1 | {func_name} | {module} | {calls} | {total_time} | {avg_time} |
| 2 | ... | ... | ... | ... | ... |

#### Top 20 按平均单次耗时

| 排名 | 函数名 | 模块 | 平均耗时 (ms) | Self Time (ms) | 调用次数 |
|------|--------|------|---------------|----------------|----------|
| 1 | {func_name} | {module} | {avg_time} | {self_time} | {calls} |
| 2 | ... | ... | ... | ... | ... |

#### Top 20 按耗时方差（稳定性分析）

| 排名 | 函数名 | 模块 | 时间方差 | 标准差 | 平均耗时 (ms) | 评估 |
|------|--------|------|----------|--------|---------------|------|
| 1 | {func_name} | {module} | {variance} | {std_dev} | {avg_time} | ⚠️/✅ |
| 2 | ... | ... | ... | ... | ... | ... |

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
---

### 4.4 对比报告模板（增强版）
```markdown
## 📊 报告对比分析

### 对比概览
- **报告 A**: {report_a_info}
- **报告 B**: {report_b_info}
- **对比时间**: {comparison_time}

### 关键指标对比

| 指标 | 报告 A | 报告 B | 变化值 | 变化率 | 趋势 | 评估 |
|------|--------|--------|--------|--------|------|------|
| 平均 FPS | {fps_a} | {fps_b} | {delta} | {delta_pct}% | {trend} 📈/📉 | {status} |
| 内存 (MB) | {mem_a} | {mem_b} | {delta} | {delta_pct}% | {trend} 📈/📉 | {status} |
| DrawCalls | {dc_a} | {dc_b} | {delta} | {delta_pct}% | {trend} 📈/📉 | {status} |
| 平均帧时间 | {ft_a} | {ft_b} | {delta} | {delta_pct}% | {trend} 📈/📉 | {status} |

### 模块耗时对比

| 模块 | 报告 A (ms) | 报告 B (ms) | 变化 | 变化率 | 趋势 | 影响评估 |
|------|-------------|-------------|------|--------|------|----------|
| 渲染 | {render_a} | {render_b} | {delta} | {delta_pct}% | {trend} | {impact} |
| 脚本 | {script_a} | {script_b} | {delta} | {delta_pct}% | {trend} | {impact} |
| 物理 | {physics_a} | {physics_b} | {delta} | {delta_pct}% | {trend} | {impact} |
| 动画 | {anim_a} | {anim_b} | {delta} | {delta_pct}% | {trend} | {impact} |
| GC | {gc_a} | {gc_b} | {delta} | {delta_pct}% | {trend} | {impact} |

### 性能差异函数详细分析

#### 性能退化 Top 20（耗时增长最多的函数）

| 排名 | 函数名 | 模块 | 报告A (ms) | 报告B (ms) | 变化 | 变化率 | 原因分析 |
|------|--------|------|------------|------------|------|--------|----------|
| 1 | {func} | {mod} | {time_a} | {time_b} | {delta} | {pct}% | {reason} |
| 2 | ... | ... | ... | ... | ... | ... | ... |

**原因分类**:
- 调用次数增加导致的函数：{list}
- 单次耗时增加导致的函数：{list}
- 新出现的性能瓶颈函数：{list}

#### 性能改进 Top 10（耗时减少最多的函数）

| 排名 | 函数名 | 模块 | 报告A (ms) | 报告B (ms) | 变化 | 变化率 |
|------|--------|------|------------|------------|------|--------|
| 1 | {func} | {mod} | {time_a} | {time_b} | {delta} | {pct}% |
| ... | ... | ... | ... | ... | ... | ... |

#### 新增热点函数（报告 B 中新出现的高耗时函数）

| 函数名 | 模块 | 耗时 | 调用次数 | 可能原因 |
|--------|------|------|----------|----------|
| {func} | {mod} | {time} | {calls} | {reason} |

#### 消失热点函数（报告 A 中的热点，在报告 B 中消失）

| 函数名 | 模块 | 报告A 耗时 | 优化效果 |
|--------|------|------------|----------|
| {func} | {mod} | {time} | {effect} |

### 具体性能差异问题定位

#### 问题 1: {problem_title}
- **问题描述**: {description}
- **影响函数**: {affected_functions}
- **变化数据**:
  - 函数 A: {func_a} ({delta_a} → {delta_b}, {delta_pct}%)
  - 函数 B: {func_b} ({delta_a} → {delta_b}, {delta_pct}%)
- **可能原因**: {possible_reasons}
- **优化建议**: {optimization_suggestions}

#### 问题 2: {problem_title}
...

### 内存对比分析

| 内存类型 | 报告 A (MB) | 报告 B (MB) | 变化 | 变化率 | 趋势 |
|----------|-------------|-------------|------|--------|------|
| 总内存 | {total_a} | {total_b} | {delta} | {pct}% | {trend} |
| Mono 堆 | {mono_a} | {mono_b} | {delta} | {pct}% | {trend} |
| GC 堆 | {gc_a} | {gc_b} | {delta} | {pct}% | {trend} |

#### GC 性能对比
- GC 次数: {gc_count_a} → {gc_count_b} ({delta} 次, {delta_pct}%)
- GC 平均耗时: {gc_time_a} → {gc_time_b} ({delta} ms, {delta_pct}%)
- GC 频率: {gc_freq_a} → {gc_freq_b} ({delta} 次/分钟)

### 总结与建议

#### 主要变化
{main_changes}

#### 需要关注的性能问题
{attention_points}

#### 优化建议优先级
1. **高优先级**: {high_priority_items}
2. **中优先级**: {medium_priority_items}
3. **低优先级**: {low_priority_items}
```

### 4.4 卡顿分析报告模板
```markdown
## 📉 帧率卡顿分析

### 掉帧统计
| 类型 | 次数 | 占比 |
|------|------|------|
| 普通掉帧 | {jank_count} | {jank_pct}% |
| 严重掉帧 | {big_jank_count} | {big_jank_pct}% |
| 总掉帧 | {total_jank} | {total_pct}% |

### 掉帧分布
- 总帧数: {total_frames}
- 掉帧率: {jank_rate}%
- 平均间隔: {avg_interval} 帧

### Top 10 掉帧帧详情
| 帧号 | 帧时间 (ms) | 主要耗时模块 | 主要耗时函数 |
|------|-------------|-------------|-------------|
| {frame_1} | {frame_time} | {module} | {function} |
| ... | ... | ... | ... |

### 卡顿原因分析
{causality_analysis}
```

### 4.5 对比报告模板
```markdown
## 📊 报告对比分析

### 对比概览
- 报告 A: {report_a_info}
- 报告 B: {report_b_info}
- 对比时间: {comparison_time}

### 关键指标对比
| 指标 | 报告 A | 报告 B | 变化 | 趋势 |
|------|--------|--------|------|------|
| 平均 FPS | {fps_a} | {fps_b} | {delta} | {trend} 📈/📉 |
| 内存 (MB) | {mem_a} | {mem_b} | {delta} | {trend} |
| DrawCalls | {dc_a} | {dc_b} | {delta} | {trend} |

### 模块耗时对比
| 模块 | 报告 A (ms) | 报告 B (ms) | 变化 | 趋势 |
|------|-------------|-------------|------|------|
| 渲染 | {render_a} | {render_b} | {delta} | {trend} |
| 脚本 | {script_a} | {script_b} | {delta} | {trend} |

### 热点函数变化
{hotspot_changes}
```

---

## 5. API 调用流程设计

### 5.1 概况分析流程
```
1. report.get_report_info(case_id) → 获取基础信息
2. report.get_case_info(case_id) → 获取案例概览
3. cpu.get_cpu_performance(case_id) → 获取 CPU 性能
4. memory.get_memory_info(case_id) → 获取内存信息
5. graphic.get_graphic_profile(case_id) → 获取渲染性能
6. 数据汇总 → 生成报告
```

### 5.2 模块分析流程
```
1. cpu.get_cputime(case_id) → CPU 时间分布
2. hot_module.get_{module}_module(case_id) → 模块热点
3. cpu.query_module_performance(case_id, module_name, project_id) → 模块详细性能
4. cpu.query_module_diagram(case_id, module_name, project_id) → 时间分布图表
5. 分析计算 → 生成模块报告
```

### 5.3 热点函数分析流程
```
1. report.get_fun_top(case_id, threshold) → Top 函数
2. report.get_func_list(case_id) → 函数列表
3. cpu.query_func_performance(case_id, func_name) → 函数性能
4. cpu.query_funcinfo(case_id, item_id, func_name) → 函数详细信息
5. 按模块分组排序 → 生成热点报告
```

### 5.4 卡顿分析流程
```
1. cpu.get_jank_frame(case_id) → 掉帧列表
2. 对每个掉帧帧:
   a. cpu.get_frame_performance(case_id, frame_id) → 帧详情
   b. 分析主要耗时模块和函数
3. graphic.get_volatility(case_id, threshold) → 渲染波动
4. 统计分析 → 生成卡顿报告
```

### 5.5 对比分析流程
```
1. 对每个 case_id:
   a. 获取完整性能数据
   b. 计算关键指标
2. 指标两两对比
3. 计算变化百分比
4. 识别显著变化
5. 生成对比报告
```

---

## 6. 性能阈值定义

### 6.1 帧率阈值
```python
FPS_THRESHOLDS = {
    "excellent": 60,    # 优秀
    "good": 45,         # 良好
    "acceptable": 30,   # 可接受
    "poor": 20,         # 较差
    "terrible": 10,     # 极差
}
```

### 6.2 内存阈值 (MB)
```python
MEMORY_THRESHOLDS = {
    "total_usage": {
        "warning": 500,     # 警告
        "critical": 1000,   # 严重
        "emergency": 1500,  # 紧急
    },
    "mono_heap": {
        "warning": 200,
        "critical": 400,
    }
}
```

### 6.3 渲染阈值
```python
RENDERING_THRESHOLDS = {
    "draw_calls": {
        "good": 100,
        "warning": 300,
        "critical": 500,
    },
    "triangles": {
        "good": 50000,
        "warning": 100000,
        "critical": 200000,
    }
}
```

### 6.4 模块耗时阈值 (ms)
```python
MODULE_TIME_THRESHOLDS = {
    "script": {
        "warning": 5,
        "critical": 10,
    },
    "physics": {
        "warning": 3,
        "critical": 5,
    },
    "rendering": {
        "warning": 8,
        "critical": 12,
    }
}
```

### 6.5 GC 阈值
```python
GC_THRESHOLDS = {
    "frequency": {
        "warning": 10,      # 10次/分钟
        "critical": 30,     # 30次/分钟
    },
    "avg_time": {
        "warning": 5,       # 5ms
        "critical": 10,     # 10ms
    }
}
```

---

## 7. 命令行接口设计

### 7.1 基本用法
```bash
# 概况分析
python scripts/cli.py analyze <case_id> --overview

# 模块分析
python scripts/cli.py analyze <case_id> --module <module_name>

# 热点分析
python scripts/cli.py analyze <case_id> --hotspots --top 20

# 卡顿分析
python scripts/cli.py analyze <case_id> --jank

# 内存分析
python scripts/cli.py analyze <case_id> --memory

# 对比分析
python scripts/cli.py compare <case_id1> <case_id2>

# 完整分析
python scripts/cli.py analyze <case_id> --all
```

### 7.2 参数选项
```
positional arguments:
  case_id               案例 ID (UUID)

optional arguments:
  -h, --help            显示帮助信息
  --url URL             API 基础 URL
  --project PROJECT     项目 ID
  -o, --output FILE     输出文件路径
  -f, --format FORMAT   报告格式 (markdown/json/html)

Analysis Options:
  --overview            性能概况
  --module MODULE       模块分析
  --hotspots            热点函数
  --jank                卡顿分析
  --memory              内存分析
  --all                 完整分析

Hotspot Options:
  --top N               Top N 热点函数
  --threshold TIME      最小耗时阈值 (ms)

Module Options:
  --cpu                 CPU 模块
  --rendering           渲染模块
  --physics             物理模块
  --animation           动画模块
  --ui                  UI 模块

Comparison Options:
  --baseline CASE_ID    基线案例 ID
  --delta-threshold PCT 变化阈值 (%)

Output Options:
  --verbose             详细输出
  --debug               调试模式
  --no-color            禁用颜色输出
```

---

## 8. 开发计划（更新）

### 8.1 Phase 1: 基础框架 (Week 1)
- [ ] 创建项目结构（包含 cache/ 和 preprocessors/ 目录）
- [ ] 实现基础类 (BaseCollector, BaseAnalyzer, BaseReporter)
- [ ] 实现 ProfilerAnalyzer 主类
- [ ] 实现 **CacheManager** 缓存管理器
- [ ] 实现配置管理
- [ ] 编写单元测试

### 8.2 Phase 2: 缓存和数据收集 (Week 2)
- [ ] CacheStore 缓存存储实现
- [ ] CacheKey 缓存键生成实现
- [ ] OverviewCollector 实现（支持缓存）
- [ ] ModuleCollector 实现（支持缓存）
- [ ] HotspotCollector 实现（支持缓存）
- [ ] JankCollector 实现（支持缓存）
- [ ] MemoryCollector 实现（支持缓存）
- [ ] 测试缓存机制

### 8.3 Phase 3: 预处理器 (Week 3)
- [ ] BasePreprocessor 基础预处理器实现
- [ ] HotspotPreprocessor 热点函数预处理
- [ ] JankPreprocessor 卡顿数据预处理
- [ ] ComparisonPreprocessor 对比数据预处理
- [ ] 大数据检测和分流逻辑
- [ ] 预处理脚本集成

### 8.4 Phase 4: 分析器 (Week 4)
- [ ] CPUAnalyzer 实现
- [ ] RenderingAnalyzer 实现
- [ ] PhysicsAnalyzer 实现
- [ ] AnimationAnalyzer 实现
- [ ] MemoryAnalyzer 实现
- [ ] JankAnalyzer 实现
- [ ] **HotspotAnalyzer** 实现（新增，支持多维度排序和趋势分析）

### 8.5 Phase 5: 报告生成器 (Week 5)
- [ ] MarkdownFormatter 实现
- [ ] OverviewReporter 实现
- [ ] ModuleReporter 实现
- [ ] **HotspotReporter** 增强（多维度 Top N 表格）
- [ ] JankReporter 实现
- [ ] MemoryReporter 实现
- [ ] **ComparisonReporter** 增强（详细差异分析）

### 8.6 Phase 6: 对比功能 (Week 6)
- [ ] ReportComparer 实现（基于缓存数据）
- [ ] MetricsComparer 实现
- [ ] 函数级差异分析器
- [ ] 性能退化识别器
- [ ] ComparisonReporter 实现（增强版）
- [ ] 测试对比功能

### 8.7 Phase 7: CLI 和集成 (Week 7)
- [ ] CLI 入口实现
- [ ] 参数解析（增加缓存和预处理选项）
- [ ] 与 ubox-api 集成
- [ ] 渐进式加载流程集成
- [ ] 错误处理完善
- [ ] 文档编写

### 8.8 Phase 8: 测试和优化 (Week 8)
- [ ] 端到端测试
- [ ] 缓存命中率测试
- [ ] 大数据预处理测试
- [ ] 真实案例测试
- [ ] 性能测试
- [ ] 文档完善
- [ ] 发布准备

---

## 9. 新增功能摘要

### 9.1 热点函数分析增强
| 功能 | 描述 | 优先级 |
|------|------|--------|
| 多维度 Top N | self_time/total_time/calls/avg_time/variance | 高 |
| 趋势分析 | 函数耗时随帧数变化、识别性能退化 | 高 |
| 调用链分析 | 调用树、父子关系、调用深度 | 中 |
| 按模块分组 | 各模块独立 Top N | 中 |

### 9.2 报告对比分析增强
| 功能 | 描述 | 优先级 |
|------|------|--------|
| 基于缓存的对比 | 使用缓存数据进行对比，避免重复请求 | 高 |
| 具体差异函数定位 | 找出耗时增长最快的函数 | 高 |
| 原因分析 | 调用次数增加 vs 单次耗时增加 | 高 |
| 新增/消失热点 | 识别新出现和消失的瓶颈 | 中 |
| 性能问题汇总 | 按优先级给出优化建议 | 中 |

### 9.3 渐进式数据获取
| 功能 | 描述 | 优先级 |
|------|------|--------|
| 缓存管理器 | JSON 格式持久化，按案例 ID 分组 | 高 |
| 分层加载策略 | 4 层加载，按需获取数据 | 高 |
| 缓存优先策略 | 优先使用缓存，减少 API 请求 | 高 |

### 9.4 大数据处理
| 功能 | 描述 | 优先级 |
|------|------|--------|
| 大数据检测 | 函数列表 > 1000，掉帧 > 100 等 | 高 |
| 预处理器 | 提取摘要统计和关键样本 | 高 |
| 分流逻辑 | 小数据直接分析，大数据先预处理 | 高 |

---

## 10. 技术栈（更新）

### 10.1 核心依赖
```txt
# 现有依赖（来自 ubox-api）
requests>=2.28.0
pycryptodome>=3.15.0

# 新增依赖
dataclasses-json>=0.5.9    # 数据类序列化
rich>=13.0.0               # 终端美化输出
click>=8.1.0               # CLI 框架
tabulate>=0.9.0            # 表格输出
python-dateutil>=2.8.2     # 日期处理
numpy>=1.24.0              # 数值计算（用于统计分析）
diskcache>=5.6.0           # 磁盘缓存（可选，用于缓存管理）
```

### 10.2 开发依赖
```txt
pytest>=7.2.0              # 测试框架
pytest-cov>=4.0.0          # 覆盖率
black>=23.0.0              # 代码格式化
flake8>=6.0.0              # 代码检查
mypy>=1.0.0                # 类型检查
```

---

## 11. 风险和挑战（更新）

### 11.1 技术风险
| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| API 返回数据不一致 | 中 | 添加数据验证和容错处理 |
| 大量数据导致性能问题 | 中 | **实现预处理和缓存机制** |
| 某些 API 接口不稳定 | 低 | 添加重试和降级逻辑 |
| 缓存数据过期 | 中 | 实现缓存有效期管理和版本控制 |
| 预处理摘要丢失信息 | 中 | 提供配置选项，可选择详细模式 |

### 11.2 业务风险
| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| 阈值定义不准确 | 中 | 提供可配置阈值 |
| 报告过于冗长 | 低 | 支持报告级别配置 |
| 分析结果误判 | 中 | 多维度验证，提供置信度 |
| 对比分析误报 | 中 | 增加显著性检验，避免噪音干扰 |

---

## 12. 未来扩展

### 12.1 短期扩展 (3 个月内)
- Web UI 界面
- 报告导出为 HTML/PDF
- 性能趋势可视化图表
- 实时分析模式

### 12.2 长期扩展 (6 个月内)
- 机器学习驱动的性能问题预测
- 自动化性能优化建议
- 与 CI/CD 集成
- 性能回归测试框架
- 多报告批量对比

---

## 13. 附录

### 13.1 相关文档
- [ubox-api API 文档](../../utils/ubox-api/API.md)
- [ubox-api README](../../utils/ubox-api/README.md)

### 13.2 参考资料
- Unity Profiler 官方文档
- 游戏性能优化最佳实践
- Python 包结构规范

---

## 变更历史

| 版本 | 日期 | 变更内容 | 作者 |
|------|------|----------|------|
| v1.0 | 2026-01-22 | 初始规划文档 | Claude |
| v1.1 | 2026-01-22 | **主要更新**: <br>1. **热点函数分析增强**: 多维度 Top N 排行、趋势分析、调用链分析<br>2. **报告对比分析增强**: 基于缓存的对比、具体差异函数定位、原因分析<br>3. **渐进式数据获取**: 缓存管理器、分层加载策略<br>4. **大数据处理**: 预处理器、分流逻辑、摘要生成<br>5. **架构更新**: 新增 cache/、preprocessors/ 目录<br>6. **报告模板增强**: 多维度表格、详细对比报告 | Claude |
