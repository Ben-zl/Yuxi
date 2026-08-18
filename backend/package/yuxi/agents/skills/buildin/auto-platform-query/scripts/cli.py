#!/usr/bin/env python3
"""
Auto Platform Query CLI

自动化测试平台数据查询工具的主入口
输出格式：JSON（专为 AI Agent 分析设计）
"""

import sys
import io

# 设置标准输出编码为 UTF-8
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='ignore')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='ignore')

import click
from pathlib import Path

# 添加 scripts 目录到路径
SCRIPTS_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPTS_DIR))

# 首先添加 SDK 路径
from utils.config import add_sdk_to_path
add_sdk_to_path()

from utils.config import load_config, validate_config, init_automation_api
from utils.output import print_output, print_json, print_error, print_success
from formatters import get_formatter


@click.group()
@click.option('--base-url', envvar='AUTOMATION_BASE_URL', help='API 基础 URL')
@click.option('--project-id', envvar='AUTOMATION_PROJECT_ID', help='项目 ID')
@click.option('--user-id', envvar='AUTOMATION_USER_ID', help='用户 ID')
@click.option('--timeout', type=int, default=30, help='请求超时时间（秒）')
@click.option('--output-file', type=click.Path(), help='输出到文件')
@click.option('--verbose', is_flag=True, help='显示详细输出')
@click.pass_context
def cli(ctx, base_url, project_id, user_id, timeout, output_file, verbose):
    """
    自动化测试平台查询工具

    输出格式：JSON（专为 AI Agent 分析设计）

    使用 --help 查看各命令的详细帮助
    """
    ctx.ensure_object(dict)

    # 加载配置
    config = load_config(
        base_url=base_url,
        project_id=project_id,
        user_id=user_id,
        timeout=timeout
    )
    ctx.obj['config'] = config
    ctx.obj['output_file'] = output_file
    ctx.obj['verbose'] = verbose

    # 验证配置
    is_valid, error_msg = validate_config(config)
    if not is_valid:
        print_error(error_msg)
        sys.exit(1)

    # 初始化 SDK
    try:
        init_automation_api(config)
    except Exception as e:
        print_error(f"初始化失败: {e}")
        sys.exit(1)

    # 显示详细输出
    if verbose:
        from utils.config import get_config_display
        click.echo(get_config_display(config))


# 导入并注册子命令
try:
    from commands.pipeline import pipeline_cmd
    cli.add_command(pipeline_cmd)
except ImportError:
    pass

try:
    from commands.task import tasks_cmd
    cli.add_command(tasks_cmd)
except ImportError:
    pass

try:
    from commands.device import devices_cmd
    cli.add_command(devices_cmd)
except ImportError:
    pass

try:
    from commands.case import cases_cmd
    cli.add_command(cases_cmd)
except ImportError:
    pass

try:
    from commands.package import packages_cmd
    cli.add_command(packages_cmd)
except ImportError:
    pass

try:
    from commands.build import builds_cmd
    cli.add_command(builds_cmd)
except ImportError:
    pass

try:
    from commands.project import config_cmd
    cli.add_command(config_cmd)
except ImportError:
    pass

try:
    from commands.log import logs_cmd
    cli.add_command(logs_cmd)
except ImportError:
    pass

try:
    from commands.dashboard import dashboard_cmd
    cli.add_command(dashboard_cmd)
except ImportError:
    pass

try:
    from commands.perfeye import perfeye_cmd
    cli.add_command(perfeye_cmd)
except ImportError:
    pass


if __name__ == '__main__':
    cli(obj={})
