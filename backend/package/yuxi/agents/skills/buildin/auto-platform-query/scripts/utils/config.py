"""
配置管理模块

支持三种配置方式（优先级从高到低）:
1. 命令行参数
2. 环境变量
3. 默认值
"""

import os
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

# 加载 .env 文件
load_dotenv()


def add_sdk_to_path() -> None:
    """将 automation-api SDK 添加到 Python 路径"""
    skill_dir = Path(__file__).parent.parent.parent
    sdk_path = skill_dir / "utils" / "automation-api"

    if sdk_path.exists() and str(sdk_path) not in sys.path:
        sys.path.insert(0, str(sdk_path))


def load_config(
    base_url: str | None = None,
    project_id: str | None = None,
    user_id: str | None = None,
    timeout: int = 30,
    max_retries: int = 3
) -> dict[str, Any]:
    """
    加载配置，优先使用参数值，否则使用环境变量

    Args:
        base_url: API 基础 URL
        project_id: 项目 ID
        user_id: 用户 ID
        timeout: 请求超时时间（秒）
        max_retries: 最大重试次数

    Returns:
        配置字典
    """
    config = {
        'base_url': base_url or os.getenv('AUTOMATION_BASE_URL', 'https://automation-api.testplus.cn'),
        'project_id': project_id or os.getenv('AUTOMATION_PROJECT_ID', ''),
        'user_id': user_id or os.getenv('AUTOMATION_USER_ID', ''),
        'timeout': timeout,
        'max_retries': max_retries
    }

    # 移除末尾的斜杠
    config['base_url'] = config['base_url'].rstrip('/')

    return config


def validate_config(config: dict[str, Any]) -> tuple[bool, str]:
    """
    验证配置是否完整

    Args:
        config: 配置字典

    Returns:
        (是否有效, 错误信息)
    """
    if not config.get('project_id'):
        return False, "未配置 project_id，请设置环境变量 AUTOMATION_PROJECT_ID 或使用 --project-id 参数"

    if not config.get('user_id'):
        return False, "未配置 user_id，请设置环境变量 AUTOMATION_USER_ID 或使用 --user-id 参数"

    return True, ""


def init_automation_api(config: dict[str, Any]) -> None:
    """
    初始化 automation-api SDK

    Args:
        config: 配置字典
    """
    try:
        # 添加 SDK 到路径
        add_sdk_to_path()

        from automation_api import init_config
        init_config(
            base_url=config['base_url'],
            project_id=config['project_id'],
            user_id=config['user_id'],
            timeout=config['timeout'],
            max_retries=config['max_retries']
        )
    except ImportError as e:
        raise ImportError(
            f"无法导入 automation-api SDK: {e}\n"
            f"请确保 SDK 位于 utils/automation-api 目录并已安装"
        )
    except Exception as e:
        raise RuntimeError(f"初始化 SDK 失败: {e}")


def get_config_display(config: dict[str, Any]) -> str:
    """
    获取配置的显示文本（隐藏敏感信息）

    Args:
        config: 配置字典

    Returns:
        格式化的配置文本
    """
    lines = [
        "=== 当前配置 ===",
        f"API URL: {config['base_url']}",
        (
            f"Project ID: {config['project_id'][:8]}..."
            if len(config['project_id']) > 8
            else f"Project ID: {config['project_id']}"
        ),
        (
            f"User ID: {config['user_id'][:8]}..."
            if len(config['user_id']) > 8
            else f"User ID: {config['user_id']}"
        ),
        f"Timeout: {config['timeout']}s",
        f"Max Retries: {config['max_retries']}",
    ]
    return "\n".join(lines)
