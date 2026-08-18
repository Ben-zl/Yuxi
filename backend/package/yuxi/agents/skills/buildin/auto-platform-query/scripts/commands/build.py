"""
构建查询命令
"""

import sys
import click
import automation_api
from automation_api import AutomationAPIError, NetworkError, APIException


@click.command('builds')
@click.option('--id', 'build_id', type=int, help='构建 ID（获取构建信息）')
@click.option('--cases', is_flag=True, help='获取构建的用例')
@click.option('--device-executions', is_flag=True, help='获取用例的设备执行情况和性能数据')
@click.option('--device-id', type=int, default=None, help='设备 ID（配合 --device-executions 使用）')
@click.option('--device-case-id', type=int, help='设备用例 ID（获取运行状态）')
@click.option('--case-device-detail', type=int, help='用例设备 ID（获取详情）')
@click.option('--output-file', type=click.Path(), help='输出到文件（JSON 格式）')
@click.pass_context
def builds_cmd(ctx, build_id, cases, device_executions, device_id, device_case_id, case_device_detail, output_file):
    """
    查询构建信息

    示例:
        # 获取构建信息
        python cli.py builds --id 123

        # 获取构建的用例
        python cli.py builds --id 123 --cases

        # 获取用例的设备执行情况和性能数据
        python cli.py builds --id 123 --device-executions

        # 获取特定设备的执行详情
        python cli.py builds --id 123 --device-executions --device-id 513

        # 获取用例运行状态
        python cli.py builds --device-case-id 456

    重要提示：
        ⚡ **支持所有状态的任务**：获取任务详情时会忽略任务的运行状态。
        无论任务是 RUNNING（运行中）、QUEUE（排队中）、CANCEL（已取消）、FAILED（失败）还是 SUCCESS（成功），
        都可以获取任务详情和性能数据。

        查询任务详情（--device-executions）前，请务必阅读 references/TASK_DETAIL.md 文档，
        了解性能指标的含义、判断标准和使用方法。
    """
    # 优先使用子命令的 output_file，如果没有则使用全局的
    output_file = output_file or ctx.obj.get('output_file')
    verbose = ctx.obj['verbose']

    try:
        from formatters import get_formatter
        formatter = get_formatter()
        from utils.output import print_output, print_error

        if device_executions:
            # 获取用例的设备执行情况
            if not build_id:
                print_error("使用 --device-executions 需要指定 --id")
                sys.exit(1)

            if verbose:
                click.echo(f"正在获取构建 {build_id} 的任务详情...")

            # 直接使用 get_task_detail 获取完整任务详情
            task_detail = automation_api.get_task_detail(task_id=build_id)

            if not task_detail or 'caseDetails' not in task_detail:
                output = f"未找到 Build ID {build_id} 的任务详情"
            else:
                # 直接使用 task_detail 中的 caseDetails 数据
                output = formatter.format_device_executions(task_detail, device_id)

        elif device_case_id:
            # 获取用例运行状态
            if verbose:
                click.echo(f"正在获取设备用例 {device_case_id} 的运行状态...")
            data = automation_api.get_case_running_status(device_case_id)
            output = formatter.format_builds(data)

        elif case_device_detail:
            # 获取用例设备详情
            if verbose:
                click.echo(f"正在获取用例设备 {case_device_detail} 的详情...")
            data = automation_api.get_case_device_detail(case_device_detail)
            output = formatter.format_builds(data)

        elif cases:
            # 获取构建的用例
            if not build_id:
                print_error("使用 --cases 需要指定 --id")
                sys.exit(1)

            if verbose:
                click.echo(f"正在获取构建 {build_id} 的用例...")

            data = automation_api.get_build_case(build_id)
            output = formatter.format_cases(data)

        else:
            # 获取构建信息
            if not build_id:
                print_error("请指定 --id")
                sys.exit(1)

            if verbose:
                click.echo(f"正在获取构建 {build_id} 的信息...")

            data = automation_api.get_build_info(build_id)
            output = formatter.format_builds(data)

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
