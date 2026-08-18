"""
流水线查询工具

Example:
    >>> from automation_api import get_pipelines, get_pipeline_detail
    >>>
    >>> # 获取流水线列表
    >>> pipelines = get_pipelines(platform="Android")
    >>>
    >>> # 获取流水线详情
    >>> detail = get_pipeline_detail(pipeline_id=1)
"""

from typing import List, Optional, Dict, Any
from .client import _get
from .config import get_config


def get_pipelines(
    pipeline_name: Optional[str] = None,
    platform: Optional[str] = None,
    creator: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    获取流水线列表

    Args:
        pipeline_name: 流水线名称（模糊查询）
        platform: 平台筛选（如：Android, iOS）
        creator: 创建者筛选
        start_time: 开始时间，格式: YYYY-MM-DD
        end_time: 结束时间，格式: YYYY-MM-DD

    Returns:
        流水线列表

    Example:
        >>> pipelines = get_pipelines(platform="Android")
        >>> for p in pipelines:
        ...     print(f"{p['name']} - {p['status']}")
    """
    config = get_config()
    params = {
        'userId': config.user_id,
        'pipelineName': pipeline_name,
        'platform': platform,
        'creator': creator,
        'startTime': start_time,
        'endTime': end_time
    }

    response = _get('/api/pipeline/list', params)
    return response.get('data', [])


def get_pipeline_detail(pipeline_id: int) -> Dict[str, Any]:
    """
    获取流水线详情

    Args:
        pipeline_id: 流水线ID

    Returns:
        流水线详情

    Example:
        >>> detail = get_pipeline_detail(pipeline_id=1)
        >>> print(detail['name'])
    """
    params = {'projectId': get_config().project_id}
    response = _get(f'/api/pipeline/detail/{pipeline_id}', params)
    return response.get('data', {})


def check_pipeline_name(pipeline_name: str) -> bool:
    """
    检查流水线名称是否存在

    Args:
        pipeline_name: 流水线名称

    Returns:
        True 如果名称存在，False 否则

    Example:
        >>> if check_pipeline_name("my_pipeline"):
        ...     print("名称已存在")
    """
    params = {
        'projectId': get_config().project_id,
        'pipelineName': pipeline_name
    }
    response = _get('/api/pipeline/pipelineName/check', params)
    return response.get('data', {}).get('exists', False)


def get_pipeline_performance_trend(
    pipeline_id: int,
    start_time: str,
    end_time: str
) -> Dict[str, Any]:
    """
    获取流水线性能趋势

    Args:
        pipeline_id: 流水线ID
        start_time: 开始时间，格式: YYYY-MM-DD 或 YYYY-MM-DD HH:mm:ss（默认添加 00:00:00）
        end_time: 结束时间，格式: YYYY-MM-DD 或 YYYY-MM-DD HH:mm:ss（默认添加 23:59:59）

    Returns:
        性能趋势数据
    """
    # 如果时间格式是 YYYY-MM-DD，添加时间部分
    if ' ' in start_time and len(start_time) == 10:
        start_time = f'{start_time} 00:00:00'
    if ' ' in end_time and len(end_time) == 10:
        end_time = f'{end_time} 23:59:59'

    params = {
        'projectId': get_config().project_id,
        'startTime': start_time,
        'endTime': end_time
    }
    return _get(f'/api/pipeline/{pipeline_id}/performance/trend', params)


def get_pipeline_power_list() -> List[Dict[str, Any]]:
    """
    获取流水线电源列表

    Returns:
        流水线电源列表
    """
    params = {'projectId': get_config().project_id}
    response = _get('/api/pipeline/power/list', params)
    return response.get('data', [])
