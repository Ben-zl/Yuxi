# 架构设计 (Architecture)

## 概述

本工具采用分层架构设计，包含数据收集、分析、报告生成和缓存管理等多个层次。

## 目录结构

```
unity-profiler-analyzer/
├── SKILL.md                    # Agent Skill 主文档
├── README.md                   # 使用说明
├── requirements.txt            # 依赖
├── scripts/                    # 核心脚本
│   ├── __init__.py
│   ├── analyzer.py             # 主分析器
│   ├── cli.py                  # CLI 入口
│   ├── preprocess.py           # 预处理脚本
│   ├── collectors/             # 数据收集器
│   │   ├── __init__.py
│   │   ├── base_collector.py   # 基础收集器
│   │   ├── overview_collector.py
│   │   ├── module_collector.py
│   │   ├── hotspot_collector.py
│   │   ├── jank_collector.py
│   │   └── memory_collector.py
│   ├── analyzers/              # 分析器
│   │   ├── __init__.py
│   │   ├── base_analyzer.py
│   │   ├── cpu_analyzer.py
│   │   ├── rendering_analyzer.py
│   │   ├── physics_analyzer.py
│   │   ├── animation_analyzer.py
│   │   ├── memory_analyzer.py
│   │   ├── jank_analyzer.py
│   │   └── hotspot_analyzer.py
│   ├── reporters/              # 报告生成器
│   │   ├── __init__.py
│   │   ├── base_reporter.py
│   │   ├── overview_reporter.py
│   │   ├── module_reporter.py
│   │   ├── hotspot_reporter.py
│   │   ├── jank_reporter.py
│   │   ├── memory_reporter.py
│   │   ├── comparison_reporter.py
│   │   └── markdown_formatter.py
│   ├── comparers/              # 对比器
│   │   ├── __init__.py
│   │   ├── report_comparer.py
│   │   └── metrics_comparer.py
│   ├── preprocessors/          # 预处理器
│   │   ├── __init__.py
│   │   ├── base_preprocessor.py
│   │   ├── hotspot_preprocessor.py
│   │   ├── jank_preprocessor.py
│   │   └── comparison_preprocessor.py
│   ├── cache/                  # 缓存管理
│   │   ├── __init__.py
│   │   ├── cache_manager.py
│   │   ├── cache_store.py
│   │   └── cache_key.py
│   └── utils/                  # 工具函数
│       ├── __init__.py
│       ├── config.py
│       ├── helpers.py
│       ├── thresholds.py
│       └── data_filter.py
├── cache/                      # 缓存数据
│   └── {case_id}/
│       ├── meta.json
│       ├── overview.json
│       ├── modules.json
│       ├── hotspots.json
│       ├── jank.json
│       └── memory.json
├── docs/                       # 文档
│   ├── overview.md
│   ├── hotspot-analysis.md
│   ├── jank-analysis.md
│   ├── memory-analysis.md
│   ├── comparison-analysis.md
│   ├── cpu-module.md
│   ├── rendering-module.md
│   ├── physics-module.md
│   ├── animation-module.md
│   ├── ui-module.md
│   ├── data-strategy.md
│   ├── workflows.md
│   └── architecture.md
└── tests/                      # 测试
    ├── __init__.py
    └── test_analyzer.py
```

## 核心类设计

### ProfilerAnalyzer (主分析器)

```python
class ProfilerAnalyzer:
    """Unity Profiler 性能分析主类

    支持渐进式数据获取和本地缓存
    """

    def __init__(
        self,
        base_url: str,
        project_id: str,
        cache_dir: str = "./cache",
        cache_ttl: int = 86400,
        enable_cache: bool = True,
        enable_preprocess: bool = True
    ):
        """初始化分析器

        Args:
            base_url: API 基础 URL
            project_id: 项目 ID
            cache_dir: 缓存目录
            cache_ttl: 缓存有效期（秒）
            enable_cache: 是否启用缓存
            enable_preprocess: 是否启用预处理
        """
        self.client = UboxProfilerClient(base_url, project_id)
        self.cache_manager = CacheManager(cache_dir, cache_ttl)
        self.enable_cache = enable_cache
        self.enable_preprocess = enable_preprocess

    def analyze_overview(
        self,
        case_id: str,
        use_cache: bool = True
    ) -> OverviewReport:
        """分析性能概况"""
        pass

    def analyze_hotspots(
        self,
        case_id: str,
        top_n: int = 20,
        sort_by: str = "self_time",
        include_trend: bool = False,
        include_call_chain: bool = False,
        group_by_module: bool = False,
        detailed: bool = False
    ) -> HotspotReport:
        """分析热点函数"""
        pass

    def analyze_jank(
        self,
        case_id: str,
        top_n: int = 10,
        use_cache: bool = True
    ) -> JankReport:
        """分析帧率卡顿"""
        pass

    def analyze_memory(
        self,
        case_id: str,
        include_trend: bool = False,
        use_cache: bool = True
    ) -> MemoryReport:
        """分析内存"""
        pass

    def analyze_module(
        self,
        case_id: str,
        module: str,
        include_hotspots: bool = False
    ) -> ModuleReport:
        """分析特定模块"""
        pass

    def compare_reports(
        self,
        case_id_a: str,
        case_id_b: str,
        delta_threshold: float = 10.0,
        use_cache: bool = True
    ) -> ComparisonReport:
        """对比两个报告"""
        pass
```

### CacheManager (缓存管理器)

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
            ttl: 缓存有效期（秒）
        """
        self.cache_dir = cache_dir
        self.ttl = ttl
        self.stats = {
            "hits": 0,
            "misses": 0,
            "total": 0
        }

    def get(
        self,
        case_id: str,
        data_type: str
    ) -> Optional[dict]:
        """获取缓存数据

        Args:
            case_id: 案例 ID
            data_type: 数据类型 (overview/modules/hotspots/jank/memory)

        Returns:
            缓存数据，如果不存在或过期则返回 None
        """
        # 检查缓存文件
        # 检查是否过期
        # 更新统计
        pass

    def set(
        self,
        case_id: str,
        data_type: str,
        data: dict
    ):
        """保存缓存数据

        Args:
            case_id: 案例 ID
            data_type: 数据类型
            data: 要缓存的数据
        """
        # 创建缓存目录
        # 保存元数据
        # 保存数据
        pass

    def is_expired(
        self,
        case_id: str,
        data_type: str
    ) -> bool:
        """检查缓存是否过期"""
        pass

    def clear(
        self,
        case_id: Optional[str] = None
    ):
        """清除缓存

        Args:
            case_id: 案例 ID，如果为 None 则清除所有缓存
        """
        pass

    def get_stats(self) -> dict:
        """获取缓存统计信息"""
        return {
            "hits": self.stats["hits"],
            "misses": self.stats["misses"],
            "total": self.stats["total"],
            "hit_rate": self.stats["hits"] / self.stats["total"] if self.stats["total"] > 0 else 0
        }
```

### BaseCollector (基础收集器)

```python
class BaseCollector:
    """基础收集器

    支持:
    - 渐进式数据获取（按需加载）
    - 自动缓存管理
    - 错误重试
    """

    def __init__(
        self,
        client: UboxProfilerClient,
        cache_manager: CacheManager
    ):
        """初始化收集器

        Args:
            client: Ubox Profiler API 客户端
            cache_manager: 缓存管理器
        """
        self.client = client
        self.cache = cache_manager

    def collect(
        self,
        case_id: str,
        use_cache: bool = True
    ) -> dict:
        """收集数据（优先使用缓存）

        Args:
            case_id: 案例 ID
            use_cache: 是否使用缓存

        Returns:
            收集的数据
        """
        # 检查缓存
        if use_cache:
            cached = self.cache.get(case_id, self.data_type)
            if cached is not None:
                return cached

        # 从 API 获取
        data = self._fetch_from_api(case_id)

        # 保存到缓存
        self.cache.set(case_id, self.data_type, data)

        return data

    def _fetch_from_api(self, case_id: str) -> dict:
        """从 API 获取数据（子类实现）"""
        raise NotImplementedError

    @property
    def data_type(self) -> str:
        """数据类型（子类实现）"""
        raise NotImplementedError
```

### BasePreprocessor (基础预处理器)

```python
class BasePreprocessor:
    """大数据预处理器

    对于数据量过大的数据:
    1. 先通过脚本进行初步分析
    2. 提取关键统计信息
    3. 生成摘要数据
    4. 然后交给 AI 进行深度分析
    """

    def preprocess(self, raw_data: dict) -> dict:
        """预处理原始数据

        Args:
            raw_data: 原始数据

        Returns:
            摘要数据，包含统计信息和关键样本
        """
        raise NotImplementedError

    def _should_preprocess(self, raw_data: dict) -> bool:
        """判断是否需要预处理"""
        raise NotImplementedError

    def _extract_summary(self, raw_data: dict) -> dict:
        """提取摘要统计"""
        raise NotImplementedError

    def _select_samples(self, raw_data: dict) -> list:
        """选择关键样本"""
        raise NotImplementedError
```

### BaseReporter (基础报告器)

```python
class BaseReporter:
    """基础报告器

    生成 Markdown 格式报告
    """

    def __init__(self, formatter: MarkdownFormatter):
        """初始化报告器

        Args:
            formatter: Markdown 格式化器
        """
        self.formatter = formatter

    def generate(self, analysis_result: dict) -> str:
        """生成 Markdown 报告

        Args:
            analysis_result: 分析结果

        Returns:
            Markdown 格式的报告
        """
        raise NotImplementedError
```

## 数据流设计

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

## 开发计划

| Phase | 周期 | 主要内容 |
|-------|------|----------|
| Phase 1 | Week 1 | 基础框架 + 缓存管理器 |
| Phase 2 | Week 2 | 缓存和数据收集 |
| Phase 3 | Week 3 | 预处理器 |
| Phase 4 | Week 4 | 分析器 |
| Phase 5 | Week 5 | 报告生成器 |
| Phase 6 | Week 6 | 对比功能 |
| Phase 7 | Week 7 | CLI 和集成 |
| Phase 8 | Week 8 | 测试和优化 |

## 技术栈

### 核心依赖
```txt
requests>=2.28.0
pycryptodome>=3.15.0
dataclasses-json>=0.5.9
rich>=13.0.0
click>=8.1.0
tabulate>=0.9.0
python-dateutil>=2.8.2
numpy>=1.24.0
diskcache>=5.6.0
```

## 相关文档

- **[SKILL.md](../SKILL.md)** - Agent Skill 定义文档
- **[数据策略](data-strategy.md)** - 数据获取和缓存策略
- **[使用工作流](workflows.md)** - 完整分析流程
