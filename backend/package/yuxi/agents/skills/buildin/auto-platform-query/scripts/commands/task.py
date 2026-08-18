"""
任务查询命令
"""

import sys
import datetime
import click
import automation_api
from automation_api import AutomationAPIError, NetworkError, APIException


def get_default_time_range():
    """获取默认时间范围（最近1个月）"""
    end_date = datetime.date.today()
    start_date = end_date - datetime.timedelta(days=30)
    return start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")


@click.command('tasks')
@click.option('--status', help='状态筛选 (如: running, completed, failed)')
@click.option('--pipeline-id', type=int, help='流水线 ID 筛选')
@click.option('--device-id', type=int, help='设备 ID 筛选')
@click.option('--build-name', help='构建名称（模糊查询）')
@click.option('--start-time', help='开始时间 (YYYY-MM-DD)，默认 1 个月前')
@click.option('--end-time', help='结束时间 (YYYY-MM-DD)，默认今天')
@click.option('--order-by', help='排序字段 (如: queueTime, startTime)')
@click.option('--count', type=int, default=50, help='返回数量 (默认: 50)')
@click.option('--id', 'task_id', type=int, help='任务 ID（查看详情）')
@click.option('--device-build-id', type=int, help='设备构建 ID（获取详情）')
@click.option('--device-execute-id', type=int, help='设备执行 ID（获取执行信息）')
@click.option('--discover', is_flag=True, help='发现模式：返回精简任务列表用于选择')
@click.option('--stability', is_flag=True, help='稳定性测试模式:返回预处理后的稳定性统计数据（需配合 --id 使用）')
@click.option('--output-file', type=click.Path(), help='输出到文件（JSON 格式）')
@click.pass_context
def tasks_cmd(ctx, status, pipeline_id, device_id, build_name, start_time, end_time,
              order_by, count, task_id, device_build_id, device_execute_id, discover, stability, output_file):
    """
    查询任务信息

    🔥 **核心原则**：
        获取任务详情或性能数据时，不管任务状态如何（RUNNING/QUEUE/CANCEL/FAILED/SUCCESS），
        都必须获取和展示所有相关数据，不得因任务状态而过滤或排除任何数据！

    示例:
        # 列出最近的任务（不指定状态，返回所有状态）
        python cli.py tasks --count 10

        # 按任务名称查询（默认最近1个月）
        python cli.py tasks --build-name "TDR"

        # 按时间范围查询
        python cli.py tasks --start-time "2024-01-01" --end-time "2024-01-31"

        # 按名称和时间范围查询
        python cli.py tasks --build-name "TDR" --start-time "2026-02-01"

        # 列出运行中的任务
        python cli.py tasks --status running

        # 获取任务详情（无论状态如何）
        python cli.py tasks --id 456

        # 输出到文件
        python cli.py tasks --id 456 --output-file result.json

        # 限制返回数量
        python cli.py tasks --count 10

    ⚡ **重要提示**：
        - 🔥 **核心原则**：获取任务详情时，必须包含所有状态的任务数据
        - 默认情况下（不指定 --status），会返回所有状态的任务
        - 使用 --status 参数可以筛选特定状态的任务（仅用于查看特定状态，不代表其他状态不重要）
        - 获取任务详情时，会忽略任务的运行状态，支持所有状态（RUNNING、QUEUE、CANCEL、FAILED、SUCCESS）
        - 使用 --output-file 将结果保存到文件（JSON 格式）
        - 未指定时间范围时，默认查询最近1个月的任务

    ❌ **严禁行为**：
        - 禁止因任务状态而过滤或排除任何数据
        - 禁止认为失败/取消任务的数据不重要
        - 禁止在性能分析时排除任何状态的任务
    """

    # 优先使用子命令的 output_file，如果没有则使用全局的
    output_file = output_file or ctx.obj.get('output_file')
    verbose = ctx.obj['verbose']

    try:
        from formatters import get_formatter
        formatter = get_formatter()
        from utils.output import print_output, print_error

        if task_id:
            # 获取任务详情
            if verbose:
                mode = "稳定性测试模式" if stability else "标准模式"
                click.echo(f"正在获取任务 {task_id} 的详情（{mode})...")
            data = automation_api.get_task_detail(task_id)

            if stability:
                # 稳定性测试模式：使用专用格式化器，不包含 case 详情
                output = formatter.format_stability_task(data)
            else:
                # 标准模式：包含完整 case 详情
                if isinstance(data, dict):
                    data = {'data': {'list': [data], 'count': 1}}
                output = formatter.format_tasks(data)

        elif device_build_id:
            # 获取设备构建详情
            if verbose:
                click.echo(f"正在获取设备构建 {device_build_id} 的详情...")
            data = automation_api.get_device_build_detail(device_build_id)
            output = formatter.format_builds(data)

        elif device_execute_id:
            # 获取设备执行信息
            if verbose:
                click.echo(f"正在获取设备执行 {device_execute_id} 的信息...")
            data = automation_api.get_device_execute_info(device_execute_id)
            output = formatter.format_tasks(data)

        else:
            # 列表查询
            if verbose:
                mode = "发现模式" if discover else "标准模式"
                click.echo(f"正在查询任务列表（{mode}）...")

            # 设置默认时间范围（1个月）
            if not start_time and not end_time:
                start_time, end_time = get_default_time_range()
                if verbose:
                    click.echo(f"使用默认时间范围: {start_time} 到 {end_time}")

            # 构建过滤条件
            filters = {}
            if status:
                filters['status'] = status
            if pipeline_id:
                filters['pipelineId'] = pipeline_id
            if device_id:
                filters['deviceId'] = device_id
            if build_name:
                filters['buildName'] = build_name
            if start_time:
                filters['startTime'] = f"{start_time} 00:00:00"
            if end_time:
                filters['endTime'] = f"{end_time} 23:59:59"

            # 构建请求参数，只传递非 None 的值
            kwargs = {'filters': filters, 'count': count}
            if order_by is not None:
                kwargs['order_by'] = order_by

            data = automation_api.get_tasks(**kwargs)

            # 根据模式选择格式化方法
            if discover:
                # 发现模式：返回精简列表
                output = formatter.format_task_discovery(
                    data, build_name or "", start_time, end_time
                )
            else:
                # 标准模式：返回完整数据
                output = formatter.format_tasks(data)

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
