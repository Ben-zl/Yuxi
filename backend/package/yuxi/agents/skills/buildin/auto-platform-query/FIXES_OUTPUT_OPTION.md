# auto-platform-query Skill 修复记录

## 问题描述

在使用 auto-platform-query skill 时，出现以下错误：
```
Error: No such option: --output-file
```

经过分析，发现代码和文档中存在对不存在的 `--output` 参数的引用，导致用户混淆。

## 问题根源

1. **CLI 只定义了 `--output-file` 参数**（在 `scripts/cli.py` 第31行）
   ```python
   @click.option('--output-file', type=click.Path(), help='输出到文件')
   ```

2. **代码和文档中错误地使用了 `--output` 参数**，例如：
   - `scripts/commands/task.py`: `python cli.py tasks --id 456 --output table`
   - `scripts/commands/package.py`: `python cli.py packages --id 123 --output table`
   - `references/PERFORMANCE_COMPARISON.md`: `python scripts/cli.py tasks --pipeline-id 932 --count 2 --output json`

## 修复方案

### 1. 修复代码注释

#### `scripts/commands/task.py` (第25-52行)

**修复前：**
```python
"""
查询任务信息

示例:
    # 获取任务详情
    python cli.py tasks --id 456 --output table
"""
```

**修复后：**
```python
"""
查询任务信息

示例:
    # 获取任务详情
    python cli.py tasks --id 456

    # 输出到文件
    python cli.py tasks --id 456 --output-file result.json

⚡ **重要提示**：
    ...
    - 使用 --output-file 将结果保存到文件（JSON 格式）
"""
```

#### `scripts/commands/package.py` (第18-36行)

**修复前：**
```python
"""
查询包体信息

示例:
    # 获取包体详情
    python cli.py packages --id 123 --output table
"""
```

**修复后：**
```python
"""
查询包体信息

示例:
    # 获取包体详情
    python cli.py packages --id 123

    # 输出到文件
    python cli.py packages --id 123 --output-file result.json
"""
```

### 2. 修复参考文档

#### `references/PERFORMANCE_COMPARISON.md`

**修复前（第705行）：**
```bash
# 或者获取 JSON 格式（可选）
python scripts/cli.py tasks --pipeline-id 932 --count 2 --output json
```

**修复后：**
```bash
# 或者保存到文件（JSON 格式）
python scripts/cli.py tasks --pipeline-id 932 --count 2 --output-file performance_data.json
```

**修复前（第737行）：**
```bash
# 或者获取最近 5 次执行
python scripts/cli.py tasks --pipeline-id 932 --count 5 --output json
```

**修复后：**
```bash
# 或者获取最近 5 次执行并保存到文件
python scripts/cli.py tasks --pipeline-id 932 --count 5 --output-file performance_data.json
```

## 正确的使用方式

### 输出到控制台（默认，Markdown 格式）

```bash
# 查询任务列表
python scripts/cli.py tasks --pipeline-id 932 --count 10

# 查询任务详情
python scripts/cli.py tasks --id 456
```

### 输出到文件（JSON 格式）

```bash
# 保存到文件
python scripts/cli.py tasks --id 456 --output-file result.json

# 查询并保存
python scripts/cli.py tasks --pipeline-id 932 --count 10 --output-file tasks.json
```

## 修复的文件列表

1. ✅ `scripts/commands/task.py` - 修复命令示例
2. ✅ `scripts/commands/package.py` - 修复命令示例
3. ✅ `references/PERFORMANCE_COMPARISON.md` - 修复文档中的命令示例

## 验证方法

所有修复已通过 grep 验证，确保没有遗漏的 `--output json` 或 `--output table` 引用：

```bash
# 验证没有残留的错误参数
grep -r "--output json" auto-platform-query/
grep -r "--output table" auto-platform-query/
# 结果：No matches found ✅
```

## 说明

- `--output-file` 是唯一支持的输出选项，用于将结果保存到文件
- 默认输出格式为 JSON（专为 AI Agent 分析设计）
- 不支持 `--output` 参数，已移除所有相关引用
- 所有命令都支持通过 context 继承 `--output-file` 选项

---

## 2026-02-11 更新：添加子命令级别的 --output-file 选项

### 问题描述

用户尝试在子命令后使用 `--output-file` 选项时报错：
```bash
python cli.py tasks --pipeline-id 947 --count 2 --output-file result.json
# Error: No such option: --output-file
```

### 问题原因

`--output-file` 选项只在主 `cli` 命令组上定义，但用户通常在子命令（如 `tasks`、`builds`）后面使用它。根据 Click 的设计，子命令无法直接访问父命令后面传递的选项。

### 解决方案

在每个可能需要输出文件的子命令中添加 `--output-file` 选项，并优先使用子命令的选项。

### 修改的文件

1. `scripts/commands/task.py` - 添加 `--output-file` 选项到 `tasks` 命令
2. `scripts/commands/build.py` - 添加 `--output-file` 选项到 `builds` 命令
3. `scripts/commands/pipeline.py` - 添加 `--output-file` 选项到 `pipeline` 命令
4. `scripts/commands/device.py` - 添加 `--output-file` 选项到 `devices` 命令
5. `scripts/commands/case.py` - 添加 `--output-file` 选项到 `cases` 命令

### 修改内容

在每个命令文件中：

1. 在装饰器中添加 `--output-file` 选项：
```python
@click.option('--output-file', type=click.Path(), help='输出到文件（JSON 格式）')
```

2. 在函数参数中添加 `output_file` 参数

3. 修改 output_file 的获取逻辑，优先使用子命令的选项：
```python
# 优先使用子命令的 output_file，如果没有则使用全局的
output_file = output_file or ctx.obj.get('output_file')
```

### 使用方式

现在 `--output-file` 选项可以在子命令后直接使用：

#### 方式 1（推荐）：选项放在子命令后面
```bash
python cli.py tasks --count 10 --output-file result.json
python cli.py builds --id 125964 --device-executions --output-file task_detail.json
python cli.py pipeline --output-file pipelines.json
```

#### 方式 2：选项放在主命令后面（仍然支持）
```bash
python cli.py --output-file result.json tasks --count 10
```

如果两种方式同时使用，子命令的 `--output-file` 优先级更高。

### 验证

验证选项是否可用：
```bash
python cli.py tasks --help | grep output-file
# 输出：--output-file PATH           输出到文件（JSON 格式）
```
