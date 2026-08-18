"""
案例查询工具

Example:
    >>> from automation_api import get_cases, get_case_linked_pipelines
    >>>
    >>> # 获取案例列表
    >>> cases = get_cases()
    >>>
    >>> # 获取案例关联的流水线
    >>> pipelines = get_case_linked_pipelines(case_id=1)
"""

from typing import List, Dict, Any
from .client import _get
from .config import get_config


def get_cases() -> List[Dict[str, Any]]:
    """
    获取案例列表

    Returns:
        案例列表

    Example:
        >>> cases = get_cases()
        >>> for case in cases:
        ...     print(f"{case['name']} - {case['status']}")
    """
    params = {'projectId': get_config().project_id}
    response = _get('/api/case/list', params)
    return response.get('data', [])


def get_case_linked_pipelines(case_id: int) -> List[Dict[str, Any]]:
    """
    获取案例关联的流水线

    Args:
        case_id: 案例ID

    Returns:
        关联的流水线列表

    Example:
        >>> pipelines = get_case_linked_pipelines(case_id=1)
        >>> for p in pipelines:
        ...     print(p['name'])
    """
    params = {
        'projectId': get_config().project_id,
        'caseId': case_id
    }
    response = _get('/api/case/linked/pipeline', params)
    return response.get('data', [])


def get_deleted_cases() -> List[Dict[str, Any]]:
    """
    获取已删除的案例列表

    Returns:
        已删除的案例列表
    """
    params = {'projectId': get_config().project_id}
    response = _get('/api/case/deleted/list', params)
    return response.get('data', [])
