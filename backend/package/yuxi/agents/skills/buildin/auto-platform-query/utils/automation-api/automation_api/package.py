"""
包体查询工具

Example:
    >>> from automation_api import get_packages, get_package_detail
    >>>
    >>> # 获取包体列表
    >>> packages = get_packages(platform="Android")
    >>>
    >>> # 获取包体详情
    >>> detail = get_package_detail(package_id=1)
"""

from typing import List, Optional
from .client import _get
from .config import get_config


def get_packages(
    platform: Optional[str] = None,
    branch: Optional[str] = None,
    build_type: Optional[str] = None
) -> List[dict]:
    """
    获取包体列表

    Args:
        platform: 平台筛选（如：Android, iOS）
        branch: 分支筛选
        build_type: 构建类型筛选

    Returns:
        包体列表

    Example:
        >>> packages = get_packages(platform="Android", branch="master")
        >>> for p in packages:
        ...     print(f"{p['name']} - {p['version']}")
    """
    params = {
        'projectId': get_config().project_id,
        'platform': platform,
        'branch': branch,
        'buildType': build_type
    }
    response = _get('/api/package/list', params)
    return response.get('data', [])


def get_package_detail(package_id: int) -> dict:
    """
    获取包体详情

    Args:
        package_id: 包体ID

    Returns:
        包体详情

    Example:
        >>> detail = get_package_detail(package_id=1)
        >>> print(detail['version'])
    """
    params = {
        'projectId': get_config().project_id,
        'packageId': package_id
    }
    response = _get('/api/package/detail', params)
    return response.get('data', {})
