"""
性能趋势数据预处理器（优化版）

对原始性能趋势数据进行预处理，按用例、设备维度组织数据，
并计算趋势变化值（first/last/change/change_percent）。
优化版：使用缩写字段名，减少 token 消耗。
"""

from typing import List, Dict, Any, Optional
from collections import defaultdict


# 字段映射常量
class FM:
    """Field Mapping - 字段映射常量"""
    # 性能指标
    F90 = "f90"           # FPS TP90 - 第90百分位帧率
    JK = "jk"             # Jank/10min - 每10分钟卡顿次数
    MEM = "mem"           # Memory MB - 内存峰值(MB)

    # 趋势字段（优化命名）
    FV = "fv"             # First Value - 最早值（时间轴起点，首次执行）
    LV = "lv"             # Last Value - 最新值（时间轴末端，最近执行）
    CH = "ch"             # Change - 变化量 = 最新值 - 最早值（正数=上升，负数=下降）
    CP = "cp"             # Change Percent - 变化百分比 = (最新值-最早值)/最早值*100

    # 统计字段
    N = "n"               # Count - 数据点数量
    AVG = "avg"           # Average - 平均值
    MIN = "min"           # Minimum - 最小值
    MAX = "max"           # Maximum - 最大值

    # ID 字段
    CI = "ci"             # Case ID - 用例ID
    DI = "di"             # Device ID - 设备ID
    DN = "dn"             # Device Name - 设备名称
    CN = "cn"             # Case Name - 用例名称
    PID = "pid"           # Pipeline ID - 流水线ID
    PN = "pn"             # Pipeline Name - 流水线名称

    # 其他
    CT = "ct"             # Create Time - 创建时间
    CFG = "cfg"           # Config Level - 配置等级


class TrendPreprocessor:
    """性能趋势数据预处理器（优化版）"""

    def __init__(self):
        self.device_config_keywords = {
            "高端配置": ["RTX3080", "RTX3090", "RX7900 XT"],
            "中高端配置": ["RTX2060", "RTX2070", "RX6800 XT"],
            "中端配置": ["GTX1060", "GTX1660", "RX6600 XT"],
            "中低端配置": ["GTX1050", "GTX1650", "RX5500 XT"],
            "低端笔记本": ["GTX960", "GTX750Ti", "RX560"],
            "低端配置": ["GTX650", "GT730", "GT740"]
        }

    def preprocess(self, raw_data: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        预处理原始数据，返回结构化数据

        Args:
            raw_data: 原始性能趋势数据列表

        Returns:
            结构化的性能趋势数据
        """
        if not raw_data:
            return self._empty_result()

        # 按日期排序
        sorted_data = self._sort_by_date(raw_data)

        # 获取日期范围
        date_range = self._get_date_range(sorted_data)

        # 获取唯一用例、设备、日期
        unique_cases = self._get_unique_values(sorted_data, FM.CN)
        unique_devices = self._get_unique_values(sorted_data, FM.DN)
        unique_dates = self._get_unique_values(sorted_data, FM.CT)

        # 提取性能指标数据
        fps_data = self._extract_metric(sorted_data, FM.F90)
        jank_data = self._extract_metric(sorted_data, FM.JK)
        memory_data = self._extract_metric(sorted_data, FM.MEM)

        # 计算合并后的 overall（包含 trend + stats）
        overall = {
            FM.F90: self._calculate_overall(fps_data),
            FM.JK: self._calculate_overall(jank_data),
            FM.MEM: self._calculate_overall(memory_data)
        }

        # 按日期分组
        by_date = self._group_by_date(sorted_data)

        # 按用例分组
        by_case = self._group_by_case(sorted_data)

        # 构建设备元数据索引
        device_metadata = self._build_device_metadata(sorted_data)

        # 构建结果
        result = {
            "type": "perf_trend",
            "meta": {
                FM.PID: None,
                FM.PN: "",
                "days": date_range.get("days", 0)
            },
            "sum": {
                FM.N: len(sorted_data),
                "cases": len(unique_cases),
                "devs": len(unique_devices),
                "dates": len(unique_dates)
            },
            "bench": {FM.F90: 60, FM.JK: 10},
            "overall": overall,
            "by_case": by_case,
            "by_date": by_date,
            "devs": device_metadata,
            "_legend": self._get_legend()
        }

        return result

    def _empty_result(self) -> Dict[str, Any]:
        """返回空结果"""
        return {
            "type": "perf_trend",
            "meta": {FM.PID: None, FM.PN: "", "days": 0},
            "sum": {FM.N: 0, "cases": 0, "devs": 0, "dates": 0},
            "bench": {FM.F90: 60, FM.JK: 10},
            "overall": {FM.F90: {}, FM.JK: {}, FM.MEM: {}},
            "by_case": {},
            "by_date": {},
            "devs": {},
            "_legend": self._get_legend()
        }

    def _get_legend(self) -> Dict[str, str]:
        """获取字段映射说明（优化版：更清晰的语义说明）"""
        return {
            FM.F90: "FPS TP90 - 第90百分位帧率",
            FM.JK: "Jank/10min - 每10分钟卡顿次数",
            FM.MEM: "Memory MB - 内存峰值(MB)",
            FM.FV: "最早值 First Value - 时间轴起点的值（首次执行）",
            FM.LV: "最新值 Last Value - 时间轴末端的值（最近执行）",
            FM.CH: "变化量 Change = 最新值 - 最早值（正数=上升，负数=下降）",
            FM.CP: "变化百分比 Change% = (最新值-最早值)/最早值*100",
            FM.N: "数据点数量 Count",
            FM.CI: "用例ID Case ID",
            FM.DI: "设备ID Device ID",
            FM.DN: "设备名称 Device Name",
            FM.CN: "用例名称 Case Name",
            FM.PID: "流水线ID Pipeline ID",
            FM.PN: "流水线名称 Pipeline Name",
            FM.CT: "创建时间 Create Time",
            FM.CFG: "配置等级 Config Level",
            FM.AVG: "平均值 Average",
            FM.MIN: "最小值 Minimum",
            FM.MAX: "最大值 Maximum"
        }

    def _format_device_name(self, name: str) -> str:
        """格式化设备名称：将 | 替换为 -"""
        return name.replace("|", "-")

    def _classify_device_config(self, name: str) -> str:
        """分类设备配置级别"""
        for level, keywords in self.device_config_keywords.items():
            for keyword in keywords:
                if keyword in name:
                    return level
        return "未知配置"

    def _sort_by_date(self, data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """按 createTime 排序数据"""
        return sorted(data, key=lambda x: x.get(FM.CT, ""))

    def _get_date_range(self, data: List[Dict[str, Any]]) -> Dict[str, Any]:
        """获取日期范围"""
        if not data:
            return {"days": 0}

        dates = [item.get("createTime") for item in data]
        dates = sorted(set(d for d in dates if d))

        if not dates:
            return {"days": 0}

        return {"days": len(dates)}

    def _get_unique_values(self, data: List[Dict[str, Any]], key: str) -> set:
        """获取指定字段的唯一值集合"""
        return set(item.get(key) for item in data if item.get(key))

    def _extract_metric(self, data: List[Dict[str, Any]], metric_key: str) -> List[float]:
        """提取指定指标的值列表"""
        values = []
        for item in data:
            value = item.get(metric_key)
            if value is not None:
                try:
                    values.append(float(value))
                except (ValueError, TypeError):
                    pass
        return values

    def _calculate_overall(self, values: List[float]) -> Dict[str, Any]:
        """
        计算 overall 统计（合并 trend 和 stats）

        fv = 第一个值（最早数据，First Value）
        lv = 最后一个值（最新数据，Last Value）
        ch = lv - fv（从最早到最新的变化，正数=上升，负数=下降）
        cp = ch / fv * 100（基于最早值的百分比变化）

        Returns:
            {fv, lv, ch, cp, avg, min, max, n}
        """
        if not values:
            return {FM.FV: None, FM.LV: None, FM.CH: None, FM.CP: None, FM.AVG: 0, FM.MIN: 0, FM.MAX: 0, FM.N: 0}

        # fv = 第一个值（最早），lv = 最后一个值（最新）
        fv = round(float(values[0]), 2)   # 时间顺序的第一个值（最早值，First Value）
        lv = round(float(values[-1]), 2)  # 时间顺序的最后一个值（最新值，Last Value）

        # 变化量 = 最新值 - 最早值（正数表示上升，负数表示下降）
        ch = round(lv - fv, 2)

        # 变化百分比 = 变化量 / 最早值 * 100
        if fv != 0:
            cp = round((ch / fv) * 100, 2)
        else:
            cp = None

        return {
            FM.FV: fv,  # 最早值（First Value）
            FM.LV: lv,  # 最新值（Last Value）
            FM.CH: ch,
            FM.CP: cp,
            FM.AVG: round(sum(values) / len(values), 2),
            FM.MIN: round(min(values), 2),
            FM.MAX: round(max(values), 2),
            FM.N: len(values)
        }

    def _group_by_date(self, data: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        """按日期分组并计算统计"""
        by_date = {}

        # 先分组计数
        for item in data:
            date = item.get("createTime")
            if not date:
                continue
            if date not in by_date:
                by_date[date] = {FM.N: 0}
            by_date[date][FM.N] += 1

        # 计算每日统计
        grouped_by_date = self._group_items(data, "createTime")
        for date, items in grouped_by_date.items():
            fps_values = self._extract_metric(items, FM.F90)
            jank_values = self._extract_metric(items, FM.JK)
            memory_values = self._extract_metric(items, FM.MEM)

            by_date[date][FM.F90] = {
                FM.AVG: round(sum(fps_values) / len(fps_values), 2) if fps_values else 0
            }
            by_date[date][FM.JK] = {
                FM.AVG: round(sum(jank_values) / len(jank_values), 2) if jank_values else 0
            }
            by_date[date][FM.MEM] = {
                FM.AVG: round(sum(memory_values) / len(memory_values), 2) if memory_values else 0
            }

        return by_date

    def _group_by_case(self, data: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        """按用例分组并计算趋势，保留设备分组"""
        by_case = {}

        # 第一遍：统计用例级别的数据点数量
        for item in data:
            case_name = item.get(FM.CN)
            case_id = item.get(FM.CI)

            if not case_name:
                continue

            if case_name not in by_case:
                by_case[case_name] = {
                    FM.CI: case_id,
                    FM.N: 0
                }

            by_case[case_name][FM.N] += 1

        # 第二遍：计算用例级别趋势和设备分组
        grouped_by_case = self._group_items(data, FM.CN)
        for case_name, items in grouped_by_case.items():
            case_id = items[0].get(FM.CI) if items else None
            case_items_by_date = self._sort_by_date(items)

            fps_values = self._extract_metric(case_items_by_date, FM.F90)
            jank_values = self._extract_metric(case_items_by_date, FM.JK)
            memory_values = self._extract_metric(case_items_by_date, FM.MEM)

            # 用例级别趋势
            # fv = 第一个值（最早数据，First Value）
            # lv = 最后一个值（最新数据，Last Value）
            # ch = lv - fv（从最早到最新的变化，正数=上升，负数=下降）
            # cp = ch / fv * 100（基于最早值的百分比变化）
            by_case[case_name][FM.F90] = {
                FM.FV: round(fps_values[0], 2) if fps_values else None,   # 最早值
                FM.LV: round(fps_values[-1], 2) if fps_values else None,  # 最新值
                FM.CH: round(fps_values[-1] - fps_values[0], 2) if len(fps_values) > 1 else None,  # 最新-最早
                FM.CP: round(((fps_values[-1] - fps_values[0]) / fps_values[0] * 100), 2) if len(fps_values) > 1 and fps_values[0] != 0 else None
            }
            by_case[case_name][FM.JK] = {
                FM.FV: round(jank_values[0], 2) if jank_values else None,   # 最早值
                FM.LV: round(jank_values[-1], 2) if jank_values else None,  # 最新值
                FM.CH: round(jank_values[-1] - jank_values[0], 2) if len(jank_values) > 1 else None,  # 最新-最早
                FM.CP: round(((jank_values[-1] - jank_values[0]) / jank_values[0] * 100), 2) if len(jank_values) > 1 and jank_values[0] != 0 else None
            }
            by_case[case_name][FM.MEM] = {
                FM.FV: round(memory_values[0], 2) if memory_values else None,   # 最早值
                FM.LV: round(memory_values[-1], 2) if memory_values else None,  # 最新值
                FM.CH: round(memory_values[-1] - memory_values[0], 2) if len(memory_values) > 1 else None,  # 最新-最早
                FM.CP: round(((memory_values[-1] - memory_values[0]) / memory_values[0] * 100), 2) if len(memory_values) > 1 and memory_values[0] != 0 else None
            }

            # 按设备分组
            by_case[case_name]["devs"] = self._group_case_by_device(items)

        return by_case

    def _group_items(self, data: List[Dict[str, Any]], key: str) -> Dict[str, List[Dict[str, Any]]]:
        """按指定字段分组"""
        grouped = {}
        for item in data:
            value = item.get(key)
            if value:
                if value not in grouped:
                    grouped[value] = []
                grouped[value].append(item)
        return grouped

    def _group_case_by_device(self, case_items: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        """按设备分组用例数据，并计算趋势"""
        by_device = {}

        # 第一遍：统计设备数据点
        for case_item in case_items:
            device_id = case_item.get(FM.DI)
            device_name = case_item.get(FM.DN)

            if not device_id:
                continue

            key = f"{FM.DI}_{device_id}"
            if key not in by_device:
                by_device[key] = {
                    FM.DI: device_id,
                    FM.DN: device_name,
                    FM.CFG: self._classify_device_config(device_name) if device_name else "未知配置"
                }

        # 第二遍：计算设备趋势
        grouped_by_device = self._group_items(case_items, FM.DI)
        for device_id, items in grouped_by_device.items():
            device_name = items[0].get(FM.DN) if items else ""
            key = f"{FM.DI}_{device_id}"

            # 按时间排序并直接提取值（保持时间顺序）
            items_by_date = self._sort_by_date(items)


            fps_values = [item.get(FM.F90) for item in items_by_date if item.get(FM.F90) is not None]
            jank_values = [item.get(FM.JK) for item in items_by_date if item.get(FM.JK) is not None]
            memory_values = [item.get(FM.MEM) for item in items_by_date if item.get(FM.MEM) is not None]

            # 计算趋势
            by_device[key][FM.F90] = self._calculate_trend(fps_values)
            by_device[key][FM.JK] = self._calculate_trend(jank_values)
            by_device[key][FM.MEM] = self._calculate_trend(memory_values)

            # ========== 使用 PerformanceEvaluator 评估 ==========
            from .performance_evaluator import PerformanceEvaluator, PerformanceMetrics

            evaluator = PerformanceEvaluator()
            if len(fps_values) > 1 or len(jank_values) > 1:
                metrics = PerformanceMetrics(
                    fps_earliest=fps_values[0] if fps_values else None,
                    fps_latest=fps_values[-1] if fps_values else None,
                    jank_earliest=jank_values[0] if jank_values else None,
                    jank_latest=jank_values[-1] if jank_values else None,
                    mem_earliest=memory_values[0] if memory_values else None,
                    mem_latest=memory_values[-1] if memory_values else None
                )
                result = evaluator.evaluate(metrics)
                by_device[key]["eval"] = result.to_dict()
            else:
                by_device[key]["eval"] = None

        return by_device

    def _calculate_trend(self, values: List[float]) -> Dict[str, Any]:
        """
        计算趋势值（fv, lv, ch, cp）

        fv = 第一个值（最早数据，First Value）
        lv = 最后一个值（最新数据，Last Value）
        ch = lv - fv（从最早到最新的变化，正数=上升，负数=下降）
        cp = ch / fv * 100（基于最早值的百分比变化）
        """
        if not values:
            return {FM.FV: None, FM.LV: None, FM.CH: None, FM.CP: None}

        fv = round(float(values[0]), 2)   # 最早值
        lv = round(float(values[-1]), 2)  # 最新值

        if len(values) > 1:
            ch = round(lv - fv, 2)  # 最新值 - 最早值（正数=上升，负数=下降）
            cp = round(ch / fv * 100, 2) if fv != 0 else None
        else:
            ch = None
            cp = None

        return {FM.FV: fv, FM.LV: lv, FM.CH: ch, FM.CP: cp}

    def _build_device_metadata(self, data: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        """
        构建设备元数据索引

        每个设备包含：
        - 基础信息（ID、名称、配置）
        - 涉及的用例列表
        - 汇总性能指标（所有用例的平均表现，含趋势统计）

        Args:
            data: 原始数据列表

        Returns:
            设备元数据索引 {device_key: {...}}
        """
        device_metadata = {}

        # 收集设备基础信息和涉及的用例
        device_cases_map = defaultdict(set)  # device_id -> set of case names
        device_info_map = {}  # device_id -> {name, config}
        device_data_map = defaultdict(list)  # device_id -> list of data items (按时间排序)

        # 先按时间排序所有数据
        sorted_data = self._sort_by_date(data)

        for item in sorted_data:
            device_id = item.get(FM.DI)
            device_name = item.get(FM.DN)
            case_name = item.get(FM.CN)

            if not device_id:
                continue

            key = f"{FM.DI}_{device_id}"

            # 收集设备基础信息
            if key not in device_info_map:
                device_info_map[key] = {
                    FM.DI: device_id,
                    FM.DN: device_name,
                    FM.CFG: self._classify_device_config(device_name) if device_name else "未知配置"
                }

            # 记录设备涉及的用例
            if case_name:
                device_cases_map[device_id].add(case_name)

            # 收集该设备的数据（保持时间顺序）
            if device_id in device_data_map:
                device_data_map[device_id].append(item)

        # 计算每个设备的汇总性能指标
        for device_id, case_names in device_cases_map.items():
            key = f"{FM.DI}_{device_id}"
            device_items = device_data_map.get(device_id, [])

            # 按时间排序并提取值（保持时间顺序）
            device_fps_values = [item.get(FM.F90) for item in device_items if item.get(FM.F90) is not None]
            device_jank_values = [item.get(FM.JK) for item in device_items if item.get(FM.JK) is not None]
            device_memory_values = [item.get(FM.MEM) for item in device_items if item.get(FM.MEM) is not None]

            # 计算设备的汇总指标
            device_metadata[key] = {
                FM.DI: device_id,
                FM.DN: device_info_map[key][FM.DN],
                FM.CFG: device_info_map[key][FM.CFG],
                FM.CN: sorted(case_names),  # 涉及的用例列表
                FM.N: len(case_names),  # 涉及的用例数量
                FM.F90: self._calculate_stats_with_trend(device_fps_values) if device_fps_values else {},
                FM.JK: self._calculate_stats_with_trend(device_jank_values) if device_jank_values else {},
                FM.MEM: self._calculate_stats_with_trend(device_memory_values) if device_memory_values else {}
            }

        return device_metadata

    def _calculate_stats_with_trend(self, values: List[float]) -> Dict[str, Any]:
        """
        计算统计指标（含趋势信息）

        FV = 最后一个值（时间上最新）
        LV = 第一个值（时间上最早）
        MIN = 最小值
        MAX = 最大值
        AVG = 平均值
        """
        if not values:
            return {}

        vals = [float(v) for v in values]
        fv = round(vals[-1], 2)  # 最后一个值（时间上最新）
        lv = round(vals[0], 2)  # 第一个值（时间上最早）
        min_val = round(min(vals), 2)
        max_val = round(max(vals), 2)
        avg_val = round(sum(vals) / len(vals), 2)

        return {
            FM.FV: fv,
            FM.LV: lv,
            FM.MIN: min_val,
            FM.MAX: max_val,
            FM.AVG: avg_val
        }
