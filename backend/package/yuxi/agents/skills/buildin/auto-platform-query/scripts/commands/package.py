"""
包体查询命令
"""

import sys
import click
import automation_api
from automation_api import AutomationAPIError, NetworkError, APIException


@click.command('packages')
@click.option('--platform', type=click.Choice(['Android', 'iOS', 'android', 'ios']),
              help='平台筛选')
@click.option('--branch', help='分支筛选')
@click.option('--id', 'package_id', type=int, help='包体 ID（查看详情）')
@click.pass_context
def packages_cmd(ctx, platform, branch, package_id):
    """
    查询包体信息

    示例:
        # 列出所有包体
        python cli.py packages

        # 按平台过滤
        python cli.py packages --platform Android

        # 按分支过滤
        python cli.py packages --branch master

        # 获取包体详情
        python cli.py packages --id 123

        # 输出到文件
        python cli.py packages --id 123 --output-file result.json
    """

    output_file = ctx.obj['output_file']
    verbose = ctx.obj['verbose']

    # 标准化平台名称
    if platform:
        platform = platform.capitalize()

    try:
        from formatters import get_formatter
        formatter = get_formatter()
        from utils.output import print_output, print_error

        if package_id:
            # 获取包体详情
            if verbose:
                click.echo(f"正在获取包体 {package_id} 的详情...")
            data = automation_api.get_package_detail(package_id)
            output = formatter.format_packages(data)

        else:
            # 列表查询
            if verbose:
                click.echo("正在查询包体列表...")

            data = automation_api.get_packages(
                platform=platform,
                branch=branch
            )
            output = formatter.format_packages(data)

        # 输出结果
        print_output(output, output_file)

    except NetworkError as e:
        print_error(f"网络错误: {e}")
        sys.exit(1)
    except APIException as e:
        print_error(f"API 错误: {e}")
        sys.exit(1)
    except AutomationAPIError as e:
        print_error(f"错误: {e}")
        sys.exit(1)
    except Exception as e:
        if verbose:
            import traceback
            traceback.print_exc()
        print_error(f"未知错误: {e}")
        sys.exit(1)
