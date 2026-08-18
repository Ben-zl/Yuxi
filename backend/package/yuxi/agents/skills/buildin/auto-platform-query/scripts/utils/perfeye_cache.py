"""
Perfeye UUID 缓存工具

将 Perfeye UUID 数据分离到本地缓存文件，减少 JSON 输出的 token 使用量。
"""

import json
import os
from pathlib import Path
from typing import Dict, Any, Optional


class PerfeyeCache:
    """Perfeye UUID 缓存管理器"""

    def __init__(self, cache_dir: str = None):
        """
        初始化缓存管理器

        Args:
            cache_dir: 缓存目录路径，默认为 .cache
        """
        skill_dir = Path(__file__).parent.parent.parent
        if cache_dir:
            self.cache_dir = Path(cache_dir)
        else:
            self.cache_dir = skill_dir / ".cache"

        # 确保缓存目录存在
        self.cache_dir.mkdir(exist_ok=True)

    def _get_task_cache_path(self, task_id: int) -> Path:
        """获取任务对应的缓存文件路径"""
        return self.cache_dir / f"perfeye_{task_id}.json"

    def _get_trend_cache_path(self, pipeline_id: int) -> Path:
        """获取趋势数据对应的缓存文件路径"""
        return self.cache_dir / f"perfeye_trend_{pipeline_id}.json"

    def _get_task_cache_path(self, task_id: int) -> Path:
        """获取任务对应的缓存文件路径"""
        return self.cache_dir / f"perfeye_{task_id}.json"

    def save_task_uuids(self, task_id: int, uuids: Dict[str, Any]) -> str:
        """
        保存任务的 Perfeye UUID 数据

        Args:
            task_id: 任务 ID
            uuids: UUID 数据字典

        Returns:
            缓存文件的相对路径（用于 JSON 输出）
        """
        cache_path = self._get_task_cache_path(task_id)

        cache_data = {
            "task_id": task_id,
            "created_at": self._get_timestamp(),
            "uuids": uuids
        }

        with open(cache_path, 'w', encoding='utf-8') as f:
            json.dump(cache_data, f, ensure_ascii=False, indent=2)

        # 返回相对于技能目录的路径，用于在输出中引用
        return f".cache/perfeye_{task_id}.json"

    def save_trend_uuids(self, pipeline_id: int, uuids: Dict[str, Any]) -> str:
        """
        保存趋势数据的 Perfeye UUID 数据

        Args:
            pipeline_id: 流水线 ID
            uuids: UUID 数据字典

        Returns:
            缓存文件的相对路径（用于 JSON 输出）
        """
        cache_path = self._get_trend_cache_path(pipeline_id)

        cache_data = {
            "pipeline_id": pipeline_id,
            "created_at": self._get_timestamp(),
            "uuids": uuids
        }

        with open(cache_path, 'w', encoding='utf-8') as f:
            json.dump(cache_data, f, ensure_ascii=False, indent=2)

        return f".cache/perfeye_trend_{pipeline_id}.json"

    def load_uuids(self, cache_path: str) -> Optional[Dict[str, Any]]:
        """
        从缓存加载 UUID 数据

        Args:
            cache_path: 缓存文件路径

        Returns:
            缓存数据字典，如果文件不存在返回 None
        """
        # 如果是相对路径，转换为绝对路径
        if not os.path.isabs(cache_path):
            cache_path = self.cache_dir / cache_path

        if not os.path.exists(cache_path):
            return None

        with open(cache_path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def extract_uuids_from_device_executions(self, task_detail: Dict[str, Any]) -> Dict[str, Any]:
        """
        从设备执行数据中提取 UUID

        Args:
            task_detail: 任务详情数据

        Returns:
            UUID 字典 {case_name: {device_id: uuid}}
        """
        uuids = {}

        for case in task_detail.get("caseDetails", []):
            case_name = case.get("caseName")
            if not case_name:
                continue

            for device in case.get("deviceDetail", []):
                device_id = device.get("deviceId")
                report_data = device.get("reportData", {})

                if isinstance(report_data, str):
                    try:
                        report_data = json.loads(report_data)
                    except:
                        report_data = {}

                perfeye_uuid = report_data.get("perfeye") if report_data else None
                if perfeye_uuid:
                    if case_name not in uuids:
                        uuids[case_name] = {}
                    uuids[case_name][f"di_{device_id}"] = perfeye_uuid

        return uuids

    def extract_uuids_from_trend_data(self, trend_list: list) -> Dict[str, Any]:
        """
        从趋势数据中提取 UUID

        Args:
            trend_list: 趋势数据列表

        Returns:
            UUID 字典 {case_name: {device_id: uuid}}
        """
        uuids = {}

        for item in trend_list:
            case_name = item.get("caseName")
            device_id = item.get("deviceId")
            perfeye_uuid = item.get("perfeye")

            if case_name and device_id and perfeye_uuid:
                if case_name not in uuids:
                    uuids[case_name] = {}
                uuids[case_name][f"di_{device_id}"] = perfeye_uuid

        return uuids

    def _get_timestamp(self) -> str:
        """获取当前时间戳"""
        from datetime import datetime
        return datetime.now().isoformat()

    def clear_cache(self) -> int:
        """
        清空所有缓存文件

        Returns:
            删除的文件数量
        """
        count = 0
        for file in self.cache_dir.glob("perfeye_*.json"):
            file.unlink()
            count += 1
        return count

    # ========== 增强版缓存功能 ==========

    def save_trend_uuids_enhanced(self, pipeline_id: int, trend_list: list,
                                   pipeline_name: str = "", date_range: dict = None) -> str:
        """
        保存趋势数据的 Perfeye UUID 数据（增强版）

        包含详细的缓存结构：
        - metadata: 流水线信息、时间范围
        - cases: 按用例组织的执行记录列表
        - trend_summary: 每个用例+设备的趋势摘要

        Args:
            pipeline_id: 流水线 ID
            trend_list: 趋势数据列表（来自 API，原始格式）
            pipeline_name: 流水线名称
            date_range: 时间范围 {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}

        Returns:
            缓存文件的相对路径
        """
        cache_path = self._get_trend_cache_path(pipeline_id)

        # 按用例组织数据
        cases_data = {}
        for item in trend_list:
            # 使用原始 API 返回的字段名
            case_name = item.get("caseName")
            device_id = item.get("deviceId")
            perfeye_uuid = item.get("perfeye")
            create_time = item.get("createTime")
            # 原始 API 返回的字段名带前缀
            fps = item.get("LabelFPS.TP90")
            jank = item.get("LabelFPS.Jank(/10min)")
            mem = item.get("LabelMemory.PeakMemory(MB)")
            task_id = item.get("taskId")
            device_name_raw = item.get("deviceName", "")
            case_id = item.get("caseId")

            if not all([case_name, device_id, perfeye_uuid]):
                continue

            if case_name not in cases_data:
                cases_data[case_name] = {
                    "ci": case_id,
                    "devices": {}
                }

            device_key = f"di_{device_id}"
            if device_key not in cases_data[case_name]["devices"]:
                device_name = device_name_raw.replace(" | ", " - ")
                cases_data[case_name]["devices"][device_key] = {
                    "device_name": device_name,
                    "config": self._classify_device_config(device_name),
                    "executions": [],
                    "trend_summary": {}
                }

            # 添加执行记录
            execution = {
                "date": create_time,
                "task_id": task_id,
                "uuid": perfeye_uuid,
                "fps": self._safe_float(fps),
                "jank": self._safe_float(jank),
                "mem": self._safe_float(mem)
            }
            cases_data[case_name]["devices"][device_key]["executions"].append(execution)

        # 生成趋势摘要
        for case_name, case_data in cases_data.items():
            for device_key, device_data in case_data["devices"].items():
                executions = device_data["executions"]
                if len(executions) > 1:
                    # 按日期排序
                    sorted_executions = sorted(executions, key=lambda x: x.get("date", ""))
                    device_data["trend_summary"] = {
                        "earliest_uuid": sorted_executions[0]["uuid"],
                        "latest_uuid": sorted_executions[-1]["uuid"],
                        "earliest_date": sorted_executions[0]["date"],
                        "latest_date": sorted_executions[-1]["date"],
                        "earliest_fps": sorted_executions[0].get("fps"),
                        "latest_fps": sorted_executions[-1].get("fps"),
                        "earliest_jank": sorted_executions[0].get("jank"),
                        "latest_jank": sorted_executions[-1].get("jank")
                    }

        # 构建缓存数据
        cache_data = {
            "metadata": {
                "pipeline_id": pipeline_id,
                "pipeline_name": pipeline_name,
                "generated_at": self._get_timestamp(),
                "date_range": date_range or {"start": None, "end": None}
            },
            "cases": cases_data
        }

        with open(cache_path, 'w', encoding='utf-8') as f:
            json.dump(cache_data, f, ensure_ascii=False, indent=2)

        return f".cache/perfeye_trend_{pipeline_id}.json"

    def load_trend_uuids_enhanced(self, pipeline_id: int) -> Optional[Dict[str, Any]]:
        """
        加载趋势数据的增强版缓存

        Args:
            pipeline_id: 流水线 ID

        Returns:
            缓存数据字典，如果不存在返回 None
        """
        cache_path = self._get_trend_cache_path(pipeline_id)
        if not cache_path.exists():
            return None

        with open(cache_path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def get_uuids_for_comparison(self, pipeline_id: int, case_name: str, device_id: str) -> Optional[Dict[str, str]]:
        """
        获取用于对比分析的 UUID

        Args:
            pipeline_id: 流水线 ID
            case_name: 用例名称
            device_id: 设备 ID (格式如 "di_1135")

        Returns:
            {"earliest": uuid, "latest": uuid} 或 None
        """
        cache = self.load_trend_uuids_enhanced(pipeline_id)
        if not cache:
            return None

        try:
            device_data = cache["cases"][case_name]["devices"][device_id]
            summary = device_data.get("trend_summary", {})
            if summary:
                return {
                    "earliest": summary.get("earliest_uuid"),
                    "latest": summary.get("latest_uuid")
                }
            return None
        except KeyError:
            return None

    def get_all_problem_cases(self, pipeline_id: int, evaluator=None) -> list:
        """
        获取所有有问题的用例（使用 PerformanceEvaluator 评估）

        Args:
            pipeline_id: 流水线 ID
            evaluator: PerformanceEvaluator 实例（可选）

        Returns:
            问题用例列表 [{"case_name", "device_id", "severity", "issues"}]
        """
        cache = self.load_trend_uuids_enhanced(pipeline_id)
        if not cache:
            return []

        # 延迟导入避免循环依赖
        if evaluator is None:
            from .performance_evaluator import PerformanceEvaluator, PerformanceMetrics
            evaluator = PerformanceEvaluator()

        problems = []
        for case_name, case_data in cache.get("cases", {}).items():
            for device_id, device_data in case_data.get("devices", {}).items():
                summary = device_data.get("trend_summary", {})
                if not summary:
                    continue

                metrics = PerformanceMetrics(
                    fps_earliest=summary.get("earliest_fps"),
                    fps_latest=summary.get("latest_fps"),
                    jank_earliest=summary.get("earliest_jank"),
                    jank_latest=summary.get("latest_jank")
                )

                result = evaluator.evaluate(metrics)
                if not result.is_normal:
                    problems.append({
                        "case_name": case_name,
                        "device_id": device_id,
                        "device_name": device_data.get("device_name"),
                        "severity": result.severity.value,
                        "issues": [issue.to_dict() for issue in result.issues],
                        "uuids": {
                            "earliest": summary.get("earliest_uuid"),
                            "latest": summary.get("latest_uuid")
                        }
                    })

        return problems

    def _classify_device_config(self, device_name: str) -> str:
        """分类设备配置级别"""
        config_keywords = {
            "高端配置": ["RTX3080", "RTX3090", "RTX4080", "RTX4090", "RX7900 XT"],
            "中高端配置": ["RTX2060", "RTX2070", "RTX3060", "RTX3070", "RX6800 XT"],
            "中端配置": ["GTX1060", "GTX1660", "RX6600 XT", "GTX1070"],
            "中低端配置": ["GTX1050", "GTX1650", "RX5500 XT", "GTX1650 SUPER"],
            "低端笔记本": ["GTX960", "GTX750Ti", "RX560", "940MX"],
            "低端配置": ["GTX650", "GT730", "GT740", "GT1030"]
        }

        for level, keywords in config_keywords.items():
            for keyword in keywords:
                if keyword in device_name:
                    return level
        return "未知配置"

    def _safe_float(self, value) -> Optional[float]:
        """安全转换为浮点数"""
        if value is None:
            return None
        try:
            return float(value)
        except (ValueError, TypeError):
            return None
