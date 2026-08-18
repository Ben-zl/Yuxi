"""
日志查询工具

Example:
    >>> from automation_api import query_logs, get_log_download_urls
    >>>
    >>> # 查询日志
    >>> logs = query_logs(build_id="123", start_time="2024-01-01", end_time="2024-01-02")
    >>>
    >>> # 获取日志下载URL
    >>> urls = get_log_download_urls(days=7)
"""

from typing import List, Optional
from .client import _get
from .config import get_config


def query_logs(
    build_id: str,
    start_time: str,
    end_time: str,
    case_id: Optional[str] = None,
    device_id: Optional[str] = None,
    retry_count: str = "0",
    log_level: Optional[str] = None
) -> List[dict]:
    """
    查询日志

    Args:
        build_id: 构建ID
        start_time: 开始时间
        end_time: 结束时间
        case_id: 案例ID（可选）
        device_id: 设备ID（可选）
        retry_count: 重试次数，默认: "0"
        log_level: 日志级别（可选）

    Returns:
        日志列表

    Example:
        >>> logs = query_logs(
        ...     build_id="123",
        ...     start_time="2024-01-01 00:00:00",
        ...     end_time="2024-01-02 00:00:00"
        ... )
    """
    params = {
        'projectId': get_config().project_id,
        'buildId': build_id,
        'start_time': start_time,
        'end_time': end_time,
        'caseId': case_id,
        'deviceId': device_id,
        'retryCount': retry_count,
        'loglevel': log_level
    }
    response = _get('/api/log/query', params)
    return response.get('data', [])


def get_log_download_urls(
    days: int = 0,
    project_id: Optional[str] = None,
    file_name: Optional[str] = None
) -> List[str]:
    """
    获取日志文件下载预授权URL

    Args:
        days: 获取多少天的数据
        project_id: 项目ID（可选，默认使用配置的项目ID）
        file_name: 文件名（可选）

    Returns:
        下载URL列表

    Example:
        >>> urls = get_log_download_urls(days=7)
        >>> for url in urls:
        ...     print(url)
    """
    params = {
        'projectId': project_id or get_config().project_id,
        'days': days,
        'fileName': file_name
    }
    response = _get('/build/logs/download/presigned/urls', params)
    return response.get('data', [])


def get_log_download_urls_v2(
    days: int = 0,
    project_id: Optional[str] = None
) -> List[str]:
    """
    获取日志文件下载预授权URL（V2版本）

    Args:
        days: 获取多少天的数据
        project_id: 项目ID（可选）

    Returns:
        下载URL列表
    """
    params = {
        'projectId': project_id or get_config().project_id,
        'days': days
    }
    response = _get('/build/logs/v2/download/presigned/urls', params)
    return response.get('data', [])
