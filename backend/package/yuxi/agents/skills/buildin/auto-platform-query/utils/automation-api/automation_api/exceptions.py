"""
异常定义
"""


class AutomationAPIError(Exception):
    """基础异常类"""
    pass


class ConfigError(AutomationAPIError):
    """配置错误"""
    pass


class NetworkError(AutomationAPIError):
    """网络错误"""
    pass


class APIException(AutomationAPIError):
    """API异常"""
    pass


class ResourceNotFoundError(AutomationAPIError):
    """资源不存在"""
    pass
