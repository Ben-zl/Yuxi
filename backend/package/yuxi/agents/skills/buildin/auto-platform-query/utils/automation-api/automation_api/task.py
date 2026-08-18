"""
任务查询工具

Example:
    >>> from automation_api import get_tasks, get_task_detail
    >>>
    >>> # 获取任务列表
    >>> tasks = get_tasks(filters={'status': 'running'})
    >>>
    >>> # 获取任务详情
    >>> detail = get_task_detail(task_id=123)
"""

from typing import Optional, Dict, Any
from .client import _get, _post
from .config import get_config


def get_tasks(
    filters: Optional[Dict[str, Any]] = None,
    order_by: str = "queueTime",
    asc: bool = False,
    page: int = 1,
    count: int = 20
) -> Dict[str, Any]:
    """
    获取任务列表

    Args:
        filters: 筛选条件，如 {'pipelineId': 947}
        order_by: 排序字段，默认: queueTime
        asc: 是否升序，默认: False（降序）
        page: 页码，默认: 1
        count: 每页数量，默认: 20

    Returns:
        任务列表数据，格式:
        {
            'code': 0,
            'msg': 'success',
            'data': {
                'list': [...],  # 任务列表
                'count': 39     # 总数量
            }
        }

    Example:
        >>> # 获取指定流水线的任务
        >>> result = get_tasks(filters={'pipelineId': 947}, count=10)
        >>> tasks = result['data']['list']
        >>> total = result['data']['count']

        >>> # 获取所有任务
        >>> result = get_tasks(count=100)
        >>> tasks = result['data']['list']

        >>> # 遍历任务
        >>> for task in tasks:
        ...     print(f"Task {task['buildId']}: {task['buildName']}")
    """
    config = get_config()
    data = {
        'projectId': config.project_id,  # body中也需要projectId
        'filters': filters or {},
        'order_by': order_by,
        'asc': str(asc).lower(),  # 转为字符串 "true" 或 "false"
        'page': page,
        'count': count
    }
    return _post('/api/tasks/list', data)


def get_task_detail(task_id: int) -> Dict[str, Any]:
    """
    获取任务详情

    Args:
        task_id: 任务ID

    Returns:
        任务详情

    Example:
        >>> detail = get_task_detail(task_id=123)
        >>> print(detail['status'])
    """
    params = {'projectId': get_config().project_id}
    response = _get(f'/api/tasks/detail/{task_id}', params)
    return response.get('data', {})


def get_device_execute_info(
    task_id: int,
    device_id: Optional[int] = None,
    build_case_id: Optional[int] = None
) -> Dict[str, Any]:
    """
    获取设备执行信息

    Args:
        task_id: 任务ID
        device_id: 设备ID（可选）
        build_case_id: 构建案例ID（可选）

    Returns:
        设备执行信息
    """
    params = {
        'projectId': get_config().project_id,
        'taskId': task_id,
        'deviceId': device_id,
        'buildCaseId': build_case_id
    }
    return _get('/api/tasks/device/execute/info', params)


def get_device_build_detail(
    build_id: int,
    device_id: int,
    build_case_id: Optional[int] = None
) -> Dict[str, Any]:
    """
    获取设备构建详情

    Args:
        build_id: 构建ID
        device_id: 设备ID
        build_case_id: 构建案例ID（可选）

    Returns:
        设备构建详情
    """
    params = {
        'projectId': get_config().project_id,
        'buildId': build_id,
        'deviceId': device_id,
        'buildCaseId': build_case_id
    }
    return _get('/api/tasks/device/build/detail', params)
