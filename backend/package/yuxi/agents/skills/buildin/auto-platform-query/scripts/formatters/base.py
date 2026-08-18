"""
基础格式化器 - 定义格式化器接口
"""

from abc import ABC, abstractmethod
from typing import Any


class BaseFormatter(ABC):
    """基础格式化器抽象类 - 定义所有格式化器必须实现的方法"""

    @abstractmethod
    def format_pipelines(self, data: Any) -> str:
        """格式化流水线数据"""
        pass

    @abstractmethod
    def format_tasks(self, data: Any) -> str:
        """格式化任务数据"""
        pass

    @abstractmethod
    def format_devices(self, data: Any) -> str:
        """格式化设备数据"""
        pass

    @abstractmethod
    def format_cases(self, data: Any) -> str:
        """格式化用例数据"""
        pass

    @abstractmethod
    def format_packages(self, data: Any) -> str:
        """格式化包体数据"""
        pass

    @abstractmethod
    def format_builds(self, data: Any) -> str:
        """格式化构建数据"""
        pass

    @abstractmethod
    def format_config(self, data: Any) -> str:
        """格式化配置数据"""
        pass

    @abstractmethod
    def format_logs(self, data: Any) -> str:
        """格式化日志数据"""
        pass

    @abstractmethod
    def format_device_executions(self, task_detail: Any, device_id: int = None) -> str:
        """
        格式化用例设备执行情况

        Args:
            task_detail: 任务详情数据（包含 caseDetails）
            device_id: 设备ID（如果指定了特定设备）

        Returns:
            格式化的字符串
        """
        pass
