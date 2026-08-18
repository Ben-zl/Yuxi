"""
用例查询命令
"""

import sys
import click
import automation_api
from automation_api import AutomationAPIError, NetworkError, APIException


@click.command('cases')
@click.option('--id', 'case_id', type=int, help='用例 ID')
@click.option('--pipelines', is_flag=True, help='获取用例关联的流水线')
@click.option('--deleted', is_flag=True, help='获取已删除的用例')
@click.option('--output-file', type=click.Path(), help='输出到文件（JSON 格式）')
@click.pass_context
def cases_cmd(ctx, case_id, pipelines, deleted, output_file):
    """
    查询用例信息

    示例:
        # 列出所有用例
        python cli.py cases

        # 获取用例关联的流水线
        python cli.py cases --id 1 --pipelines

        # 查看已删除用例
        python cli.py cases --deleted
    """

    # 优先使用子命令的 output_file，如果没有则使用全局的
    output_file = output_file or ctx.obj.get('output_file')
    verbose = ctx.obj['verbose']

    try:
        from formatters import get_formatter
        formatter = get_formatter()
        from utils.output import print_output, print_error

        if deleted:
            # 获取已删除用例
            if verbose:
                click.echo("正在获取已删除的用例...")
            data = automation_api.get_deleted_cases()
            output = formatter.format_cases(data)

        elif pipelines:
            # 获取用例关联的流水线
            if not case_id:
                print_error("使用 --pipelines 需要指定 --id")
                sys.exit(1)

            if verbose:
                click.echo(f"正在获取用例 {case_id} 关联的流水线...")

            data = automation_api.get_case_linked_pipelines(case_id)
            output = formatter.format_pipelines(data)

        else:
            # 获取所有用例
            if verbose:
                click.echo("正在查询用例列表...")

            data = automation_api.get_cases()
            output = formatter.format_cases(data)

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
