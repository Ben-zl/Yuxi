# Auto Platform Query - 功能新增记录

## 2026-02-02: 封装 Perfeye API 接口

### 功能描述

新增 Perfeye 平台 API 接口封装，用于直接从 Perfeye 平台获取性能数据。

### API 信息

- **URL**: `http://perfeye.console.testplus.cn/api/show/task/{uuid}`
- **Method**: POST
- **Headers**: `Authorization: Bearer mj6cltF&!L#yWX8k`

### 新增文件

#### 1. scripts/utils/perfeye_api.py

**功能**：Perfeye API 封装模块

**核心函数**：
- `get_task_data(uuid)` - 获取任务的完整数据
- `get_task_performance_metrics(uuid)` - 仅获取性能指标（FPS、JANK、内存）
- `check_api_connection()` - 检查 API 连接状态

**异常类**：
- `PerfeyeAPIError` - API 错误基类
- `PerfeyeNetworkError` - 网络错误
- `PerfeyeAuthError` - 认证错误

**配置**：
```python
PERFEYE_API_CONFIG = {
    "BASE_URL": "http://perfeye.console.testplus.cn",
    "TOKEN": "Bearer mj6cltF&!L#yWX8k",
    "TIMEOUT": 30
}
```

#### 2. scripts/commands/perfeye.py

**功能**：CLI 命令接口

**命令选项**：
- `--uuid TEXT` - Perfeye 任务 UUID（必需）
- `--metrics-only` - 仅获取性能指标
- `--check-connection` - 检查 API 连接

**使用示例**：
```bash
# 获取完整任务数据
python cli.py perfeye --uuid abc123-def456

# 仅获取性能指标
python cli.py perfeye --uuid abc123-def456 --metrics-only

# 检查 API 连接
python cli.py perfeye --check-connection
```

**输出格式**：JSON（所有输出都是 JSON 格式）

### 修改文件

#### 1. scripts/cli.py

**修改内容**：注册 perfeye 命令

```python
try:
    from commands.perfeye import perfeye_cmd
    cli.add_command(perfeye_cmd)
except ImportError:
    pass
```

#### 2. requirements.txt

**修改内容**：添加 requests 依赖

```
requests>=2.31.0
```

### 输出格式

#### perfeye_metrics（仅性能指标）

```json
{
  "type": "perfeye_metrics",
  "uuid": "abc123-def456",
  "fps_tp90": 60.5,
  "jank_per_10min": 5.2,
  "peak_memory_mb": 7500.0
}
```

#### perfeye_task（完整数据）

```json
{
  "type": "perfeye_task",
  "uuid": "abc123-def456",
  "data": {
    // API 返回的完整数据
  }
}
```

### 测试验证

#### 连接测试

```bash
$ python scripts/cli.py --verbose perfeye --check-connection
=== 当前配置 ===
API URL: https://automation-api.testplus.cn
Project ID: starsand...
User ID: starsand...
Timeout: 30s
Max Retries: 3
正在检查 Perfeye API 连接...
[OK] Perfeye API 连接正常
```

### 使用场景

1. **获取性能指标**：从 Perfeye 平台直接获取 FPS、JANK、内存等性能数据
2. **验证数据**：对比自动化平台和 Perfeye 平台的数据
3. **详细分析**：获取 Perfeye 平台的完整性能分析报告

### 技术要点

1. **错误处理**：
   - 网络错误：超时、连接失败
   - 认证错误：Token 无效
   - API 错误：其他 API 返回错误

2. **数据提取**：
   - 自动从 API 返回数据中提取 FPS、JANK、内存指标
   - 支持多种字段名称（fps_tp90 / LabelFPS.TP90）

3. **JSON 输出**：
   - 所有输出都是 JSON 格式
   - 便于 AI Agent 直接解析和分析

### 后续优化

1. **配置化**：将 Token 和 URL 配置化，支持环境变量
2. **缓存**：添加数据缓存功能，减少重复请求
3. **批量查询**：支持批量 UUID 查询
4. **数据验证**：添加返回数据的格式验证

---

**新增完成时间**: 2026-02-02
**新增人**: AI Agent
**相关文件**:
- `scripts/utils/perfeye_api.py`（新建）
- `scripts/commands/perfeye.py`（新建）
- `scripts/cli.py`（修改）
- `requirements.txt`（修改）
