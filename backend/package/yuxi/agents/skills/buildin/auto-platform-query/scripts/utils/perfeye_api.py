"""
Perfeye API 模块

用于调用 Perfeye 平台接口获取性能数据
"""

import requests
from typing import Optional, Dict, Any


class PerfeyeAPIError(Exception):
    """Perfeye API 错误"""
    pass


class PerfeyeNetworkError(PerfeyeAPIError):
    """Perfeye 网络错误"""
    pass


class PerfeyeAuthError(PerfeyeAPIError):
    """Perfeye 认证错误"""
    pass


# API 配置
PERFEYE_API_CONFIG = {
    "BASE_URL": "http://perfeye.console.testplus.cn",
    "TOKEN": "Bearer mj6cltF&!L#yWX8k",
    "TIMEOUT": 30
}


def get_task_data(uuid: str, timeout: int = None) -> Dict[str, Any]:
    """
    获取 Perfeye 任务数据

    Args:
        uuid: 任务 UUID
        timeout: 请求超时时间（秒），默认使用配置的值

    Returns:
        任务数据字典

    Raises:
        PerfeyeNetworkError: 网络请求失败
        PerfeyeAuthError: 认证失败
        PerfeyeAPIError: API 返回错误
    """
    url = f"{PERFEYE_API_CONFIG['BASE_URL']}/api/show/task/{uuid}"
    headers = {
        "Authorization": PERFEYE_API_CONFIG["TOKEN"],
        "Content-Type": "application/json"
    }

    timeout = timeout or PERFEYE_API_CONFIG["TIMEOUT"]

    try:
        response = requests.post(
            url,
            headers=headers,
            timeout=timeout
        )

        # 检查认证错误
        if response.status_code == 401:
            raise PerfeyeAuthError("认证失败：无效的 Token")

        # 检查其他错误
        if response.status_code != 200:
            error_msg = f"API 返回错误: {response.status_code}"
            try:
                error_detail = response.json()
                error_msg += f" - {error_detail.get('message', '未知错误')}"
            except:
                pass
            raise PerfeyeAPIError(error_msg)

        # 返回 JSON 数据
        try:
            return response.json()
        except ValueError as e:
            raise PerfeyeAPIError(f"解析响应数据失败: {e}")

    except requests.exceptions.Timeout:
        raise PerfeyeNetworkError(f"请求超时（超过 {timeout} 秒）")
    except requests.exceptions.ConnectionError as e:
        raise PerfeyeNetworkError(f"网络连接失败: {e}")
    except requests.exceptions.RequestException as e:
        raise PerfeyeNetworkError(f"网络请求失败: {e}")


def get_task_performance_metrics(uuid: str) -> Dict[str, Any]:
    """
    获取任务的性能指标数据（从 LabelInfo.All 统计数据中提取）

    Args:
        uuid: 任务 UUID

    Returns:
        性能指标字典，包含 FPS、JANK、内存等统计数据
    """
    data = get_task_data(uuid)

    # 提取性能指标
    metrics = {
        "uuid": uuid,
        # FPS 指标
        "avg_fps": None,
        "tp90": None,
        "tp90_deposit": None,
        "jank_per_10min": None,
        "big_jank_per_10min": None,
        "logic_jank_per_10min": None,
        "logic_big_jank_per_10min": None,
        # 内存指标
        "avg_memory_mb": None,
        "peak_memory_mb": None,
        "peak_memory_deposit_mb": None,
        # GPU 指标
        "avg_gpu_load_percent": None,
        "max_gpu_load_percent": None,
        "avg_gpu_temp": None,
        "max_gpu_temp": None,
        # 原始数据
        "raw_data": data
    }

    # 从 LabelInfo.All 中提取统计指标
    try:
        if isinstance(data, dict) and 'data' in data:
            inner_data = data['data']
            if isinstance(inner_data, dict) and 'LabelInfo' in inner_data:
                label_info = inner_data['LabelInfo']
                if isinstance(label_info, dict) and 'All' in label_info:
                    all_stats = label_info['All']

                    # 提取 FPS 统计
                    fps_stats = all_stats.get('LabelFPS', {})
                    metrics["avg_fps"] = _parse_float(fps_stats.get("AvgFPS"))
                    metrics["tp90"] = _parse_float(fps_stats.get("TP90"))
                    metrics["tp90_deposit"] = _parse_float(fps_stats.get("TP90Deposit"))
                    metrics["jank_per_10min"] = _parse_float(fps_stats.get("Jank(/10min)"))
                    metrics["big_jank_per_10min"] = _parse_float(fps_stats.get("BigJank(/10min)"))
                    metrics["logic_jank_per_10min"] = _parse_float(fps_stats.get("LogicJank(/10min)"))
                    metrics["logic_big_jank_per_10min"] = _parse_float(fps_stats.get("LogicBigJank(/10min)"))

                    # 提取内存统计（优先使用 PeakMemory，其次使用 PeakMemoryDeposit）
                    mem_stats = all_stats.get('LabelMemory', {})
                    metrics["avg_memory_mb"] = _parse_float(mem_stats.get("AvgMemory(MB)"))
                    # 尝试读取内存峰值（支持两种字段名）
                    peak_mem = _parse_float(mem_stats.get("PeakMemory(MB)"))
                    if peak_mem is None:
                        peak_mem = _parse_float(mem_stats.get("PeakMemoryDeposit(MB)"))
                    metrics["peak_memory_mb"] = peak_mem
                    metrics["peak_memory_deposit_mb"] = _parse_float(mem_stats.get("PeakMemoryDeposit(MB)"))

                    # 提取 GPU 统计
                    gpu_stats = all_stats.get('LabelGPU', {})
                    metrics["avg_gpu_load_percent"] = _parse_float(gpu_stats.get("Avg(GPULoad)[%]"))
                    metrics["max_gpu_load_percent"] = _parse_float(gpu_stats.get("Max(GPULoad)[%]"))
                    metrics["avg_gpu_temp"] = _parse_float(gpu_stats.get("AvgGTemp"))
                    metrics["max_gpu_temp"] = _parse_float(gpu_stats.get("MaxGTemp"))
    except Exception as e:
        # 如果提取失败，保持默认值
        pass

    return metrics


def _parse_float(value: Any) -> Optional[float]:
    """解析浮点数"""
    if value is None or value == '':
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def check_api_connection() -> bool:
    """
    检查 Perfeye API 连接是否正常

    Returns:
        True if 连接正常，False otherwise
    """
    try:
        # 尝试获取一个简单的任务（可能不存在，但可以验证连接和认证）
        # 使用一个测试 UUID
        get_task_data("test-uuid-connection-check")
        return True
    except PerfeyeAuthError:
        # 认证错误说明连接正常但 Token 无效
        return True
    except PerfeyeNetworkError:
        # 网络错误说明连接失败
        return False
    except PerfeyeAPIError:
        # 其他 API 错误说明连接正常
        return True
    except Exception:
        return False
