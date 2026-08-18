"""
HTTP客户端封装
"""

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from typing import Optional, Dict, Any
from .config import get_config
from .exceptions import APIException, NetworkError


def _create_session() -> requests.Session:
    """创建带重试机制的Session"""
    session = requests.Session()
    config = get_config()

    retry_strategy = Retry(
        total=config.max_retries,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)

    return session


# 全局Session
_session: Optional[requests.Session] = None


def _get_session() -> requests.Session:
    """获取或创建Session"""
    global _session
    if _session is None:
        _session = _create_session()
    return _session


def _get(
    endpoint: str,
    params: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    发送GET请求

    Args:
        endpoint: API端点
        params: 查询参数

    Returns:
        响应数据

    Raises:
        NetworkError: 网络错误
        APIException: API错误
    """
    config = get_config()

    if params is None:
        params = {}
    params.setdefault('projectId', config.project_id)

    url = f"{config.base_url}{endpoint}"
    session = _get_session()

    try:
        response = session.get(
            url,
            params=params,
            timeout=config.timeout
        )
        response.raise_for_status()
        return response.json()

    except requests.RequestException as e:
        raise NetworkError(f"网络请求失败: {e}") from e
    except ValueError as e:
        raise APIException(f"无效的JSON响应: {e}") from e


def _post(
    endpoint: str,
    data: Dict[str, Any],
    params: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    发送POST请求

    Args:
        endpoint: API端点
        data: 请求体数据
        params: 查询参数

    Returns:
        响应数据
    """
    config = get_config()

    if params is None:
        params = {}
    # API 要求 projectId 必须在 URL 查询参数中
    params.setdefault('projectId', config.project_id)

    url = f"{config.base_url}{endpoint}"
    session = _get_session()

    try:
        response = session.post(
            url,
            params=params,
            json=data,
            timeout=config.timeout
        )
        response.raise_for_status()
        return response.json()

    except requests.RequestException as e:
        raise NetworkError(f"网络请求失败: {e}") from e
    except ValueError as e:
        raise APIException(f"无效的JSON响应: {e}") from e


def close_session():
    """关闭Session"""
    global _session
    if _session is not None:
        _session.close()
        _session = None
