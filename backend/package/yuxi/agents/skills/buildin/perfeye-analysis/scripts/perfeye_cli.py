#!/usr/bin/env python3
"""
Perfye CLI - Perfye 平台数据查询工具

使用示例:
    # 获取性能指标
    python perfeye_cli.py --uuid abc123-def456 --metrics

    # 获取完整数据
    python perfeye_cli.py --uuid abc123-def456 --full

    # 检查连接
    python perfeye_cli.py --check
"""

import sys
import click
import json
from pathlib import Path

# 添加 scripts 目录到路径
SCRIPTS_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPTS_DIR))

from perfeye_api import (
    get_task_data,
    get_task_performance_metrics,
    check_api_connection,
    PerfeyeAPIError,
    PerfeyeNetworkError,
    PerfeyeAuthError
)


@click.command()
@click.option('--uuid', 'task_uuid', help='Perfye 任务 UUID')
@click.option('--metrics', is_flag=True, help='仅获取性能指标（从 LabelInfo.All 统计）')
@click.option('--full', is_flag=True, help='获取完整数据（包括原始数据点）')
@click.option('--check', is_flag=True, help='检查 Perfye API 连接')
@click.option('--output', '-o', type=click.Path(), help='输出到文件')
@click.option('--pretty', is_flag=True, help='格式化 JSON 输出')
def perfeye_cli(task_uuid, metrics, full, check, output, pretty):
    """Perfye 平台性能数据查询工具"""

    try:
        if check:
            # 检查连接
            click.echo("检查 Perfye API 连接...")
            is_connected = check_api_connection()

            if is_connected:
                click.echo("[OK] Perfye API 连接正常")
                return 0
            else:
                click.echo("[FAILED] Perfye API 连接失败", err=True)
                return 1

        # 验证必需参数
        if not task_uuid:
            click.echo("错误: 缺少必需参数 --uuid", err=True)
            click.echo("使用 --help 查看帮助信息")
            return 1

        # 获取数据
        if metrics:
            # 获取性能指标
            click.echo(f"正在获取任务 {task_uuid} 的性能指标...")
            data = get_task_performance_metrics(task_uuid)

            # 移除 raw_data 以保持输出简洁
            if 'raw_data' in data:
                del data['raw_data']

            result = {
                "type": "perfeye_metrics",
                "uuid": task_uuid,
                **data
            }
        elif full:
            # 获取完整数据
            click.echo(f"正在获取任务 {task_uuid} 的完整数据...")
            task_data = get_task_data(task_uuid)

            result = {
                "type": "perfeye_task",
                "uuid": task_uuid,
                "data": task_data
            }
        else:
            # 默认获取性能指标
            click.echo(f"正在获取任务 {task_uuid} 的性能指标...")
            data = get_task_performance_metrics(task_uuid)

            if 'raw_data' in data:
                del data['raw_data']

            result = {
                "type": "perfeye_metrics",
                "uuid": task_uuid,
                **data
            }

        # 输出 JSON
        if pretty:
            json_output = json.dumps(result, ensure_ascii=False, indent=2)
        else:
            json_output = json.dumps(result, ensure_ascii=False)

        if output:
            # 写入文件
            with open(output, 'w', encoding='utf-8') as f:
                f.write(json_output)
            click.echo(f"结果已保存到: {output}")
        else:
            # 输出到控制台
            click.echo(json_output)

        return 0

    except PerfeyeAuthError as e:
        click.echo(f"认证错误: {e}", err=True)
        return 1
    except PerfeyeNetworkError as e:
        click.echo(f"网络错误: {e}", err=True)
        return 1
    except PerfeyeAPIError as e:
        click.echo(f"API 错误: {e}", err=True)
        return 1
    except Exception as e:
        if '--verbose' in sys.argv or '-v' in sys.argv:
            import traceback
            traceback.print_exc()
        click.echo(f"未知错误: {e}", err=True)
        return 1


if __name__ == '__main__':
    perfeye_cli()
