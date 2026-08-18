"""格式化模块 - 生成 JSON 格式数据（专为 AI Agent 分析设计）"""

from .base import BaseFormatter
from .json import JSONFormatter


def get_formatter() -> BaseFormatter:
    """
    获取格式化器实例

    Returns:
        JSONFormatter 实例
    """
    return JSONFormatter()


__all__ = [
    'BaseFormatter',
    'JSONFormatter',
    'get_formatter'
]
