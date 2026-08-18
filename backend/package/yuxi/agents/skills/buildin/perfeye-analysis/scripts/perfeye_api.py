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
        # 基础信息 (BaseInfo)
        "case_name": None,
        "app_version": None,
        "cpu_type": None,
        "gpu_type": None,
        "picture_quality": None,
        "resolution": None,
        "ram_size": None,
        "os_version": None,
        "graphics_api": None,
        "duration": None,
        "report_time": None,
        # FPS 指标 (LabelFPS)
        "avg_fps": None,
        "tp90": None,
        "jank_per_10min": None,
        "big_jank_per_10min": None,
        "all_jank": None,
        "all_big_jank": None,
        # 正常游玩 FPS (LabelModuleFPS)
        "tp90_fps_state_1": None,
        # CPU 指标 (LabelCPU)
        "avg_app": None,
        "avg_app_percent": None,
        "avg_ctemp": None,
        "max_app_percent": None,
        "max_ctemp": None,
        # GPU 指标 (LabelGPU)
        "avg_gpu_load_percent": None,
        "avg_gpu_freq_mhz": None,
        "avg_gtemp": None,
        "max_gpu_load_percent": None,
        "max_gpu_freq_mhz": None,
        "max_gtemp": None,
        # 内存指标 (LabelMemory)
        "init_memory_mb": None,
        "avg_memory_mb": None,
        "peak_memory_mb": None,
        # Xbox 平台特定内存指标
        "avg_total_used_memory_mb": None,
        "max_total_used_memory_mb": None,
        # 渲染指标 (LabelRenderer)
        "avg_drawcall": None,
        "max_drawcall": None,
        "avg_primitive": None,
        "peak_primitive": None,
        "avg_vertex": None,
        "peak_vertex": None,
        "avg_setpass": None,
        # 网络指标 (LabelNetwork)
        "avg_recv_kb_s": None,
        "avg_send_kb_s": None,
        "max_recv_kb_s": None,
        "max_send_kb_s": None,
        # 原始数据
        "raw_data": data
    }

    # 从 LabelInfo.All 中提取统计指标
    try:
        if isinstance(data, dict) and 'data' in data:
            inner_data = data['data']

            # 提取基础信息 (BaseInfo)
            if isinstance(inner_data, dict) and 'BaseInfo' in inner_data:
                base_info = inner_data['BaseInfo']
                metrics["case_name"] = base_info.get("CaseName")
                metrics["app_version"] = base_info.get("AppVersion")
                metrics["cpu_type"] = base_info.get("CPUType")
                metrics["gpu_type"] = base_info.get("GPUType")
                metrics["picture_quality"] = base_info.get("PictureQuality")
                metrics["resolution"] = base_info.get("Resolution")
                metrics["ram_size"] = base_info.get("RAMSize")
                metrics["os_version"] = base_info.get("OSVersion")
                metrics["graphics_api"] = base_info.get("GraphicsAPI")
                metrics["duration"] = base_info.get("Duration")
                metrics["report_time"] = base_info.get("ReportTime")

            if isinstance(inner_data, dict) and 'LabelInfo' in inner_data:
                label_info = inner_data['LabelInfo']
                if isinstance(label_info, dict) and 'All' in label_info:
                    all_stats = label_info['All']

                    # 提取 FPS 统计 (LabelFPS)
                    fps_stats = all_stats.get('LabelFPS', {})
                    metrics["avg_fps"] = _parse_float(fps_stats.get("AvgFPS"))
                    metrics["tp90"] = _parse_float(fps_stats.get("TP90"))
                    metrics["jank_per_10min"] = _parse_float(fps_stats.get("Jank(/10min)"))
                    metrics["big_jank_per_10min"] = _parse_float(fps_stats.get("BigJank(/10min)"))
                    metrics["all_jank"] = _parse_float(fps_stats.get("AllJank"))
                    metrics["all_big_jank"] = _parse_float(fps_stats.get("AllBigJank"))

                    # 提取正常游玩 FPS (LabelModuleFPS)
                    module_fps_stats = all_stats.get('LabelModuleFPS', {})
                    metrics["tp90_fps_state_1"] = _parse_float(module_fps_stats.get("Tp90(FPSState_1)"))

                    # 提取 CPU 统计 (LabelCPU)
                    cpu_stats = all_stats.get('LabelCPU', {})
                    metrics["avg_app"] = _parse_float(cpu_stats.get("AvgApp"))
                    metrics["avg_app_percent"] = _parse_float(cpu_stats.get("AvgApp(%)"))
                    metrics["avg_ctemp"] = _parse_float(cpu_stats.get("AvgCTemp"))
                    metrics["max_app_percent"] = _parse_float(cpu_stats.get("MaxApp(%)"))
                    metrics["max_ctemp"] = _parse_float(cpu_stats.get("MaxCTemp"))

                    # 提取 GPU 统计 (LabelGPU)
                    gpu_stats = all_stats.get('LabelGPU', {})
                    metrics["avg_gpu_load_percent"] = _parse_float(gpu_stats.get("Avg(GPULoad)[%]"))
                    metrics["avg_gpu_freq_mhz"] = _parse_float(gpu_stats.get("Avg(GPUFreq)[MHz]"))
                    metrics["avg_gtemp"] = _parse_float(gpu_stats.get("AvgGTemp"))
                    metrics["max_gpu_load_percent"] = _parse_float(gpu_stats.get("Max(GPULoad)[%]"))
                    metrics["max_gpu_freq_mhz"] = _parse_float(gpu_stats.get("Max(GPUFreq)[MHz]"))
                    metrics["max_gtemp"] = _parse_float(gpu_stats.get("MaxGTemp"))

                    # 提取内存统计 (LabelMemory)
                    mem_stats = all_stats.get('LabelMemory', {})
                    metrics["init_memory_mb"] = _parse_float(mem_stats.get("InitMemory(MB)"))
                    metrics["avg_memory_mb"] = _parse_float(mem_stats.get("AvgMemory(MB)"))
                    metrics["peak_memory_mb"] = _parse_float(mem_stats.get("PeakMemory(MB)"))
                    # Xbox 平台特定内存指标
                    metrics["avg_total_used_memory_mb"] = _parse_float(mem_stats.get("AvgTotalUsedMemory(MB)"))
                    metrics["max_total_used_memory_mb"] = _parse_float(mem_stats.get("MaxTotalUsedMemory(MB)"))

                    # 提取渲染统计 (LabelRenderer)
                    renderer_stats = all_stats.get('LabelRenderer', {})
                    metrics["avg_drawcall"] = _parse_float(renderer_stats.get("Avg(Drawcall)"))
                    metrics["max_drawcall"] = _parse_float(renderer_stats.get("Max(Drawcall)"))
                    metrics["avg_primitive"] = _parse_float(renderer_stats.get("Avg(PrimitiveCount)"))
                    metrics["peak_primitive"] = _parse_float(renderer_stats.get("Peak(PrimitiveCount)"))
                    metrics["avg_vertex"] = _parse_float(renderer_stats.get("Avg(VertexCount)"))
                    metrics["peak_vertex"] = _parse_float(renderer_stats.get("Peak(VertexCount)"))
                    # 尝试获取 SetPass 数据（Tp90 优先，Avg 其次）
                    metrics["avg_setpass"] = _parse_float(renderer_stats.get("Tp90(SetPass)") or renderer_stats.get("Avg(SetPass)"))

                    # 提取网络统计 (LabelNetwork)
                    network_stats = all_stats.get('LabelNetwork', {})
                    metrics["avg_recv_kb_s"] = _parse_float(network_stats.get("AvgRecv(KB/s)"))
                    metrics["avg_send_kb_s"] = _parse_float(network_stats.get("AvgSend(KB/s)"))
                    metrics["max_recv_kb_s"] = _parse_float(network_stats.get("MaxRecv(KB/s)"))
                    metrics["max_send_kb_s"] = _parse_float(network_stats.get("MaxSend(KB/s)"))
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
