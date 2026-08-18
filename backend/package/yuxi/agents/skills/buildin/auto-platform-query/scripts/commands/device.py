"""
设备查询命令
"""

import sys
import click
import automation_api
from automation_api import AutomationAPIError, NetworkError, APIException


@click.command('devices')
@click.option('--status', help='状态筛选 (如: online, offline)')
@click.option('--platform', type=click.Choice(['Android', 'iOS', 'android', 'ios']),
              help='平台筛选')
@click.option('--id', 'device_id', type=int, help='设备 ID（查看详情）')
@click.option('--screenshots', is_flag=True, help='获取设备截图')
@click.option('--screenshot-count', type=int, default=10, help='截图数量（默认: 10）')
@click.option('--pipelines', is_flag=True, help='查看设备关联的流水线')
@click.option('--all-devices', is_flag=True, help='获取所有设备（包括已删除）')
@click.option('--output-file', type=click.Path(), help='输出到文件（JSON 格式）')
@click.pass_context
def devices_cmd(ctx, status, platform, device_id, screenshots,
                screenshot_count, pipelines, all_devices, output_file):
    """
    查询设备信息

    示例:
        # 列出所有设备
        python cli.py devices

        # 只显示在线设备
        python cli.py devices --status online

        # 获取设备截图
        python cli.py devices --id 1 --screenshots --screenshot-count 10

        # 查看设备关联的流水线
        python cli.py devices --id 1 --pipelines
    """

    # 优先使用子命令的 output_file，如果没有则使用全局的
    output_file = output_file or ctx.obj.get('output_file')
    verbose = ctx.obj['verbose']

    # 标准化平台名称
    if platform:
        platform = platform.capitalize()

    try:
        from formatters import get_formatter
        formatter = get_formatter()
        from utils.output import print_output, print_error

        if screenshots:
            # 获取设备截图
            if not device_id:
                print_error("使用 --screenshots 需要指定 --id")
                sys.exit(1)

            if verbose:
                click.echo(f"正在获取设备 {device_id} 的截图...")

            urls = automation_api.get_device_screenshots(
                device_id=device_id,
                count=screenshot_count
            )

            # JSON output removed
            if not urls:
                output = "暂无截图"
            else:
                lines = [f"\n=== 设备 {device_id} 的截图 ===\n"]
                for i, url in enumerate(urls[:screenshot_count], 1):
                    lines.append(f"{i}. {url}")
                output = "\n".join(lines)

        elif pipelines:
            # 获取设备关联的流水线
            if not device_id:
                print_error("使用 --pipelines 需要指定 --id")
                sys.exit(1)

            if verbose:
                click.echo(f"正在获取设备 {device_id} 关联的流水线...")

            data = automation_api.get_device_pipeline_relation(device_id)
            output = formatter.format_pipelines(data)

        elif all_devices:
            # 获取所有设备
            if verbose:
                click.echo("正在获取所有设备（包括已删除）...")

            devices = automation_api.get_all_devices()
            output = formatter.format_devices(devices)

        else:
            # 常规设备列表查询
            if verbose:
                click.echo("正在查询设备列表...")

            devices = automation_api.get_devices()

            # 客户端过滤（如果 API 不支持过滤）
            if status:
                devices = [d for d in devices if d.get('status') == status]
            if platform:
                devices = [d for d in devices if d.get('platform') == platform]

            output = formatter.format_devices(devices)

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
