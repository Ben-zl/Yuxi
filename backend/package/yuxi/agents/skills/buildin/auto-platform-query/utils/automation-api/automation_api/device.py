"""
设备查询工具

Example:
    >>> from automation_api import get_devices, get_device_screenshots
    >>>
    >>> # 获取设备列表
    >>> devices = get_devices()
    >>>
    >>> # 获取设备截图
    >>> screenshots = get_device_screenshots(device_id=1)
"""

from typing import List, Optional
from .client import _get
from .config import get_config


def get_devices(group_id: Optional[int] = None) -> List[dict]:
    """
    获取设备列表

    Args:
        group_id: 分组ID（可选）

    Returns:
        设备列表

    Example:
        >>> devices = get_devices()
        >>> for d in devices:
        ...     print(f"{d['name']} - {d['status']}")
    """
    params = {
        'projectId': get_config().project_id,
        'groupId': group_id
    }
    response = _get('/api/device/list', params)
    return response.get('data', [])


def get_all_devices(group_id: Optional[int] = None) -> List[dict]:
    """
    获取所有设备列表

    Args:
        group_id: 分组ID（可选）

    Returns:
        所有设备列表
    """
    params = {
        'projectId': get_config().project_id,
        'groupId': group_id
    }
    response = _get('/api/device/all', params)
    return response.get('data', [])


def get_device_screenshots(
    device_id: int,
    count: int = 5,
    start: int = 0,
    end: int = 0
) -> List[str]:
    """
    获取设备截图URL列表

    Args:
        device_id: 设备ID
        count: 最多返回多少个截图，默认: 5
        start: 截图筛选开始时间戳，默认: 0
        end: 截图筛选结束时间戳，默认: 0

    Returns:
        截图URL列表

    Example:
        >>> urls = get_device_screenshots(device_id=1, count=10)
        >>> for url in urls:
        ...     print(url)
    """
    params = {
        'deviceId': device_id,
        'count': count,
        'start': start,
        'end': end
    }
    response = _get('/api/device/screenshots', params)
    return response.get('data', [])


def get_device_pipeline_relation(device_id: int) -> List[dict]:
    """
    获取设备关联的流水线

    Args:
        device_id: 设备ID

    Returns:
        关联的流水线列表
    """
    params = {'projectId': get_config().project_id}
    response = _get(f'/api/device/{device_id}/pipeline/relation', params)
    return response.get('data', [])
