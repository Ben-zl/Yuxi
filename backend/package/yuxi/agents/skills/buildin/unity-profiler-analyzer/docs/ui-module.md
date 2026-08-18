# UI 模块分析 (UI Module Analysis)

## 模块定位

分析 UI 系统性能，识别 UI 瓶颈。

## 分析内容

### UI 重绘次数
- Canvas 重绘次数
- 重绘频率

### Canvas 性能
- Canvas 数量
- 活动 Canvas 数量
- Canvas 嵌套层级

### UI 热点函数识别
- UI 布局相关热点函数
- UI 渲染相关热点函数

## 数据获取方式

### 使用命令行工具

UI 模块分析可以通过热点函数分析结合模块过滤来实现：

```bash
# 获取热点函数数据（用于筛选 UI 模块）
python scripts/cli.py analyze <case_id> --hotspots --top 50

# 保存到文件后，使用 AI Agent 筛选 UI 模块相关函数
python scripts/cli.py analyze <case_id> --hotspots --top 50 -o ui_data.json

# 完整示例
python scripts/cli.py analyze bf909275-f505-11f0-9e9c-708bcdbcb7b1 \
  --hotspots \
  --top 50 \
  --project pcavpt6w \
  -o ui_data.json
```

**说明**: 通过 `--hotspots` 获取所有热点函数后，使用 AI Agent 筛选出 UI 相关函数（如 EventSystem、Canvas、Layout 等）。

## 输出报告模板

```markdown
## 🖼️ UI 模块深入分析

### UI 性能概览

{ui_overview}

### UI 重绘分析

#### 重绘统计
- **Canvas 重绘次数**: {canvas_redraws}
- **重绘频率**: {redraw_frequency}

### Canvas 性能

#### Canvas 统计
- **Canvas 总数**: {total_canvases}
- **活动 Canvas**: {active_canvases}

### UI 热点函数 Top 10

{ui_hotspots}

### 优化建议

1. {suggestion_1}
2. {suggestion_2}
```

## 使用示例

### 命令行工具使用

```bash
# 获取热点函数数据（用于筛选 UI 模块）
python scripts/cli.py analyze <case_id> --hotspots --top 50 -o ui_data.json

# 完整示例
python scripts/cli.py analyze bf909275-f505-11f0-9e9c-708bcdbcb7b1 \
  --hotspots \
  --top 50 \
  --project pcavpt6w \
  -o ui_data.json
```
