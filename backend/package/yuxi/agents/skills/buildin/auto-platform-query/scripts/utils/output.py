"""
输出工具模块
"""

import sys
from typing import Optional
from rich.console import Console
from rich.syntax import Syntax

console = Console()


def print_output(content: str, output_file: Optional[str] = None) -> None:
    """
    输出内容到终端或文件

    Args:
        content: 输出内容
        output_file: 输出文件路径（可选）
    """
    if output_file:
        try:
            with open(output_file, 'w', encoding='utf-8') as f:
                f.write(content)
            console.print(f"[green]✓[/green] 输出到文件: {output_file}")
        except IOError as e:
            console.print(f"[red]✗[/red] 写入文件失败: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        # 直接使用 print，避免 rich.console.print 的自动换行
        print(content)


def print_json(content: str, output_file: Optional[str] = None) -> None:
    """
    输出 JSON 格式的内容（带语法高亮）

    Args:
        content: JSON 字符串
        output_file: 输出文件路径（可选）
    """
    if output_file:
        print_output(content, output_file)
    else:
        try:
            syntax = Syntax(content, "json", theme="monokai", line_numbers=True)
            console.print(syntax)
        except Exception:
            # 如果语法高亮失败，直接输出
            console.print(content)


def print_error(message: str) -> None:
    """
    打印错误信息

    Args:
        message: 错误消息
    """
    from rich.console import Console
    err_console = Console(stderr=True)
    err_console.print(f"[red]✗[/red] 错误: {message}")


def print_success(message: str) -> None:
    """
    打印成功信息

    Args:
        message: 成功消息
    """
    console.print(f"[green]✓[/green] {message}")


def print_warning(message: str) -> None:
    """
    打印警告信息

    Args:
        message: 警告消息
    """
    console.print(f"[yellow]⚠[/yellow] {message}")


def print_info(message: str) -> None:
    """
    打印信息

    Args:
        message: 信息内容
    """
    console.print(f"[blue]ℹ[/blue] {message}")
