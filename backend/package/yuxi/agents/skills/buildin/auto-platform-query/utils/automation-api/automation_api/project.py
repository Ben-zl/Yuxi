"""
项目配置查询工具

Example:
    >>> from automation_api import get_project_config, get_email_template
    >>>
    >>> # 获取项目配置
    >>> config = get_project_config()
    >>>
    >>> # 获取邮件模板
    >>> template = get_email_template()
"""

from typing import List, Dict, Any
from .client import _get
from .config import get_config


def get_project_config() -> Dict[str, Any]:
    """
    获取项目配置

    Returns:
        项目配置信息

    Example:
        >>> config = get_project_config()
        >>> print(config['project_name'])
    """
    params = {'projectId': get_config().project_id}
    response = _get('/api/project/config', params)
    return response.get('data', {})


def get_email_template() -> Dict[str, str]:
    """
    获取邮件模板

    Returns:
        邮件模板信息
    """
    params = {'projectId': get_config().project_id}
    response = _get('/api/project/email/template', params)
    return response.get('data', {})


def get_email_config() -> Dict[str, Any]:
    """
    获取邮件配置

    Returns:
        邮件配置信息
    """
    params = {'projectId': get_config().project_id}
    response = _get('/api/project/email/config', params)
    return response.get('data', {})


def get_xiezuo_token() -> str:
    """
    获取协作Token

    Returns:
        协作Token
    """
    params = {'projectId': get_config().project_id}
    response = _get('/api/project/xiezuo/token', params)
    return response.get('data', {}).get('token', '')


def get_trend_pipelines() -> List[int]:
    """
    获取趋势图流水线列表

    Returns:
        流水线ID列表
    """
    params = {'projectId': get_config().project_id}
    response = _get('/api/project/trend/pipelines', params)
    return response.get('data', [])


def get_project_group_relations() -> List[dict]:
    """
    获取项目和分组关系

    Returns:
        分组关系列表
    """
    params = {'projectId': get_config().project_id}
    response = _get('/api/group/project/group', params)
    return response.get('data', [])
