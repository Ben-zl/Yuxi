"""
Perfeye 查询命令
"""

import sys
import click


@click.command('perfeye')
@click.option('--uuid', 'task_uuid', help='Perfeye 任务 UUID')
@click.option('--metrics-only', is_flag=True, help='仅获取性能指标（FPS、JANK、内存）')
@click.option('--check-connection', is_flag=True, help='检查 Perfeye API 连接')
@click.pass_context
def perfeye_cmd(ctx, task_uuid, metrics_only, check_connection):
    """
    查询 Perfeye 平台的任务数据

    示例:
        # 获取完整任务数据
        python cli.py perfeye --uuid abc123-def456

        # 仅获取性能指标
        python cli.py perfeye --uuid abc123-def456 --metrics-only

        # 检查 API 连接
        python cli.py perfeye --check-connection

    注意：
        Perfeye API 需要有效的任务 UUID 才能获取数据。
        UUID 通常从任务详情的 perfeyeData 字段中获取。
    """
    output_file = ctx.obj.get('output_file')
    verbose = ctx.obj.get('verbose', False)

    try:
        from utils.output import print_output, print_error
        from utils.perfeye_api import get_task_data, get_task_performance_metrics, check_api_connection
        from utils.perfeye_api import PerfeyeAPIError, PerfeyeNetworkError, PerfeyeAuthError
        import json

        if check_connection:
            # 检查连接
            if verbose:
                click.echo("正在检查 Perfeye API 连接...")

            is_connected = check_api_connection()

            if is_connected:
                output = "[OK] Perfeye API 连接正常"
                click.echo(output)
            else:
                output = "[FAILED] Perfeye API 连接失败"
                print_error(output)
                sys.exit(1)

            # 输出结果
            print_output(output, output_file)
            return

        # 验证必需的参数
        if not task_uuid:
            print_error("缺少必需参数: --uuid")
            click.echo("提示: 使用 --help 查看帮助信息")
            sys.exit(1)

        # 获取任务数据
        if verbose:
            if metrics_only:
                click.echo(f"正在获取任务 {task_uuid} 的性能指标...")
            else:
                click.echo(f"正在获取任务 {task_uuid} 的详细数据...")

        if metrics_only:
            # 仅获取性能指标
            data = get_task_performance_metrics(task_uuid)

            # 格式化输出
            output = {
                "type": "perfeye_metrics",
                "uuid": task_uuid,
                # FPS 统计
                "avg_fps": data["avg_fps"],
                "tp90": data["tp90"],
                "tp90_deposit": data["tp90_deposit"],
                "jank_per_10min": data["jank_per_10min"],
                "big_jank_per_10min": data["big_jank_per_10min"],
                "logic_jank_per_10min": data["logic_jank_per_10min"],
                "logic_big_jank_per_10min": data["logic_big_jank_per_10min"],
                # 内存统计
                "avg_memory_mb": data["avg_memory_mb"],
                "peak_memory_mb": data["peak_memory_mb"],
                "peak_memory_deposit_mb": data["peak_memory_deposit_mb"],
                # GPU 统计
                "avg_gpu_load_percent": data["avg_gpu_load_percent"],
                "max_gpu_load_percent": data["max_gpu_load_percent"],
                "avg_gpu_temp": data["avg_gpu_temp"],
                "max_gpu_temp": data["max_gpu_temp"]
            }
        else:
            # 获取完整数据
            data = get_task_data(task_uuid)
            output = {
                "type": "perfeye_task",
                "uuid": task_uuid,
                "data": data
            }

        # 输出 JSON 格式
        output_json = json.dumps(output, ensure_ascii=False, indent=2)
        print_output(output_json, output_file)

    except PerfeyeAuthError as e:
        print_error(f"认证错误: {e}")
        sys.exit(1)
    except PerfeyeNetworkError as e:
        print_error(f"网络错误: {e}")
        sys.exit(1)
    except PerfeyeAPIError as e:
        print_error(f"API 错误: {e}")
        sys.exit(1)
    except Exception as e:
        if verbose:
            import traceback
            traceback.print_exc()
        print_error(f"未知错误: {e}")
        sys.exit(1)
