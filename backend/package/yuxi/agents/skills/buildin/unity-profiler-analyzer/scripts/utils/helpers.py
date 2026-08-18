"""Unity Profiler Analyzer - Helper Functions"""

from typing import Any


def format_number(value: Any, decimals: int = 2) -> str:
    """Format a number for display"""
    if value is None:
        return "N/A"
    try:
        if isinstance(value, float):
            return f"{value:.{decimals}f}"
        return str(value)
    except (TypeError, ValueError):
        return str(value)


def format_percentage(value: Any, decimals: int = 1) -> str:
    """Format a value as percentage"""
    if value is None:
        return "N/A"
    try:
        pct = float(value) * 100
        return f"{pct:.{decimals}f}%"
    except (TypeError, ValueError):
        return str(value)


def format_time(value: Any, decimals: int = 2) -> str:
    """Format a time value in milliseconds"""
    if value is None:
        return "N/A"
    try:
        return f"{float(value):.{decimals}f} ms"
    except (TypeError, ValueError):
        return str(value)


def get_fps_status(fps: float) -> tuple[str, str]:
    """Get FPS status evaluation

    Returns:
        (status_text, status_icon)
    """
    try:
        from ..utils.thresholds import Thresholds
    except (ImportError, ValueError):
        from utils.thresholds import Thresholds
    thresholds = Thresholds()

    if fps >= thresholds.fps_excellent:
        return "优秀", "✅"
    elif fps >= thresholds.fps_good:
        return "良好", "✅"
    elif fps >= thresholds.fps_acceptable:
        return "可接受", "⚠️"
    elif fps >= thresholds.fps_poor:
        return "较差", "⚠️"
    else:
        return "极差", "🔴"


def get_memory_status(memory_mb: float, is_mono: bool = False) -> tuple[str, str]:
    """Get memory status evaluation

    Args:
        memory_mb: Memory value in MB
        is_mono: Whether this is Mono heap memory

    Returns:
        (status_text, status_icon)
    """
    try:
        from ..utils.thresholds import Thresholds
    except (ImportError, ValueError):
        from utils.thresholds import Thresholds
    thresholds = Thresholds()

    if is_mono:
        if memory_mb < thresholds.memory_mono_warning:
            return "正常", "✅"
        elif memory_mb < thresholds.memory_mono_critical:
            return "警告", "⚠️"
        else:
            return "严重", "🔴"
    else:
        if memory_mb < thresholds.memory_total_warning:
            return "正常", "✅"
        elif memory_mb < thresholds.memory_total_critical:
            return "警告", "⚠️"
        elif memory_mb < thresholds.memory_total_emergency:
            return "严重", "🔴"
        else:
            return "紧急", "🔴"


def get_drawcall_status(drawcalls: int) -> tuple[str, str]:
    """Get DrawCall status evaluation

    Returns:
        (status_text, status_icon)
    """
    try:
        from ..utils.thresholds import Thresholds
    except (ImportError, ValueError):
        from utils.thresholds import Thresholds
    thresholds = Thresholds()

    if drawcalls <= thresholds.drawcall_good:
        return "优秀", "✅"
    elif drawcalls <= thresholds.drawcall_warning:
        return "良好", "✅"
    elif drawcalls <= thresholds.drawcall_critical:
        return "警告", "⚠️"
    else:
        return "严重", "🔴"
