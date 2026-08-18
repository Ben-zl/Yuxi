"""
稳定性测试数据预处理器

对原始稳定性测试数据进行预处理，生成结构化的统计数据供 AI 分析。
按 STABILITY_TEST_DETAIL.md 文档要求，提供执行时长、内存使用和崩溃检测功能。
"""

from typing import List, Dict, Any, Optional, Tuple
from collections import defaultdict, Counter
from datetime import datetime
import re


# 字段映射常量
class FM:
    """Field Mapping - 字段映射常量"""
    # 时间字段
    START_TIME = "st"
    END_TIME = "et"
    DURATION = "dur"
    EXECUTE_TIME = "et"

    # 性能指标
    PEAK_MEMORY_MB = "mem"
    AVG_MEMORY_MB = "avg_mem"

    # 统计字段
    COUNT = "n"
    AVG = "avg"
    MIN = "min"
    MAX = "max"
    TOTAL = "total"

    # 状态字段
    STATUS = "st"

    # ID 和名称字段
    DEVICE_ID = "di"
    DEVICE_NAME = "dn"
    CASE_ID = "ci"
    CASE_NAME = "cn"

    # 配置字段
    CONFIG_LEVEL = "cfg"

    # 崩溃字段
    CRASH_COUNT = "cc"


# 配置级别阈值（MB）
class MemoryThreshold:
    """内存阈值配置"""
    LOW = 8 * 1024  # 8 GB
    MEDIUM = 10 * 1024  # 10 GB
    HIGH = 12 * 1024  # 12 GB


# 内存峰值区间定义（GB）
class MemoryRange:
    """内存峰值区间定义"""
    RANGES = [
        (0, 6, "0-6GB"),
        (6, 8, "6-8GB"),
        (8, 10, "8-10GB"),
        (10, 12, "10-12GB"),
        (12, float('inf'), ">12GB"),
    ]


class StabilityPreprocessor:
    """稳定性测试数据预处理器"""

    def __init__(self):
        # 配置分类关键词
        self.device_config_keywords = {
            "low": [  # 低配
                "GTX1050", "GTX1650", "GTX960", "GTX750Ti",
                "GTX650", "GT730", "GT740", "MX250", "MX450",
                "vega8", "780M", "660M", "Graphics 630"
            ],
            "medium": [  # 中配
                "RTX2060", "RTX3060", "RTX2070", "RTX2070Super",
                "GTX1080", "GTX1660Ti", "RX6600 XT", "RX5500 XT",
                "6500XT", "6700S", "RX6700XT"
            ],
            "high": [  # 高配
                "RTX3080", "RTX3090", "RTX4060", "RTX4070",
                "RTX4070Ti", "RTX4070S", "RTX4070TiS", "RTX5080",
                "RX7900 XT", "RX7700S", "9800X3D"
            ]
        }

    def preprocess(self, task_detail: Dict[str, Any]) -> Dict[str, Any]:
        """
        预处理稳定性测试任务详情数据

        Args:
            task_detail: 任务详情数据（包含 caseDetails）

        Returns:
            结构化的稳定性统计数据
        """
        result = {
            FM.START_TIME: task_detail.get("startTime"),
            FM.END_TIME: task_detail.get("endTime"),
            FM.EXECUTE_TIME: task_detail.get("executeTime"),
        }

        # 检查 caseDetails
        case_details = task_detail.get("caseDetails")
        if not case_details:
            result["error"] = "暂无用例数据"
            return result

        # ========== 统计执行时长（按状态区分）==========
        duration_stats = self._calculate_duration_stats(case_details)
        result["duration_stats"] = duration_stats

        # ========== 统计内存使用（含区间分布）==========
        memory_stats = self._calculate_memory_stats(case_details)
        result["memory_stats"] = memory_stats

        # ========== 检测崩溃记录 ==========
        crash_stats = self._calculate_crash_stats(case_details)
        result["crash_stats"] = crash_stats

        return result

    # ========== 预处理方法 ==========
    def _calculate_duration_stats(self, case_details: List[Dict[str, Any]]) -> Dict[str, Any]:
        """计算执行时长统计（按状态区分）"""
        # 按 SUCCESS/FAILED/CANCEL 状态分组
        status_groups = defaultdict(list)

        for case in case_details:
            for device in case.get("deviceDetail", []):
                # 只统计在线设备 (deviceStatus != 0)
                if device.get("deviceStatus", 1) == 0:
                    continue

                status = device.get("status")
                # 统计所有状态，不过滤
                duration = self._parse_duration_seconds(
                    device.get("startTime"),
                    device.get("endTime")
                )
                if duration is not None:
                    status_groups[status].append(duration)

        # 计算每组统计
        stats = {}
        for status in ["SUCCESS", "FAILED", "CANCEL"]:
            durations = status_groups.get(status, [])
            stats[status] = {
                FM.COUNT: len(durations),
                FM.AVG: round(sum(durations) / len(durations), 2) if durations else 0,
                FM.MIN: min(durations) if durations else 0,
                FM.MAX: max(durations) if durations else 0,
            }

        return stats

    def _calculate_memory_stats(self, case_details: List[Dict[str, Any]]) -> Dict[str, Any]:
        """计算内存使用统计（含区间分布）"""
        all_memory = []

        # 收集内存数据
        for case in case_details:
            for device in case.get("deviceDetail", []):
                # 只统计在线设备
                if device.get("deviceStatus", 1) == 0:
                    continue

                # 提取内存数据
                perfeye_data = device.get("perfeyeData")
                if not perfeye_data:
                    continue

                # 解析 perfeyeData
                if isinstance(perfeye_data, str):
                    try:
                        perfeye_dict = {}
                        # 手动解析关键字段
                        import re
                        mem_match = re.search(r'"LabelMemory\.PeakMemory\(MB\)":\s*"?(\d+\.?\d*)"?', perfeye_data)
                        if mem_match:
                            peak_memory = float(mem_match.group(1))
                        else:
                            continue
                    except:
                        continue
                elif isinstance(perfeye_data, dict):
                    peak_memory = perfeye_data.get("LabelMemory.PeakMemory(MB)")
                    if peak_memory is None:
                        peak_memory = perfeye_data.get("LabelMemory.PeakMemoryDeposit(MB)")
                    peak_memory = float(peak_memory) if peak_memory else None
                else:
                    continue

                if peak_memory is None:
                    continue

                all_memory.append(peak_memory)

        # 整体统计
        overall = {
            FM.COUNT: len(all_memory),
            FM.AVG: round(sum(all_memory) / len(all_memory), 2) if all_memory else 0,
            FM.MIN: min(all_memory) if all_memory else 0,
            FM.MAX: max(all_memory) if all_memory else 0,
        }

        # 按区间统计
        by_range = {label: 0 for _, _, label in MemoryRange.RANGES}
        for mem in all_memory:
            range_label = self._classify_memory_range(mem)
            by_range[range_label] += 1

        return {
            "overall": overall,
            "by_range": by_range,
        }

    def _classify_memory_range(self, memory_mb: float) -> str:
        """将内存值分类到对应区间"""
        memory_gb = memory_mb / 1024
        for min_gb, max_gb, label in MemoryRange.RANGES:
            if min_gb <= memory_gb < max_gb:
                return label
        return ">12GB"

    def _calculate_crash_stats(self, case_details: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        计算崩溃统计（增强版 - 包含 Crasheye URL 和配置级别）

        Args:
            case_details: 用例详情列表

        Returns:
            崩溃统计数据，包含：
            - total_devices: 总设备数
            - crash_devices: 崩溃设备数
            - crash_rate: 崩溃率（百分比）
            - details: 崩溃设备详情列表（含 URL 和配置级别）
        """
        import json

        total_devices = 0
        crash_devices = []

        for case in case_details:
            for device in case.get("deviceDetail", []):
                # 只统计在线设备
                if device.get("deviceStatus", 1) == 0:
                    continue

                total_devices += 1
                report_data = device.get("reportData", {})

                # 处理 reportData 为字符串的情况
                if isinstance(report_data, str):
                    try:
                        report_data = json.loads(report_data)
                    except (json.JSONDecodeError, TypeError):
                        report_data = {}

                # 提取 Crasheye URL
                crasheye_urls = self._extract_crasheye_urls(report_data)

                if crasheye_urls:
                    device_name = device.get("deviceName", "")
                    crash_devices.append({
                        FM.DEVICE_NAME: device_name,
                        FM.COUNT: len(crasheye_urls),
                        FM.STATUS: device.get("status"),
                        FM.CONFIG_LEVEL: self._classify_device_config(device_name),
                        "urls": crasheye_urls,
                    })

        return {
            "total_devices": total_devices,
            "crash_devices": len(crash_devices),
            "crash_rate": round(len(crash_devices) / total_devices * 100, 2) if total_devices > 0 else 0,
            "details": crash_devices,
        }

    def _extract_crasheye_urls(self, report_data: Dict[str, Any]) -> List[str]:
        """
        从 reportData 中提取所有 Crasheye URL

        Args:
            report_data: 设备报告数据字典

        Returns:
            Crasheye URL 列表
        """
        urls = []

        if not isinstance(report_data, dict):
            return urls

        for key, value in report_data.items():
            if "Crasheye" in key:
                # 处理直接 URL 字符串
                if isinstance(value, str) and value.startswith("http"):
                    urls.append(value)
                # 处理嵌套字典中的 URL
                elif isinstance(value, dict) and "url" in value:
                    url = value["url"]
                    if isinstance(url, str) and url.startswith("http"):
                        urls.append(url)

        return urls

    def _count_crasheye_keys(self, report_data: Dict[str, Any]) -> int:
        """统计 reportData 中包含 Crasheye 的键"""
        count = 0
        for key in report_data.keys():
            if "Crasheye" in key:
                count += 1
        return count

    def _calculate_config_stats(self, case_details: List[Dict[str, Any]]) -> Dict[str, Any]:
        """计算设备配置统计"""
        config_distribution = {"low": 0, "medium": 0, "high": 0, "unknown": 0}

        for case in case_details:
            for device in case.get("deviceDetail", []):
                # 只统计在线设备
                if device.get("deviceStatus", 1) == 0:
                    continue

                device_name = device.get("deviceName", "")
                config_level = self._classify_device_config(device_name)
                if config_level in config_distribution:
                    config_distribution[config_level] += 1

        return config_distribution

    def _classify_device_config(self, name: str) -> str:
        """分类设备配置级别"""
        if not name:
            return "unknown"

        name_upper = name.upper()

        for level, keywords in self.device_config_keywords.items():
            for keyword in keywords:
                if keyword.upper() in name_upper:
                    return level

        return "unknown"

    def _parse_duration_seconds(self, start_time: Optional[str], end_time: Optional[str]) -> Optional[int]:
        """解析时长（秒）"""
        if not start_time or not end_time:
            return None

        try:
            start = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
            end = datetime.fromisoformat(end_time.replace('Z', '+00:00'))
            return int((end - start).total_seconds())
        except (ValueError, AttributeError, TypeError):
            return None

    def _count_crasheye_reports(self, report_data: Dict[str, Any]) -> int:
        """统计 Crasheye 报告数量"""
        count = 0
        for key in report_data.keys():
            if "Crasheye" in key:
                count += 1
        return count
