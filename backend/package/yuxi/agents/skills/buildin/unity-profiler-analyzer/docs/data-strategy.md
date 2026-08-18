# 数据获取策略 (Data Strategy)

## 概述

本工具采用渐进式数据获取策略，结合本地缓存机制和大数据预处理，优化性能并减少 API 请求。

## 渐进式数据获取

### 设计原则

1. **按需加载**: 只获取当前分析需要的数据
2. **缓存优先**: 优先使用本地缓存，减少 API 请求
3. **分页处理**: 大数据量采用分页获取
4. **后台预取**: 预测可能需要的数据，提前获取

### 分层加载策略

#### 第一层：基础信息（立即加载）

优先级最高，必须立即获取的数据：

| API | 用途 | 触发条件 |
|-----|------|----------|
| `report.get_report_info()` | 获取基础元数据 | 所有分析 |
| `report.get_case_info()` | 获取案例概览 | 所有分析 |

**特点**: 数据量小，响应快，必需

#### 第二层：核心指标（按需加载）

性能概况分析需要的数据：

| API | 用途 | 触发条件 |
|-----|------|----------|
| `cpu.get_cpu_performance()` | CPU 性能数据 | 概况分析 |
| `memory.get_memory_info()` | 内存信息 | 概况分析 |
| `graphic.get_graphic_profile()` | 渲染性能 | 概况分析 |

**特点**: 中等数据量，常用

#### 第三层：详细数据（深度分析时加载）

特定分析需要的数据：

| API | 用途 | 触发条件 |
|-----|------|----------|
| `report.get_fun_top()` | 函数 Top N | 热点分析 |
| `cpu.get_jank_frame()` | 掉帧数据 | 卡顿分析 |
| `hot_module.get_render_module()` | 渲染热点 | 模块分析 |
| `hot_module.get_physics_module()` | 物理热点 | 模块分析 |
| `hot_module.get_animation_module()` | 动画热点 | 模块分析 |

**特点**: 数据量较大，按需加载

#### 第四层：原始数据（需要时加载）

详细分析需要的数据：

| API | 用途 | 触发条件 |
|-----|------|----------|
| `cpu.query_func_performance()` | 具体函数性能 | 深度热点分析 |
| `cpu.get_frame_performance()` | 帧性能详情 | 深度卡顿分析 |
| `cpu.query_funtime_diagram()` | 函数时间图表 | 趋势分析 |

**特点**: 数据量大，分析时按需获取

### 渐进式加载流程

```
用户请求分析
    ↓
检查缓存 (CacheManager)
    ↓
    ├─ 缓存命中 → 直接使用缓存数据
    │                  ↓
    │              返回结果
    │
    └─ 缓存未命中 → 分层加载
                        ↓
                    第一层：基础信息
                        ↓ 加载成功
                    第二层：核心指标
                        ↓ 按需加载
                    第三层：详细数据
                        ↓ 按需加载
                    第四层：原始数据
                        ↓ 保存到缓存
                    返回结果
```

## 本地缓存机制

### CacheManager 设计

**功能**:
- 按案例 ID 分组的缓存存储
- JSON 格式持久化
- 缓存有效期管理（默认 24 小时）
- 缓存命中率统计

**接口**:
```python
class CacheManager:
    def get(self, case_id: str, data_type: str) -> Optional[dict]:
        """获取缓存数据"""

    def set(self, case_id: str, data_type: str, data: dict):
        """保存缓存数据"""

    def is_expired(self, case_id: str, data_type: str) -> bool:
        """检查缓存是否过期"""

    def clear(self, case_id: Optional[str] = None):
        """清除缓存"""

    def get_stats(self) -> dict:
        """获取缓存统计信息"""
```

### 缓存目录结构

```
cache/
└── {case_id}/
    ├── meta.json              # 元数据（创建时间、有效期等）
    ├── overview.json          # 概况数据
    ├── modules.json           # 模块数据
    ├── hotspots.json          # 热点函数数据
    ├── jank.json              # 卡顿数据
    ├── memory.json            # 内存数据
    └── frames/                # 帧数据（大数据）
        ├── frame_100.json
        ├── frame_250.json
        └── ...
```

### 缓存数据格式

**meta.json**:
```json
{
    "case_id": "uuid",
    "created_at": "2026-01-22T10:00:00",
    "expires_at": "2026-01-23T10:00:00",
    "ttl": 86400,
    "version": "1.0"
}
```

**overview.json**:
```json
{
    "case_info": {...},
    "cpu_performance": {...},
    "memory_info": {...},
    "graphic_profile": {...}
}
```

### 缓存策略

**缓存优先策略**:
1. 检查缓存是否存在
2. 检查缓存是否过期
3. 缓存有效：直接使用
4. 缓存无效：从 API 获取并更新缓存

**缓存更新策略**:
- 主动更新：用户指定 `--refresh-cache`
- 自动更新：缓存过期时自动更新
- 智能更新：数据变化检测（未来实现）

**缓存清理策略**:
- 定期清理：清理超过 7 天的缓存
- 手动清理：`--clear-cache` 指定案例
- 全量清理：`--clear-cache all`

## 大数据处理策略

### 判断标准

```python
BIG_DATA_THRESHOLDS = {
    "function_list": 1000,      # 函数列表 > 1000 个
    "jank_frames": 100,         # 掉帧数 > 100 个
    "frame_samples": 50,        # 帧样本 > 50 个
}
```

### 处理流程

```
大数据检测
    ↓
    ├─ 小数据 → 直接分析 → 生成报告
    │
    └─ 大数据 → 预处理脚本 → 摘要数据 → AI 分析 → 生成报告
                      ↓
                  保存摘要到缓存
```

### 预处理器设计

**BasePreprocessor**:
```python
class BasePreprocessor:
    def preprocess(self, raw_data: dict) -> dict:
        """预处理原始数据

        Returns:
            摘要数据，包含统计信息和关键样本
        """
```

**HotspotPreprocessor**:
```python
class HotspotPreprocessor(BasePreprocessor):
    def preprocess(self, raw_data: dict) -> dict:
        """处理热点函数数据

        生成:
        - 多维度 Top N 排行
        - 趋势统计摘要
        - 关键样本数据
        """
```

**JankPreprocessor**:
```python
class JankPreprocessor(BasePreprocessor):
    def preprocess(self, raw_data: dict) -> dict:
        """处理卡顿数据

        生成:
        - 掉帧统计摘要
        - Top 掉帧帧样本
        - 掉帧分布统计
        """
```

### 预处理示例

**热点函数预处理**:
```
原始数据: 5000+ 函数
    ↓ 预处理脚本
摘要数据:
  - Top 100 函数（按多个指标）
  - 按模块分组的 Top 10
  - 趋势统计（耗时分布、调用分布）
  - 关键样本（Top 20 的详细数据）
    ↓ AI 分析
识别性能瓶颈、生成优化建议
```

**卡顿数据预处理**:
```
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

## 性能优化

### 缓存命中率优化

1. **智能预热**: 常用数据预加载
2. **批量获取**: 一次性获取多个相关数据
3. **并行加载**: 多线程并行获取独立数据

### 网络请求优化

1. **请求合并**: 将多个相关请求合并
2. **请求去重**: 避免重复请求相同数据
3. **请求重试**: 失败请求自动重试
4. **超时控制**: 合理设置超时时间

### 内存使用优化

1. **流式处理**: 大数据分块处理
2. **惰性加载**: 需要时才加载数据
3. **数据释放**: 使用后及时释放

## 配置选项

### 缓存配置

```python
CACHE_CONFIG = {
    "enabled": True,           # 是否启用缓存
    "dir": "./cache",          # 缓存目录
    "ttl": 86400,             # 缓存有效期（秒）
    "auto_cleanup": True,      # 自动清理过期缓存
    "cleanup_days": 7,         # 清理 N 天前的缓存
}
```

### 预处理配置

```python
PREPROCESS_CONFIG = {
    "enabled": True,           # 是否启用预处理
    "thresholds": {            # 大数据阈值
        "function_list": 1000,
        "jank_frames": 100,
        "frame_samples": 50,
    },
    "auto_preprocess": True,   # 自动预处理大数据
}
```

### 加载配置

```python
LOADING_CONFIG = {
    "progressive": True,       # 启用渐进式加载
    "parallel": True,          # 启用并行加载
    "max_parallel": 4,         # 最大并行数
    "timeout": 30,             # 请求超时（秒）
}
```

## 使用示例

### 启用/禁用缓存

```bash
# 使用缓存（默认）
python scripts/cli.py analyze <case_id>

# 禁用缓存
python scripts/cli.py analyze <case_id> --no-cache

# 刷新缓存
python scripts/cli.py analyze <case_id> --refresh-cache

# 清理缓存
python scripts/cli.py clear-cache <case_id>
python scripts/cli.py clear-cache all
```

### 预处理模式

```bash
# 自动检测并预处理（默认）
python scripts/cli.py analyze <case_id> --hotspots

# 强制详细模式（禁用预处理）
python scripts/cli.py analyze <case_id> --hotspots --detailed

# 自定义阈值
python scripts/cli.py analyze <case_id> --hotspots --threshold 2000
```

### 代码调用

```python
from analyzer import ProfilerAnalyzer

analyzer = ProfilerAnalyzer(
    base_url="http://10.11.10.173:8080",
    project_id="your_project_id",
    cache_dir="./cache",         # 缓存目录
    cache_ttl=86400,             # 缓存有效期
    enable_cache=True,           # 启用缓存
    enable_preprocess=True       # 启用预处理
)

# 禁用缓存
report = analyzer.analyze_overview(
    case_id="uuid",
    use_cache=False
)

# 强制详细模式
report = analyzer.analyze_hotspots(
    case_id="uuid",
    detailed=True
)
```