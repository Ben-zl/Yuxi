"""
JSON 格式化器 - 生成 AI 友好的 JSON 数据（优化版）
"""

import json
from typing import Any, List, Dict
from collections import Counter
from .base import BaseFormatter
from utils.stability_preprocessor import StabilityPreprocessor


# 字段映射常量
class FieldMapping:
    """字段映射：完整名称 -> 缩短名称"""

    # 性能指标
    FPS_TP90 = "f90"
    JANK_PER_10MIN = "jk"
    PEAK_MEMORY_MB = "mem"

    # 趋势字段
    FIRST_VALUE = "fv"
    LAST_VALUE = "lv"
    CHANGE = "ch"
    CHANGE_PERCENT = "cp"

    # 计数字段
    DATA_POINTS = "n"
    TOTAL_DEVICES = "t"
    COUNT = "n"

    # ID 字段
    BUILD_ID = "id"
    CASE_ID = "ci"
    DEVICE_ID = "di"
    PIPELINE_ID = "pid"

    # 名称字段
    BUILD_NAME = "name"
    CASE_NAME = "cn"
    DEVICE_NAME = "dn"
    PIPELINE_NAME = "pn"

    # 状态字段
    STATUS = "st"
    CASE_STATUS = "cs"
    DEVICE_STATUS = "ds"

    # 时间字段
    START_TIME = "t"
    END_TIME = "et"
    EXECUTE_TIME = "t"
    CREATE_TIME = "ct"

    # 配置字段
    CONFIG_LEVEL = "cfg"

    # 性能统计
    PERFORMANCE = "p"
    DEVICES = "devs"

    # 其他
    AVG = "avg"
    MIN = "min"
    MAX = "max"
    ONLINE = "on"
    OFFLINE = "off"
    SUCCESS = "s"
    FAILED = "f"


class JSONFormatter(BaseFormatter):
    """JSON 格式化器 - 生成结构化的 JSON 数据（专为 AI Agent 分析设计）"""

    def __init__(self):
        super().__init__()
        self._field_legend = {
            FieldMapping.FPS_TP90: "FPS TP90",
            FieldMapping.JANK_PER_10MIN: "Jank/10min",
            FieldMapping.PEAK_MEMORY_MB: "Memory MB",
            FieldMapping.FIRST_VALUE: "First Value",
            FieldMapping.LAST_VALUE: "Last Value",
            FieldMapping.CHANGE: "Change",
            FieldMapping.CHANGE_PERCENT: "Change %",
            FieldMapping.DATA_POINTS: "Count",
            FieldMapping.BUILD_ID: "Task ID",
            FieldMapping.CASE_ID: "Case ID",
            FieldMapping.DEVICE_ID: "Device ID",
            FieldMapping.PIPELINE_ID: "Pipeline ID",
            FieldMapping.BUILD_NAME: "Task Name",
            FieldMapping.CASE_NAME: "Case Name",
            FieldMapping.DEVICE_NAME: "Device Name",
            FieldMapping.PIPELINE_NAME: "Pipeline Name",
            FieldMapping.STATUS: "Status",
            FieldMapping.CONFIG_LEVEL: "Config Level",
            FieldMapping.PERFORMANCE: "Performance Stats",
            FieldMapping.DEVICES: "Devices",
        }

    def _get_legend(self) -> Dict[str, str]:
        """获取字段映射说明"""
        return self._field_legend

    def _escape_control_chars(self, s: str) -> str:
        """转义字符串中的控制字符为 JSON 安全格式"""
        if not isinstance(s, str):
            return s
        replacements = {
            '\n': '\\n',
            '\r': '\\r',
            '\t': '\\t',
            '\b': '\\b',
            '\f': '\\f',
        }
        for char, escaped in replacements.items():
            s = s.replace(char, escaped)
        return s

    def _clean_data(self, data: Any) -> Any:
        """递归清理数据中的所有字符串，转义控制字符"""
        if isinstance(data, str):
            return self._escape_control_chars(data)
        elif isinstance(data, dict):
            return {k: self._clean_data(v) for k, v in data.items()}
        elif isinstance(data, list):
            return [self._clean_data(item) for item in data]
        else:
            return data

    def _to_json(self, data: dict) -> str:
        """将字典转换为 JSON 字符串"""
        cleaned = self._clean_data(data)
        return json.dumps(cleaned, ensure_ascii=False, indent=2)

    def _parse_float(self, value) -> float:
        """解析浮点数"""
        if value is None or value == "N/A":
            return None
        try:
            return float(value)
        except (ValueError, TypeError):
            return None

    def _format_duration(self, seconds: int) -> str:
        """格式化秒数为可读时长"""
        if not seconds:
            return "N/A"
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        if hours > 0:
            return f"{hours}h {minutes}m"
        elif minutes > 0:
            return f"{minutes}m"
        else:
            return f"{seconds}s"

    def format_pipelines(self, data: Any) -> str:
        """格式化流水线数据为 JSON"""
        result = {"type": "pipes", "d": []}

        if isinstance(data, dict):
            result["d"] = [data]
        elif isinstance(data, list):
            result["d"] = data

        if result["d"]:
            platforms = Counter(p.get("platform", "Unknown") for p in result["d"])
            statuses = Counter(p.get("status", "Unknown") for p in result["d"])

            result["sum"] = {
                FieldMapping.TOTAL_DEVICES: len(result["d"]),
                "by_platform": dict(platforms),
                "by_status": dict(statuses),
            }

        return self._to_json(result)

    def format_tasks(self, data: Any) -> str:
        """格式化任务数据为 JSON"""
        result = {"type": "tasks", "d": []}

        if isinstance(data, dict):
            task_list = data.get("data", {}).get("list", [])
            total_count = data.get("data", {}).get("count", 0)
        elif isinstance(data, list):
            task_list = data
            total_count = len(data)
        else:
            task_list = []
            total_count = 0

        result["d"] = task_list

        if task_list:
            statuses = Counter(t.get("status", "Unknown") for t in task_list)

            result["sum"] = {
                "show": len(task_list),
                FieldMapping.TOTAL_DEVICES: total_count,
                "by_status": dict(statuses),
            }

        return self._to_json(result)

    def format_devices(self, data: Any) -> str:
        """格式化设备数据为 JSON"""
        result = {"type": "devs", "d": []}

        if isinstance(data, list):
            devices = data
        elif isinstance(data, dict) and "data" in data:
            devices = data["data"]
        else:
            devices = []

        result["d"] = devices

        if devices:
            statuses = Counter(d.get("status", "Unknown") for d in devices)
            platforms = Counter(d.get("platform", "Unknown") for d in devices)

            result["sum"] = {
                FieldMapping.TOTAL_DEVICES: len(devices),
                "by_status": dict(statuses),
                "by_platform": dict(platforms),
            }

        return self._to_json(result)

    def format_cases(self, data: Any) -> str:
        """格式化用例数据为 JSON"""
        result = {"type": "cases", "d": []}

        if isinstance(data, list):
            cases = data
        elif isinstance(data, dict) and "data" in data:
            cases = data["data"]
        else:
            cases = []

        result["d"] = cases

        if cases:
            case_types = Counter(c.get("type", "Unknown") for c in cases)
            priorities = Counter(c.get("priority", "Unknown") for c in cases)

            result["sum"] = {
                FieldMapping.TOTAL_DEVICES: len(cases),
                "by_type": dict(case_types),
                "by_priority": dict(priorities),
            }

        return self._to_json(result)

    def format_packages(self, data: Any) -> str:
        """格式化包体数据为 JSON"""
        result = {"type": "pkgs", "d": []}

        if isinstance(data, list):
            packages = data
        elif isinstance(data, dict) and "data" in data:
            packages = data["data"]
        else:
            packages = []

        result["d"] = packages

        if packages:
            platforms = Counter(p.get("platform", "Unknown") for p in packages)

            result["sum"] = {FieldMapping.TOTAL_DEVICES: len(packages), "by_platform": dict(platforms)}

        return self._to_json(result)

    def format_builds(self, data: Any) -> str:
        """格式化构建数据为 JSON"""
        result = {"type": "build", "d": data}
        return self._to_json(result)

    def format_config(self, data: Any) -> str:
        """格式化配置数据为 JSON"""
        result = {"type": "cfg", "d": data}
        return self._to_json(result)

    def format_logs(self, data: Any) -> str:
        """格式化日志数据为 JSON"""
        result = {"type": "logs", "d": []}

        if isinstance(data, list):
            result["d"] = data
            result["sum"] = {FieldMapping.TOTAL_DEVICES: len(data), "show": len(result["d"])}

        return self._to_json(result)

    def format_device_executions(self, task_detail: Any, device_id: int = None) -> str:
        """
        格式化用例设备执行情况为 JSON（AI 分析专用 - 优化版）

        Args:
            task_detail: 任务详情数据（包含 caseDetails）
            device_id: 设备ID（如果指定了特定设备）

        Returns:
            格式化的 JSON 字符串
        """
        from utils.perfeye_cache import PerfeyeCache

        cache = PerfeyeCache()
        result = {"type": "dev_exec", "task": {}, "sum": {}, "cases": []}

        # 解析任务详情
        if not task_detail or "caseDetails" not in task_detail:
            result["error"] = "无法解析任务详情"
            return self._to_json(result)

        case_details = task_detail.get("caseDetails", [])
        if not case_details:
            result["error"] = "暂无用例数据"
            return self._to_json(result)

        # ========== 任务概要 ==========
        result["task"] = {
            FieldMapping.BUILD_ID: task_detail.get("buildId"),
            FieldMapping.BUILD_NAME: task_detail.get("buildName"),
            FieldMapping.PIPELINE_NAME: task_detail.get("pipelineName"),
            FieldMapping.STATUS: task_detail.get("status"),
            FieldMapping.START_TIME: task_detail.get("startTime"),
            FieldMapping.END_TIME: task_detail.get("endTime"),
            FieldMapping.EXECUTE_TIME: task_detail.get("executeTime"),
            FieldMapping.DATA_POINTS: len(case_details),
        }

        # ========== 统计摘要 ==========
        case_status_count = {}
        all_devices = set()
        online_devices = set()
        offline_devices = set()
        success_devices = set()
        failed_devices = set()

        for case in case_details:
            status = case.get("status", "UNKNOWN")
            case_status_count[status] = case_status_count.get(status, 0) + 1

            for device in case.get("deviceDetail", []):
                dev_id = device.get("deviceId")
                dev_status = device.get("status")
                dev_online_status = device.get("deviceStatus", 1)

                all_devices.add(dev_id)

                if dev_online_status == 0:
                    offline_devices.add(dev_id)
                else:
                    online_devices.add(dev_id)

                if dev_status == "SUCCESS":
                    success_devices.add(dev_id)
                elif dev_status == "FAILED":
                    failed_devices.add(dev_id)

        result["sum"] = {
            FieldMapping.CASE_STATUS: case_status_count,
            FieldMapping.DEVICE_STATUS: {
                FieldMapping.TOTAL_DEVICES: len(all_devices),
                FieldMapping.ONLINE: len(online_devices),
                FieldMapping.OFFLINE: len(offline_devices),
                FieldMapping.SUCCESS: len(success_devices),
                FieldMapping.FAILED: len(failed_devices),
            },
        }

        # ========== 用例详细执行情况 ==========
        for case in case_details:
            case_data = {
                FieldMapping.CASE_ID: case.get("caseId"),
                FieldMapping.CASE_NAME: case.get("caseName"),
                FieldMapping.STATUS: case.get("status"),
                FieldMapping.DEVICES: [],
            }

            device_details = case.get("deviceDetail", [])

            if device_id:
                device_details = [d for d in device_details if d.get("deviceId") == device_id]

            case_fps_list = []
            case_jank_list = []
            case_memory_list = []

            for device in device_details:
                device_data = {
                    FieldMapping.DEVICE_ID: device.get("deviceId"),
                    FieldMapping.DEVICE_NAME: device.get("deviceName"),
                    FieldMapping.STATUS: device.get("status"),
                    "st": device.get("startTime"),
                    "et": device.get("endTime"),
                    "dst": device.get("deviceStatus", 1),
                }

                # 提取性能数据
                perfeye_data = device.get("perfeyeData")
                if perfeye_data:
                    if isinstance(perfeye_data, str):
                        try:
                            perfeye_dict = json.loads(perfeye_data)
                        except:
                            perfeye_dict = {}
                    elif isinstance(perfeye_data, dict):
                        perfeye_dict = perfeye_data
                    else:
                        perfeye_dict = {}

                    peak_memory = self._parse_float(
                        perfeye_dict.get("LabelMemory.PeakMemory(MB)")
                        or perfeye_dict.get("LabelMemory.PeakMemoryDeposit(MB)")
                    )

                    fps = self._parse_float(perfeye_dict.get("LabelFPS.TP90"))
                    jank = self._parse_float(perfeye_dict.get("LabelFPS.Jank(/10min)"))

                    device_data["perf"] = {
                        FieldMapping.FPS_TP90: fps,
                        FieldMapping.JANK_PER_10MIN: jank,
                        FieldMapping.PEAK_MEMORY_MB: peak_memory,
                    }

                    if fps is not None:
                        case_fps_list.append(fps)
                    if jank is not None:
                        case_jank_list.append(jank)
                    if peak_memory is not None:
                        case_memory_list.append(peak_memory)
                else:
                    device_data["pltf"] = device.get("platform", "N/A")
                    device_data["sv"] = device.get("systemVersion", "N/A")

                case_data[FieldMapping.DEVICES].append(device_data)

            # 计算性能统计
            if case_fps_list or case_jank_list or case_memory_list:
                case_data[FieldMapping.PERFORMANCE] = {}

                if case_fps_list:
                    case_data[FieldMapping.PERFORMANCE][FieldMapping.FPS_TP90] = {
                        FieldMapping.AVG: round(sum(case_fps_list) / len(case_fps_list), 2),
                        FieldMapping.MIN: round(min(case_fps_list), 2),
                        FieldMapping.MAX: round(max(case_fps_list), 2),
                    }

                if case_jank_list:
                    case_data[FieldMapping.PERFORMANCE][FieldMapping.JANK_PER_10MIN] = {
                        FieldMapping.AVG: round(sum(case_jank_list) / len(case_jank_list), 2),
                        FieldMapping.MIN: round(min(case_jank_list), 2),
                        FieldMapping.MAX: round(max(case_jank_list), 2),
                    }

                if case_memory_list:
                    case_data[FieldMapping.PERFORMANCE][FieldMapping.PEAK_MEMORY_MB] = {
                        FieldMapping.AVG: round(sum(case_memory_list) / len(case_memory_list), 2),
                        FieldMapping.MIN: round(min(case_memory_list), 2),
                        FieldMapping.MAX: round(max(case_memory_list), 2),
                    }

            result["cases"].append(case_data)

        # ========== 稳定性测试专用预处理 ==========
        # 如果是稳定性测试，生成额外的统计数据
        if task_detail.get("buildName", "").find("稳定性") != -1:
            preprocessor = StabilityPreprocessor()
            stability_stats = preprocessor.preprocess(task_detail)
            result["stability"] = stability_stats

        # 保存 Perfeye UUID 到缓存
        task_id = task_detail.get("buildId")
        if task_id:
            uuids = cache.extract_uuids_from_device_executions(task_detail)
            if uuids:
                perfeye_file = cache.save_task_uuids(task_id, uuids)
                result["perfeye_file"] = perfeye_file

        # 添加 legend
        result["_legend"] = self._get_legend()

        return self._to_json(result)

    def format_task_discovery(
        self,
        tasks_data: Any,
        task_name: str,
        start_time: str,
        end_time: str
    ) -> str:
        """
        格式化任务发现结果 - 精简任务列表供用户选择

        只保留关键字段
        """
        if isinstance(tasks_data, dict):
            task_list = tasks_data.get("data", {}).get("list", [])
            total_count = tasks_data.get("data", {}).get("count", 0)
        elif isinstance(tasks_data, list):
            task_list = tasks_data
            total_count = len(tasks_data)
        else:
            task_list = []
            total_count = 0

        simplified_tasks = []
        for task in task_list:
            simplified_task = {
                FieldMapping.BUILD_ID: task.get("buildId"),
                FieldMapping.BUILD_NAME: task.get("buildName"),
                FieldMapping.PIPELINE_ID: task.get("pipelineId"),
                FieldMapping.PIPELINE_NAME: task.get("pipelineName"),
                FieldMapping.STATUS: task.get("status"),
                "st": task.get("startTime"),
                "et": task.get("endTime"),
                "tf": self._format_duration(task.get("executeTime", 0))
            }
            simplified_tasks.append(simplified_task)

        status_dist = Counter(t.get("status") for t in task_list)
        pipeline_dist = Counter(t.get("pipelineId") for t in task_list if t.get("pipelineId"))

        from utils.id_resolver import IDResolver
        resolver = IDResolver()
        days_range = resolver.calculate_date_range_days(start_time, end_time)

        result = {
            "type": "task_disc",
            "q": {
                FieldMapping.BUILD_NAME: task_name,
                "st": start_time,
                "et": end_time
            },
            "sum": {
                "total_tasks": total_count,
                "show": len(task_list),
                "status_dist": dict(status_dist),
                "unique_pipes": len(pipeline_dist),
                "days": days_range
            },
            "tasks": simplified_tasks
        }

        return self._to_json(result)

    def format_performance_trend_v2(self, data: Any, pipeline_id: int = None, pipeline_name: str = "") -> str:
        """
        格式化性能趋势数据 V2 - 优化版

        Args:
            data: 原始性能趋势数据
            pipeline_id: 流水线 ID
            pipeline_name: 流水线名称

        Returns:
            格式化的 JSON 字符串
        """
        from utils.perfeye_cache import PerfeyeCache
        cache = PerfeyeCache()

        if isinstance(data, dict):
            trend_list = data.get("data", [])
        elif isinstance(data, list):
            trend_list = data
        else:
            trend_list = []

        # 简化数据（使用新字段名）
        simplified_list = []
        for item in trend_list:
            simplified_item = {
                "ct": item.get("createTime"),
                FieldMapping.CASE_ID: item.get("caseId"),
                FieldMapping.CASE_NAME: item.get("caseName"),
                FieldMapping.DEVICE_ID: item.get("deviceId"),
                FieldMapping.DEVICE_NAME: item.get("deviceName"),
                FieldMapping.FPS_TP90: self._parse_float(item.get("LabelFPS.TP90")),
                FieldMapping.JANK_PER_10MIN: self._parse_float(item.get("LabelFPS.Jank(/10min)")),
                FieldMapping.PEAK_MEMORY_MB: self._parse_float(item.get("LabelMemory.PeakMemory(MB)")),
            }
            simplified_list.append(simplified_item)

        # 调用预处理器（已优化版）
        from utils.trend_preprocessor import TrendPreprocessor
        preprocessor = TrendPreprocessor()
        structured_data = preprocessor.preprocess(simplified_list)

        # 更新元数据
        if pipeline_id:
            structured_data["meta"][FieldMapping.PIPELINE_ID] = pipeline_id
        if pipeline_name:
            structured_data["meta"][FieldMapping.PIPELINE_NAME] = pipeline_name

        # 保存 Perfeye UUID 到缓存（增强版）
        if pipeline_id:
            # 提取时间范围
            date_range = None
            if structured_data.get("meta"):
                date_range = {
                    "start": structured_data["meta"].get("start_date"),
                    "end": structured_data["meta"].get("end_date")
                }

            perfeye_file = cache.save_trend_uuids_enhanced(
                pipeline_id=pipeline_id,
                trend_list=trend_list,
                pipeline_name=pipeline_name or "",
                date_range=date_range
            )
            structured_data["perfeye_file"] = perfeye_file

        return self._to_json(structured_data)

    def format_id_discovery(self, result: Any) -> str:
        """格式化ID发现结果为JSON"""
        output = {
            "type": "id_disc",
            "result": result
        }
        return self._to_json(output)

    def format_stability_task(self, task_detail: Any) -> str:
        """
        格式化稳定性任务详情（专用格式 - 不包含 case 详情）

        专为 AI Agent 分析设计，输出结构化统计数据而非原始用例数据。

        Args:
            task_detail: 任务详情数据（包含 caseDetails）

        Returns:
            格式化的 JSON 字符串，包含：
            - type: "stability_task"
            - task: 任务基本信息
            - sum: 设备状态汇总
            - stability: 预处理的统计数据（执行时长、内存、崩溃、配置）
            - _legend: 字段说明
        """
        from utils.stability_preprocessor import StabilityPreprocessor

        result = {
            "type": "stability_task",
            "task": {},
            "sum": {},
            "stability": {},
        }

        # 解析任务详情
        if not task_detail:
            result["error"] = "无法解析任务详情"
            return self._to_json(result)

        # ========== 任务概要 ==========
        result["task"] = {
            FieldMapping.BUILD_ID: task_detail.get("buildId"),
            FieldMapping.BUILD_NAME: task_detail.get("buildName"),
            FieldMapping.PIPELINE_ID: task_detail.get("pipelineId"),
            FieldMapping.PIPELINE_NAME: task_detail.get("pipelineName"),
            FieldMapping.STATUS: task_detail.get("status"),
            FieldMapping.START_TIME: task_detail.get("startTime"),
            FieldMapping.END_TIME: task_detail.get("endTime"),
            FieldMapping.EXECUTE_TIME: task_detail.get("executeTime"),
        }

        case_details = task_detail.get("caseDetails", [])

        # ========== 设备状态汇总 ==========
        case_status_count = {}
        all_devices = set()
        online_devices = set()
        offline_devices = set()
        success_devices = set()
        failed_devices = set()

        for case in case_details:
            status = case.get("status", "UNKNOWN")
            case_status_count[status] = case_status_count.get(status, 0) + 1

            for device in case.get("deviceDetail", []):
                dev_id = device.get("deviceId")
                dev_status = device.get("status")
                dev_online_status = device.get("deviceStatus", 1)

                all_devices.add(dev_id)

                if dev_online_status == 0:
                    offline_devices.add(dev_id)
                else:
                    online_devices.add(dev_id)

                if dev_status == "SUCCESS":
                    success_devices.add(dev_id)
                elif dev_status == "FAILED":
                    failed_devices.add(dev_id)

        result["sum"] = {
            FieldMapping.CASE_STATUS: case_status_count,
            FieldMapping.DEVICE_STATUS: {
                FieldMapping.TOTAL_DEVICES: len(all_devices),
                FieldMapping.ONLINE: len(online_devices),
                FieldMapping.OFFLINE: len(offline_devices),
                FieldMapping.SUCCESS: len(success_devices),
                FieldMapping.FAILED: len(failed_devices),
            },
        }

        # ========== 稳定性统计数据（预处理）==========
        preprocessor = StabilityPreprocessor()
        result["stability"] = preprocessor.preprocess(task_detail)

        # 添加 legend
        result["_legend"] = self._get_legend()

        return self._to_json(result)
