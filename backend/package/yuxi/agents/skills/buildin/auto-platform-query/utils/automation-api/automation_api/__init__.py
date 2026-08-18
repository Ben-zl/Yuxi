"""
自动化测试平台查询工具模块

使用方式:
    # 方式1: 环境变量配置（推荐）
    # export AUTOMATION_BASE_URL=https://automation-api.testplus.cn
    # export AUTOMATION_PROJECT_ID=your_project_id
    # export AUTOMATION_USER_ID=your_user_id
    # from automation_api import get_pipelines
    # pipelines = get_pipelines()

    # 方式2: 代码中配置
    # from automation_api import init_config, get_pipelines
    # init_config(
    #     base_url="https://automation-api.testplus.cn",
    #     project_id="your_project_id",
    #     user_id="your_user_id"
    # )
    # pipelines = get_pipelines()
"""

from .config import init_config, get_config
from .exceptions import (
    AutomationAPIError,
    ConfigError,
    NetworkError,
    APIException,
    ResourceNotFoundError
)
from .pipeline import (
    get_pipelines,
    get_pipeline_detail,
    check_pipeline_name,
    get_pipeline_performance_trend,
    get_pipeline_power_list
)
from .task import (
    get_tasks,
    get_task_detail,
    get_device_execute_info,
    get_device_build_detail
)
from .case import (
    get_cases,
    get_case_linked_pipelines,
    get_deleted_cases
)
from .device import (
    get_devices,
    get_all_devices,
    get_device_screenshots,
    get_device_pipeline_relation
)
from .package import (
    get_packages,
    get_package_detail
)
from .build import (
    get_build_info,
    get_build_case,
    get_case_running_status,
    get_case_device_detail
)
from .project import (
    get_project_config,
    get_email_template,
    get_email_config,
    get_xiezuo_token,
    get_trend_pipelines,
    get_project_group_relations
)
from .log import (
    query_logs,
    get_log_download_urls,
    get_log_download_urls_v2
)

__all__ = [
    # 配置
    'init_config',
    'get_config',
    # 异常
    'AutomationAPIError',
    'ConfigError',
    'NetworkError',
    'APIException',
    'ResourceNotFoundError',
    # 流水线
    'get_pipelines',
    'get_pipeline_detail',
    'check_pipeline_name',
    'get_pipeline_performance_trend',
    'get_pipeline_power_list',
    # 任务
    'get_tasks',
    'get_task_detail',
    'get_device_execute_info',
    'get_device_build_detail',
    # 案例
    'get_cases',
    'get_case_linked_pipelines',
    'get_deleted_cases',
    # 设备
    'get_devices',
    'get_all_devices',
    'get_device_screenshots',
    'get_device_pipeline_relation',
    # 包体
    'get_packages',
    'get_package_detail',
    # 构建
    'get_build_info',
    'get_build_case',
    'get_case_running_status',
    'get_case_device_detail',
    # 项目
    'get_project_config',
    'get_email_template',
    'get_email_config',
    'get_xiezuo_token',
    'get_trend_pipelines',
    'get_project_group_relations',
    # 日志
    'query_logs',
    'get_log_download_urls',
    'get_log_download_urls_v2',
]

__version__ = '1.0.0'
