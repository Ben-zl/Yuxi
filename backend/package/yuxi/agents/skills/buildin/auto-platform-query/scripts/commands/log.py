"""
日志查询命令
"""

import sys
import click
import automation_api
from automation_api import AutomationAPIError, NetworkError, APIException


@click.command('logs')
@click.option('--build-id', help='构建 ID')
@click.option('--start-time', help='开始时间 (YYYY-MM-DD HH:MM:SS)')
@click.option('--end-time', help='结束时间 (YYYY-MM-DD HH:MM:SS)')
@click.option('--download-urls', is_flag=True, help='获取日志下载链接')
@click.option('--days', type=int, default=7, help='获取最近 N 天的下载链接（默认: 7）')
@click.option('--v2', is_flag=True, help='使用 v2 版本的下载链接 API')
@click.pass_context
def logs_cmd(ctx, build_id, start_time, end_time, download_urls, days, v2):
    """
    查询日志信息

    示例:
        # 查询日志
        python cli.py logs --build-id "123" --start "2024-01-01 00:00:00" --end "2024-01-02 00:00:00"

        # 获取日志下载链接
        python cli.py logs --download-urls --days 7

        # 使用 v2 API
        python cli.py logs --download-urls --days 7 --v2
    """

    output_file = ctx.obj['output_file']
    verbose = ctx.obj['verbose']

    try:
        from formatters import get_formatter
        formatter = get_formatter()
        from utils.output import print_output, print_error

        if download_urls:
            # 获取日志下载链接
            if verbose:
                click.echo(f"正在获取最近 {days} 天的日志下载链接...")

            if v2:
                urls = automation_api.get_log_download_urls_v2(days=days)
            else:
                urls = automation_api.get_log_download_urls(days=days)

            if not urls:
                output = "暂无下载链接"
            else:
                lines = [f"\n=== 日志下载链接（最近 {days} 天）===\n"]
                for i, url in enumerate(urls[:50], 1):
                    lines.append(f"{i}. {url}")
                if len(urls) > 50:
                    lines.append(f"\n... 还有 {len(urls) - 50} 个链接")
                output = "\n".join(lines)

        else:
            # 查询日志
            if not build_id:
                print_error("查询日志需要指定 --build-id")
                sys.exit(1)

            if verbose:
                click.echo(f"正在查询构建 {build_id} 的日志...")

            data = automation_api.query_logs(
                build_id=build_id,
                start_time=start_time,
                end_time=end_time
            )
            output = formatter.format_logs(data)

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
