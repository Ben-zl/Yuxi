"""统一的 UUID 生成工具，避免各模块重复定义等价 helper。"""

from __future__ import annotations

import uuid


def new_uuid() -> str:
    """生成标准 UUID v4 字符串。"""
    return str(uuid.uuid4())
