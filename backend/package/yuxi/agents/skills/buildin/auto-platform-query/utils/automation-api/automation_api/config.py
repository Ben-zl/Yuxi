"""
配置管理模块

支持三种配置方式（优先级从高到低）:
1. 代码中调用 init_config() 显式配置
2. 环境变量
3. .env 文件
"""

import os
from typing import Optional
from dotenv import load_dotenv

# 加载 .env 文件
load_dotenv()


class _Config:
    """内部配置类"""

    def __init__(self):
        self._base_url: Optional[str] = None
        self._project_id: Optional[str] = None
        self._user_id: Optional[str] = None
        self._timeout: int = 30
        self._max_retries: int = 3

    @property
    def base_url(self) -> str:
        """获取API基础URL"""
        if self._base_url is None:
            self._base_url = os.getenv(
                'AUTOMATION_BASE_URL',
                'https://automation-api.testplus.cn'
            )
        return self._base_url.rstrip('/')

    @property
    def project_id(self) -> str:
        """获取项目ID"""
        if self._project_id is None:
            self._project_id = os.getenv('AUTOMATION_PROJECT_ID', '')
        if not self._project_id:
            raise ValueError(
                "project_id 未配置，请通过 init_config() 配置 "
                "或设置环境变量 AUTOMATION_PROJECT_ID"
            )
        return self._project_id

    @property
    def user_id(self) -> str:
        """获取用户ID"""
        if self._user_id is None:
            self._user_id = os.getenv('AUTOMATION_USER_ID', '')
        if not self._user_id:
            raise ValueError(
                "user_id 未配置，请通过 init_config() 配置 "
                "或设置环境变量 AUTOMATION_USER_ID"
            )
        return self._user_id

    @property
    def timeout(self) -> int:
        """获取请求超时时间"""
        return self._timeout

    @property
    def max_retries(self) -> int:
        """获取最大重试次数"""
        return self._max_retries


# 全局配置单例
_config = _Config()


def init_config(
    base_url: Optional[str] = None,
    project_id: Optional[str] = None,
    user_id: Optional[str] = None,
    timeout: int = 30,
    max_retries: int = 3
):
    """
    初始化配置

    Args:
        base_url: API基础URL
        project_id: 项目ID
        user_id: 用户ID
        timeout: 请求超时时间（秒）
        max_retries: 最大重试次数

    Example:
        >>> from automation_api import init_config
        >>> init_config(
        ...     base_url="https://automation-api.testplus.cn",
        ...     project_id="your_project_id",
        ...     user_id="your_user_id"
        ... )
    """
    if base_url is not None:
        _config._base_url = base_url
    if project_id is not None:
        _config._project_id = project_id
    if user_id is not None:
        _config._user_id = user_id
    _config._timeout = timeout
    _config._max_retries = max_retries


def get_config() -> _Config:
    """获取当前配置"""
    return _config
