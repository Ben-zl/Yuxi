"""
监控仪表板命令
"""

import sys
import time
import click
import automation_api
from automation_api import AutomationAPIError, NetworkError, APIException
from collections import Counter
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.layout import Layout
from rich.live import Live
from rich.align import Align

console = Console()


@click.command('dashboard')
@click.option('--watch', type=int, help='自动刷新间隔（秒），启用监控模式')
@click.pass_context
def dashboard_cmd(ctx, watch):
    """
    监控仪表板 - 综合展示平台状态

    示例:
        # 显示一次性仪表板
        python cli.py dashboard

        # 每 10 秒刷新一次
        python cli.py dashboard --watch 10
    """
    verbose = ctx.obj.get('verbose', False)

    def fetch_dashboard_data():
        """获取仪表板数据"""
        try:
            # 并行获取各类数据
            pipelines = automation_api.get_pipelines()
            devices = automation_api.get_devices()
            tasks_result = automation_api.get_tasks(count=100)

            # 提取任务列表
            if isinstance(tasks_result, dict):
                tasks = tasks_result.get('data', {}).get('list', [])
                total_tasks = tasks_result.get('data', {}).get('count', 0)
            else:
                tasks = []
                total_tasks = 0

            return {
                'pipelines': pipelines,
                'devices': devices,
                'tasks': tasks,
                'total_tasks': total_tasks
            }
        except Exception as e:
            console.print(f"[red]获取数据失败: {e}[/red]")
            return None

    def render_dashboard(data):
        """渲染仪表板"""
        if not data:
            return Panel("[red]无法获取数据[/red]", title="监控仪表板", border_style="red")

        pipelines = data['pipelines']
        devices = data['devices']
        tasks = data['tasks']
        total_tasks = data['total_tasks']

        # 创建布局
        layout = Layout()
        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="body", ratio=1),
        )

        # 标题
        header = "🚀 自动化测试平台监控仪表板"
        layout["header"].update(Align.center(header))

        # 统计卡片
        stats_table = Table(show_header=False, show_edge=False, padding=(0, 2))
        stats_table.add_column("Metric", style="cyan")
        stats_table.add_column("Value", style="green")

        # 流水线统计
        pipeline_platforms = Counter(p.get('platform', 'Unknown') for p in pipelines)
        pipeline_statuses = Counter(p.get('status', 'Unknown') for p in pipelines)

        stats_table.add_row("流水线总数", str(len(pipelines)))
        for platform, count in pipeline_platforms.most_common():
            stats_table.add_row(f"  - {platform}", str(count))

        # 设备统计
        device_statuses = Counter(d.get('status', 'Unknown') for d in devices)
        online_devices = device_statuses.get('online', 0)

        stats_table.add_row("\n设备总数", str(len(devices)))
        stats_table.add_row("  - 在线", f"[green]{online_devices}[/green]")
        stats_table.add_row("  - 离线", str(device_statuses.get('offline', 0)))

        # 任务统计
        task_statuses = Counter(t.get('status', 'Unknown') for t in tasks)

        stats_table.add_row("\n显示任务数", str(len(tasks)))
        stats_table.add_row("任务总数", str(total_tasks))
        for status, count in task_statuses.most_common(5):
            color = "green" if status == "running" else "yellow"
            stats_table.add_row(f"  - {status}", f"[{color}]{count}[/{color}]")

        # 运行中的任务
        running_tasks_table = Table(title="运行中的任务", show_header=True, header_style="bold magenta")
        running_tasks_table.add_column("ID", style="dim")
        running_tasks_table.add_column("流水线")
        running_tasks_table.add_column("设备")
        running_tasks_table.add_column("状态")

        for task in tasks[:10]:
            if task.get('status') == 'running':
                running_tasks_table.add_row(
                    str(task.get('id', '')),
                    task.get('pipelineName', '')[:25],
                    task.get('deviceName', '')[:15],
                    "[green]运行中[/green]"
                )

        # 在线设备
        online_devices_table = Table(title="在线设备", show_header=True, header_style="bold cyan")
        online_devices_table.add_column("名称")
        online_devices_table.add_column("平台")
        online_devices_table.add_column("状态")

        for device in devices:
            if device.get('status') == 'online':
                online_devices_table.add_row(
                    device.get('name', '')[:20],
                    device.get('platform', ''),
                    "[green]在线[/green]"
                )

        # 组合布局
        layout["body"].split_row(
            Layout(Panel(stats_table, title="统计概览", border_style="blue")),
            Layout().split_column(
                Layout(Panel(running_tasks_table, border_style="magenta")),
                Layout(Panel(online_devices_table, border_style="cyan"))
            )
        )

        return layout

    if watch:
        # 监控模式 - 自动刷新
        with Live(render_dashboard(fetch_dashboard_data()), console=console, refresh_per_second=10) as live:
            try:
                while True:
                    data = fetch_dashboard_data()
                    if data:
                        live.update(render_dashboard(data))
                    time.sleep(watch)
            except KeyboardInterrupt:
                console.print("\n[yellow]监控已停止[/yellow]")
    else:
        # 单次显示
        data = fetch_dashboard_data()
        if data:
            console.print(render_dashboard(data))


def render_simple_dashboard(data):
    """简化版仪表板（当 Rich 不可用时）"""
    pipelines = data['pipelines']
    devices = data['devices']
    tasks = data['tasks']
    total_tasks = data['total_tasks']

    lines = [
        "\n" + "=" * 80,
        "自动化测试平台监控仪表板".center(80),
        "=" * 80,
        "",
        "【统计概览】",
        f"流水线总数: {len(pipelines)}",
        f"设备总数: {len(devices)}",
        f"任务总数: {total_tasks}",
        "",
        "【运行中的任务】"
    ]

    for task in tasks[:10]:
        if task.get('status') == 'running':
            lines.append(
                f"  - [{task.get('id')}] {task.get('pipelineName', '')[:30]} "
                f"on {task.get('deviceName', '')[:15]}"
            )

    lines.extend([
        "",
        "【在线设备】"
    ])

    for device in devices[:10]:
        if device.get('status') == 'online':
            lines.append(f"  - [{device.get('platform')}] {device.get('name', '')[:30]}")

    lines.append("=" * 80)

    for line in lines:
        click.echo(line)
