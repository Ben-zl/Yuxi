"""
构建查询工具

Example:
    >>> from automation_api import get_build_info, get_case_running_status
    >>>
    >>> # 获取构建信息
    >>> info = get_build_info(build_id=123)
    >>>
    >>> # 获取案例运行状态
    >>> status = get_case_running_status(device_case_id=456)
"""

from typing import Dict, Any
from .client import _get
from .config import get_config


def get_build_info(build_id: int) -> Dict[str, Any]:
    """
    获取构建信息

    Args:
        build_id: 构建ID

    Returns:
        构建信息
    """
    return _get(f'/build/controller/build/info?buildId={build_id}', {})


def get_build_case(build_id: int, device_id: int) -> Dict[str, Any]:
    """
    获取构建案例信息

    Args:
        build_id: 构建ID
        device_id: 设备ID

    Returns:
        案例信息
    """
    params = {
        'projectId': get_config().project_id,
        'buildId': build_id,
        'deviceId': device_id
    }
    return _get('/build/controller/get/case', params)


def get_case_running_status(device_case_id: int) -> Dict[str, Any]:
    """
    获取案例运行状态

    Args:
        device_case_id: 设备案例ID

    Returns:
        运行状态信息
    """
    params = {'deviceCaseId': device_case_id}
    return _get('/build/controller/case/running/status', params)


def get_case_device_detail(device_case_id: int) -> Dict[str, Any]:
    """
    获取案例设备详情

    Args:
        device_case_id: 设备案例ID

    Returns:
        设备详情
    """
    params = {'deviceCaseId': device_case_id}
    return _get('/build/controller/case/device/running/detail', params)
