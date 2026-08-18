# Unity Profiler Performance Analyzer

Unity 游戏性能分析工具 - 基于 `utils/ubox-api` 模块获取 Unity Profiler 数据，进行性能问题分析，使用 AI 生成 Markdown 格式分析报告。

## 功能特性

- **AI 驱动报告生成**: 使用 AI 提示词自动生成专业的分析报告
- **数据收集和缓存**: 按需加载，优先使用本地缓存
- **多维度分析**: 从多个角度分析性能瓶颈（概况、热点函数、卡顿、内存）
- **CLI 数据导出**: 导出 JSON 数据供 AI 处理

## 安装

```bash
# 安装依赖
pip install -r requirements.txt
```

## 配置

### 环境变量

```bash
export UBOX_BASE_URL="http://10.11.10.173:8080"
export UBOX_PROJECT_ID="your_project_id"
export UBOX_CACHE_DIR="./cache"
```

### 配置文件

修改 `scripts/utils/config.py` 中的默认配置。

## 使用方法

### 方式一：通过 AI Agent（推荐）

直接与 AI 对话，请求分析任务：

```
"分析 Unity 性能数据，case_id 是 bf909275-f505-11f0-9e9c-708bcdbcb7b1"

"生成性能概况报告"

"分析热点函数，Top 50"
```

AI 会自动：
1. 获取性能数据
2. 使用相应的提示词模板
3. 生成 Markdown 格式的分析报告

### 方式二：通过 CLI 导出数据

```bash
# 导出性能概况数据为 JSON
python scripts/cli.py analyze <case_id> --overview -o overview.json

# 导出热点函数数据
python scripts/cli.py analyze <case_id> --hotspots --top 20 -o hotspots.json

# 禁用缓存（强制重新获取）
python scripts/cli.py analyze <case_id> --overview --no-cache
```

然后将 JSON 数据提供给 AI Agent 生成报告。

## 项目结构

```
unity-profiler-analyzer/
├── SKILL.md                    # AI Agent Skill 定义（主要文档）
├── README.md                   # 本文件
├── requirements.txt            # 依赖
├── scripts/                    # 核心脚本
│   ├── analyzer.py             # 主分析器
│   ├── cli.py                  # CLI 入口（数据导出）
│   ├── cache/                  # 缓存管理
│   ├── collectors/             # 数据收集器
│   └── utils/                  # 工具函数
├── cache/                      # 缓存数据
└── docs/                       # 模块文档
```

## 相关文档

- **[SKILL.md](SKILL.md)** - AI Agent Skill 定义文档（主要文档，包含 AI 提示词模板）
- **[docs/](docs/)** - 模块详细文档
- **[ubox-api API 文档](../../utils/ubox-api/API.md)** - API 接口文档

## 许可证

MIT License
