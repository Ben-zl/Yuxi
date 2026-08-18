"""
流水线查询命令
"""

import sys
import datetime
import click
import automation_api
from automation_api import (
    AutomationAPIError,
    NetworkError,
    APIException
)


def get_recent_date_range(days=3):
    """获取最近 N 天的日期范围"""
    end_date = datetime.date.today()
    start_date = end_date - datetime.timedelta(days=days - 1)  # N天（包含今天）
    # 返回格式: YYYY-MM-DD HH:mm:ss
    return (start_date.strftime("%Y-%m-%d") + " 00:00:00", end_date.strftime("%Y-%m-%d") + " 23:59:59")


@click.command('pipeline')
@click.option('--platform', type=click.Choice(['Android', 'iOS', 'android', 'ios']),
              help='平台筛选')
@click.option('--creator', help='创建者筛选')
@click.option('--name', 'pipeline_name', help='流水线名称（模糊查询）')
@click.option('--search', 'pipeline_name', help='流水线名称（模糊查询，--name 的别名）')
@click.option('--start-time', help='开始时间 (YYYY-MM-DD 或 YYYY-MM-DD HH:mm:ss)')
@click.option('--end-time', help='结束时间 (YYYY-MM-DD 或 YYYY-MM-DD HH:mm:ss)')
@click.option('--id', 'pipeline_id', type=int, help='流水线 ID（查看详情）')
@click.option('--check-name', help='检查名称是否存在')
@click.option('--trend', nargs=2, type=str,
              help='获取性能趋势 (开始日期 结束日期)')
@click.option('--trend-days', type=int, default=None,
              help='获取最近 N 天的性能趋势，默认 3 天')
@click.option('--power-list', is_flag=True, help='获取流水线电源列表')
@click.option('--output-file', type=click.Path(), help='输出到文件（JSON 格式）')
@click.pass_context
def pipeline_cmd(ctx, platform, creator, pipeline_name, start_time, end_time,
                 pipeline_id, check_name, trend, trend_days, power_list, output_file):
    """
    查询流水线信息

    示例:
        # 列出所有流水线
        python cli.py pipeline

        # 按平台过滤
        python cli.py pipeline --platform Android

        # 查看详情
        python cli.py pipeline --id 123

        # 检查名称是否存在
        python cli.py pipeline --check-name "my_pipeline"

        # 获取性能趋势（默认最近3天）
        python cli.py pipeline --id 123 --trend-days

        # 获取性能趋势（自定义日期范围）
        python cli.py pipeline --id 123 --trend "2024-01-01" "2024-01-31"
        # 使用完整时间格式
        python cli.py pipeline --id 123 --trend "2024-01-01 00:00:00" "2024-01-31 23:59:59"
    """
    # 优先使用子命令的 output_file，如果没有则使用全局的
    output_file = output_file or ctx.obj.get('output_file')
    verbose = ctx.obj['verbose']

    # 标准化平台名称
    if platform:
        platform = platform.capitalize()

    try:
        # 获取格式化器
        from formatters import get_formatter
        formatter = get_formatter()
        from utils.output import print_output, print_error

        # 处理性能趋势选项
        trend_range = None
        if trend_days is not None:
            # --trend-days: 获取最近 N 天的数据（默认 3 天）
            days = trend_days if trend_days > 0 else 3
            trend_range = get_recent_date_range(days)
        elif trend:
            # --trend: 自定义日期范围
            trend_range = (trend[0], trend[1])

        if trend_range:
            # 性能趋势（优先处理）
            if not pipeline_id:
                print_error("使用 --trend 或 --trend-days 需要指定 --id")
                sys.exit(1)
            if verbose:
                click.echo(f"正在获取流水线 {pipeline_id} 的性能趋势...")
            data = automation_api.get_pipeline_performance_trend(
                pipeline_id, trend_range[0], trend_range[1]
            )

            # 获取流水线信息用于元数据
            pipeline_detail = automation_api.get_pipeline_detail(pipeline_id)
            pipeline_name_for_meta = pipeline_detail.get("pipelineName", "") if pipeline_detail else ""

            # 使用新的性能趋势数据格式（包含预分组数据和变化值）
            output = formatter.format_performance_trend_v2(
                data,
                pipeline_id=pipeline_id,
                pipeline_name=pipeline_name_for_meta
            )

        elif pipeline_id:
            # 获取详情
            if verbose:
                click.echo(f"正在获取流水线 {pipeline_id} 的详情...")
            data = automation_api.get_pipeline_detail(pipeline_id)
            output = formatter.format_pipelines(data)

        elif power_list:
            # 获取电源列表
            if verbose:
                click.echo("正在获取流水线电源列表...")
            data = automation_api.get_pipeline_power_list()
            output = formatter.format_pipelines(data)

        else:
            # 列表查询
            if verbose:
                click.echo("正在查询流水线列表...")

            data = automation_api.get_pipelines(
                pipeline_name=pipeline_name,
                platform=platform,
                creator=creator,
                start_time=start_time,
                end_time=end_time
            )
            output = formatter.format_pipelines(data)

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
