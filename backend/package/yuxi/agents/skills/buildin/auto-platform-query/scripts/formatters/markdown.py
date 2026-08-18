"""
Markdown 格式化器 - 生成 AI 友好的 Markdown 报告
"""

from typing import Any, List, Dict
from collections import Counter
from .base import BaseFormatter


class MarkdownFormatter(BaseFormatter):
    """Markdown 格式化器 - 生成结构化的 Markdown 报告"""

    def format_pipelines(self, data: Any) -> str:
        """格式化流水线数据为 Markdown"""
        lines = ["# 流水线查询报告\n"]
        lines.append("## 概要\n")

        if isinstance(data, dict):
            # 单个流水线详情
            return self._format_single_pipeline(data)
        elif isinstance(data, list):
            if not data:
                lines.append("*暂无流水线数据*\n")
                return "".join(lines)
            return self._format_pipeline_list(data)
        return "*未知数据格式*\n"

    def _format_pipeline_list(self, pipelines: List[Dict]) -> str:
        """格式化流水线列表"""
        lines = ["# 流水线查询报告\n"]
        lines.append("## 概要\n")
        lines.append(f"- **总数量**: {len(pipelines)}\n")

        # 按平台统计
        platforms = Counter(p.get('platform', 'Unknown') for p in pipelines)
        if platforms:
            lines.append("\n### 按平台分布\n")
            for platform, count in platforms.most_common():
                lines.append(f"- **{platform}**: {count}\n")

        # 按状态统计
        statuses = Counter(p.get('status', 'Unknown') for p in pipelines)
        if statuses:
            lines.append("\n### 按状态分布\n")
            for status, count in statuses.most_common():
                lines.append(f"- **{status}**: {count}\n")

        # 详细列表
        lines.append("\n## 流水线列表\n\n")
        lines.append("| ID | 名称 | 平台 | 状态 | 创建者 | 创建时间 |\n")
        lines.append("|----|------|------|------|--------|----------|\n")

        for p in pipelines:
            name = p.get('pipelineName', p.get('name', ''))[:50]
            lines.append(f"| {p.get('id')} | {name} | {p.get('platform')} | {p.get('status', 'N/A')} | {p.get('creator', 'N/A')} | {self._format_date(p.get('createTime'))} |\n")

        return "".join(lines)

    def _format_single_pipeline(self, pipeline: Dict) -> str:
        """格式化单个流水线详情"""
        lines = ["# 流水线详情\n"]
        lines.append("## 基本信息\n")
        lines.append(f"- **ID**: {pipeline.get('id')}\n")
        lines.append(f"- **名称**: {pipeline.get('name')}\n")
        lines.append(f"- **平台**: {pipeline.get('platform')}\n")
        lines.append(f"- **状态**: {pipeline.get('status')}\n")
        lines.append(f"- **创建者**: {pipeline.get('creator')}\n")
        lines.append(f"- **创建时间**: {pipeline.get('createTime')}\n")
        lines.append(f"- **描述**: {pipeline.get('description', 'N/A')}\n")
        return "".join(lines)

    def format_tasks(self, data: Any) -> str:
        """格式化任务数据为 Markdown"""
        lines = ["# 任务查询报告\n"]

        # tasks 返回格式: {'code': 0, 'msg': 'success', 'data': {'list': [...], 'count': 39}}
        if isinstance(data, dict):
            task_list = data.get('data', {}).get('list', [])
            total_count = data.get('data', {}).get('count', 0)
        elif isinstance(data, list):
            task_list = data
            total_count = len(data)
        else:
            task_list = []
            total_count = 0

        if not task_list:
            lines.append(f"*暂无任务数据 (总计: {total_count})*\n")
            return "".join(lines)

        lines.append("## 概要\n")
        lines.append(f"- **显示数量**: {len(task_list)}\n")
        lines.append(f"- **总计**: {total_count}\n")

        # 按状态统计
        statuses = Counter(t.get('status', 'Unknown') for t in task_list)
        if statuses:
            lines.append("\n### 按状态分布\n")
            for status, count in statuses.most_common():
                lines.append(f"- **{status}**: {count}\n")

        # 任务列表
        lines.append("\n## 任务列表\n\n")
        lines.append("| ID | 流水线 | 设备 | 状态 | 队列时间 | 开始时间 |\n")
        lines.append("|----|--------|------|------|----------|----------|\n")

        for t in task_list:
            pipeline_name = t.get('pipelineName', '')[:40]
            device_name = t.get('deviceName', '')[:30]
            build_id = t.get('buildId') or t.get('id')  # buildId 是正确的字段名
            lines.append(f"| {build_id} | {pipeline_name} | {device_name} | {t.get('status')} | {self._format_datetime(t.get('queueTime'))} | {self._format_datetime(t.get('startTime'))} |\n")

        return "".join(lines)

    def format_devices(self, data: Any) -> str:
        """格式化设备数据为 Markdown"""
        lines = ["# 设备查询报告\n"]

        if isinstance(data, list):
            devices = data
        elif isinstance(data, dict) and 'data' in data:
            devices = data['data']
        else:
            return "*未知数据格式*\n"

        if not devices:
            lines.append("*暂无设备数据*\n")
            return "".join(lines)

        lines.append("## 概要\n")
        lines.append(f"- **总数量**: {len(devices)}\n")

        # 按状态统计
        statuses = Counter(d.get('status', 'Unknown') for d in devices)
        if statuses:
            lines.append("\n### 按状态分布\n")
            for status, count in statuses.most_common():
                lines.append(f"- **{status}**: {count}\n")

        # 按平台统计
        platforms = Counter(d.get('platform', 'Unknown') for d in devices)
        if platforms:
            lines.append("\n### 按平台分布\n")
            for platform, count in platforms.most_common():
                lines.append(f"- **{platform}**: {count}\n")

        # 设备列表
        lines.append("\n## 设备列表\n\n")
        lines.append("| ID | 名称 | 平台 | 状态 | 系统版本 | 分辨率 |\n")
        lines.append("|----|------|------|------|----------|--------|\n")

        for d in devices:
            name = d.get('name', '')[:40]
            lines.append(f"| {d.get('id')} | {name} | {d.get('platform')} | {d.get('status')} | {d.get('osVersion', 'N/A')} | {d.get('resolution', 'N/A')} |\n")

        return "".join(lines)

    def format_cases(self, data: Any) -> str:
        """格式化用例数据为 Markdown"""
        lines = ["# 用例查询报告\n"]

        if isinstance(data, list):
            cases = data
        elif isinstance(data, dict) and 'data' in data:
            cases = data['data']
        else:
            return "*未知数据格式*\n"

        if not cases:
            lines.append("*暂无用例数据*\n")
            return "".join(lines)

        lines.append("## 概要\n")
        lines.append(f"- **总数量**: {len(cases)}\n")

        # 按类型统计
        case_types = Counter(c.get('type', 'Unknown') for c in cases)
        if case_types:
            lines.append("\n### 按类型分布\n")
            for case_type, count in case_types.most_common():
                lines.append(f"- **{case_type}**: {count}\n")

        # 按优先级统计
        priorities = Counter(c.get('priority', 'Unknown') for c in cases)
        if priorities:
            lines.append("\n### 按优先级分布\n")
            for priority, count in priorities.most_common():
                lines.append(f"- **{priority}**: {count}\n")

        # 用例列表
        lines.append("\n## 用例列表\n\n")
        lines.append("| ID | 名称 | 类型 | 优先级 | 状态 |\n")
        lines.append("|----|------|------|--------|------|\n")

        for c in cases:
            name = c.get('name', '')[:50]
            lines.append(f"| {c.get('id')} | {name} | {c.get('type', 'N/A')} | {c.get('priority', 'N/A')} | {c.get('status', 'N/A')} |\n")

        return "".join(lines)

    def format_packages(self, data: Any) -> str:
        """格式化包体数据为 Markdown"""
        lines = ["# 包体查询报告\n"]

        if isinstance(data, list):
            packages = data
        elif isinstance(data, dict) and 'data' in data:
            packages = data['data']
        else:
            return "*未知数据格式*\n"

        if not packages:
            lines.append("*暂无包体数据*\n")
            return "".join(lines)

        lines.append("## 概要\n")
        lines.append(f"- **总数量**: {len(packages)}\n")

        # 按平台统计
        platforms = Counter(p.get('platform', 'Unknown') for p in packages)
        if platforms:
            lines.append("\n### 按平台分布\n")
            for platform, count in platforms.most_common():
                lines.append(f"- **{platform}**: {count}\n")

        # 包体列表
        lines.append("\n## 包体列表\n\n")
        lines.append("| ID | 名称 | 平台 | 分支 | 版本 | 构建时间 |\n")
        lines.append("|----|------|------|------|------|----------|\n")

        for p in packages:
            name = p.get('name', '')[:40]
            lines.append(f"| {p.get('id')} | {name} | {p.get('platform')} | {p.get('branch', 'N/A')} | {p.get('versionName', 'N/A')} | {self._format_datetime(p.get('buildTime'))} |\n")

        return "".join(lines)

    def format_builds(self, data: Any) -> str:
        """格式化构建数据为 Markdown"""
        lines = ["# 构建信息报告\n"]

        if isinstance(data, dict):
            lines.append("## 基本信息\n")
            for key, value in data.items():
                value = self._format_value(value)
                lines.append(f"- **{key}**: {value}\n")
        else:
            lines.append("*暂无构建数据*\n")

        return "".join(lines)

    def format_config(self, data: Any) -> str:
        """格式化配置数据为 Markdown"""
        lines = ["# 项目配置报告\n"]

        if isinstance(data, dict):
            lines.append("## 基本信息\n")
            for key, value in data.items():
                value = self._format_value(value)
                lines.append(f"- **{key}**: {value}\n")
        else:
            lines.append("*暂无配置数据*\n")

        return "".join(lines)

    def format_logs(self, data: Any) -> str:
        """格式化日志数据为 Markdown"""
        lines = ["# 日志查询报告\n"]

        if isinstance(data, list):
            if not data:
                lines.append("*暂无日志数据*\n")
                return "".join(lines)

            lines.append("## 概要\n")
            lines.append(f"- **日志数量**: {len(data)}\n")
            lines.append("\n## 日志列表\n\n")
            lines.append("| 时间 | 级别 | 消息 |\n")
            lines.append("|------|------|------|\n")

            for log in data[:50]:  # 限制显示前 50 条
                msg = log.get('message', '')[:100]
                lines.append(f"| {self._format_datetime(log.get('timestamp'))} | {log.get('level', 'INFO')} | {msg} |\n")
        else:
            lines.append("*暂无日志数据*\n")

        return "".join(lines)

    def format_device_executions(self, task_detail: Any, device_id: int = None) -> str:
        """
        格式化用例设备执行情况为 Markdown（AI 分析专用）

        Args:
            task_detail: 任务详情数据（包含 caseDetails）
            device_id: 设备ID（如果指定了特定设备）

        Returns:
            格式化的 Markdown 字符串
        """
        lines = []

        # 解析任务详情
        if not task_detail or 'caseDetails' not in task_detail:
            return "# 任务执行报告\n\n*无法解析任务详情*\n"

        case_details = task_detail.get('caseDetails', [])
        if not case_details:
            return "# 任务执行报告\n\n*暂无用例数据*\n"

        # ========== 任务概要 ==========
        lines.append("# 任务执行报告\n")
        lines.append("\n## 📋 任务概要\n\n")
        lines.append("| 项目 | 信息 |\n")
        lines.append("|------|------|\n")
        lines.append(f"| **Build ID** | {task_detail.get('buildId')} |\n")
        lines.append(f"| **任务名称** | {task_detail.get('buildName')} |\n")
        lines.append(f"| **流水线** | {task_detail.get('pipelineName')} |\n")
        lines.append(f"| **状态** | {task_detail.get('status')} |\n")
        lines.append(f"| **开始时间** | {self._format_datetime(task_detail.get('startTime'))} |\n")
        lines.append(f"| **结束时间** | {self._format_datetime(task_detail.get('endTime'))} |\n")

        # 计算执行时长
        if task_detail.get('executeTime'):
            lines.append(f"| **执行时长** | {task_detail.get('executeTime')} |\n")

        lines.append(f"| **用例总数** | {len(case_details)} |\n")

        # ========== 统计摘要 ==========
        lines.append("\n---\n")
        lines.append("\n## 📊 统计摘要\n\n")

        # 用例状态统计
        case_status_count = {}
        for case in case_details:
            status = case.get('status', 'UNKNOWN')
            case_status_count[status] = case_status_count.get(status, 0) + 1

        lines.append("### 用例状态分布\n\n")
        lines.append("| 状态 | 数量 | 占比 |\n")
        lines.append("|------|------|------|\n")
        total_cases = len(case_details)
        for status, count in sorted(case_status_count.items()):
            percentage = (count / total_cases * 100) if total_cases > 0 else 0
            lines.append(f"| **{status}** | {count} | {percentage:.1f}% |\n")

        # 设备执行统计
        all_devices = set()
        success_devices = set()
        failed_devices = set()

        for case in case_details:
            for device in case.get('deviceDetail', []):
                dev_id = device.get('deviceId')
                dev_status = device.get('status')
                all_devices.add(dev_id)
                if dev_status == 'SUCCESS':
                    success_devices.add(dev_id)
                elif dev_status == 'FAILED':
                    failed_devices.add(dev_id)

        lines.append("\n### 设备执行统计\n\n")
        lines.append(f"- **总设备数**: {len(all_devices)}\n")
        lines.append(f"- **成功设备数**: {len(success_devices)}\n")
        lines.append(f"- **失败设备数**: {len(failed_devices)}\n")

        # ========== 用例详细执行情况 ==========
        lines.append("\n---\n")
        lines.append("\n## 📝 用例详细执行情况\n\n")

        for i, case in enumerate(case_details, 1):
            case_name = case.get('caseName', 'N/A')
            case_id = case.get('caseId', 'N/A')
            case_status = case.get('status', 'N/A')

            lines.append(f"### {i}. {case_name}\n")
            lines.append(f"\n**用例 ID**: {case_id} | **状态**: {case_status}\n\n")

            device_details = case.get('deviceDetail', [])

            # 如果指定了 device_id，过滤设备
            if device_id:
                device_details = [d for d in device_details if d.get('deviceId') == device_id]

            if not device_details:
                lines.append("*暂无设备执行记录*\n\n")
                continue

            # 检查是否有 perfeye 数据
            has_perfeye = any(d.get('perfeyeData') for d in device_details)

            # 设备执行表格
            if has_perfeye:
                lines.append("| 设备 ID | 设备名称 | 状态 | FPS (TP90) | JANK (/10min) | 峰值内存 (MB) | 结束时间 |\n")
                lines.append("|---------|----------|------|------------|---------------|---------------|----------|\n")
            else:
                lines.append("| 设备 ID | 设备名称 | 状态 | 平台 | 系统版本 | 结束时间 |\n")
                lines.append("|---------|----------|------|------|----------|----------|\n")

            for device in device_details:
                dev_name = self._escape_device_name(device.get('deviceName'))

                if has_perfeye:
                    fps = self._get_perfeye_metric(device, 'LabelFPS.TP90')
                    jank = self._get_perfeye_metric(device, 'LabelFPS.Jank(/10min)')
                    memory = self._get_perfeye_metric(device, 'LabelMemory.PeakMemoryDeposit(MB)')

                    lines.append(f"| {device.get('deviceId')} | {dev_name} | {device.get('status')} | {fps} | {jank} | {memory} | {self._format_datetime(device.get('endTime'))} |\n")
                else:
                    lines.append(f"| {device.get('deviceId')} | {dev_name} | {device.get('status')} | {device.get('platform', 'N/A')} | {device.get('systemVersion', 'N/A')} | {self._format_datetime(device.get('endTime'))} |\n")

            lines.append("\n")

            # 性能统计摘要（如果有 Perfeye 数据）
            if has_perfeye:
                perfeye_fps_list = []
                perfeye_jank_list = []
                perfeye_memory_list = []

                for device in device_details:
                    if device.get('status') == 'SUCCESS' and device.get('perfeyeData'):
                        try:
                            import json
                            perfeye_data = json.loads(device.get('perfeyeData'))
                            fps = perfeye_data.get('LabelFPS.TP90')
                            jank = perfeye_data.get('LabelFPS.Jank(/10min)')
                            memory = perfeye_data.get('LabelMemory.PeakMemoryDeposit(MB)')

                            if fps:
                                try:
                                    perfeye_fps_list.append(float(fps))
                                except:
                                    pass
                            if jank:
                                try:
                                    perfeye_jank_list.append(float(jank))
                                except:
                                    pass
                            if memory and memory != 'N/A':
                                try:
                                    perfeye_memory_list.append(float(memory))
                                except:
                                    pass
                        except:
                            pass

                if perfeye_fps_list or perfeye_jank_list or perfeye_memory_list:
                    lines.append("**性能统计** (成功执行的设备):\n\n")

                    if perfeye_fps_list:
                        avg_fps = sum(perfeye_fps_list) / len(perfeye_fps_list)
                        min_fps = min(perfeye_fps_list)
                        max_fps = max(perfeye_fps_list)
                        lines.append(f"- **FPS (TP90)**: 平均={avg_fps:.2f}, 最小={min_fps:.2f}, 最大={max_fps:.2f}\n")

                    if perfeye_jank_list:
                        avg_jank = sum(perfeye_jank_list) / len(perfeye_jank_list)
                        min_jank = min(perfeye_jank_list)
                        max_jank = max(perfeye_jank_list)
                        lines.append(f"- **JANK (/10min)**: 平均={avg_jank:.2f}, 最小={min_jank:.2f}, 最大={max_jank:.2f}\n")

                    if perfeye_memory_list:
                        avg_memory = sum(perfeye_memory_list) / len(perfeye_memory_list)
                        min_memory = min(perfeye_memory_list)
                        max_memory = max(perfeye_memory_list)
                        lines.append(f"- **峰值内存 (MB)**: 平均={avg_memory:.2f}, 最小={min_memory:.2f}, 最大={max_memory:.2f}\n")

                    lines.append("\n")

        lines.append("\n---\n")

        # ========== AI 分析提示 ==========
        lines.append("\n## 🤖 AI 分析要点\n\n")

        # 重要说明 - 强调查阅 references
        lines.append("### ⚠️ 重要说明\n\n")
        lines.append("**在使用本报告进行分析前，请务必查阅 `references/` 目录下的文档！**\n\n")
        lines.append("| 文档 | 用途 | 何时查阅 |\n")
        lines.append("|------|------|----------|\n")
        lines.append("| **[references/TASK_DETAIL.md](references/TASK_DETAIL.md)** | 任务详情完整指南 | 了解数据结构、性能指标标准、最佳实践 |\n")
        lines.append("| **[references/PERFORMANCE_COMPARISON.md](references/PERFORMANCE_COMPARISON.md)** | 性能对比完整指南 | 进行多次Build性能对比时 |\n\n")

        lines.append("### 📖 核心文档要点 (TASK_DETAIL.md)\n\n")
        lines.append("- ✅ 任务详情的完整数据结构说明\n")
        lines.append("- ✅ 性能指标的含义和判断标准\n")
        lines.append("- ✅ 各种输出格式的详细说明\n")
        lines.append("- ✅ 使用场景和最佳实践\n")
        lines.append("- ✅ 常见问题的解决方案\n\n")

        lines.append("### 性能分析\n")
        lines.append("- 检查 FPS 是否达标（一般建议：PC ≥ 60 FPS）\n")
        lines.append("- 分析 JANK 值，判断卡顿是否严重（建议：< 10 次/10min）\n")
        lines.append("- 识别内存峰值异常的设备\n\n")

        lines.append("### 失败分析\n")
        lines.append("- 统计失败的用例和设备\n")
        lines.append("- 识别普遍失败 vs 个别设备失败\n")
        lines.append("- 分析失败是否有规律性（如特定配置、特定用例）\n\n")

        lines.append("### 趋势分析\n")
        lines.append("- 对比不同设备的性能差异\n")
        lines.append("- 分析用例复杂度与性能的关系\n")
        lines.append("- 识别性能瓶颈（CPU、GPU、内存）\n\n")

        lines.append("---\n")
        lines.append("\n📚 **详细文档目录**: `references/`\n")
        lines.append("- [TASK_DETAIL.md](references/TASK_DETAIL.md) - 任务详情查询完整指南\n")
        lines.append("- [PERFORMANCE_COMPARISON.md](references/PERFORMANCE_COMPARISON.md) - 性能对比分析完整指南\n\n")
        lines.append("*报告生成时间: " + self._get_current_time() + "*\n")

        return "".join(lines)

    def _get_perfeye_metric(self, device: dict, metric_key: str) -> str:
        """
        从 perfeye 数据中提取指定指标

        Args:
            device: 设备数据
            metric_key: 指标键名

        Returns:
            格式化的指标值
        """
        perfeye_data = device.get('perfeyeData')
        if not perfeye_data:
            return 'N/A'

        # 如果是字符串，解析为 JSON
        if isinstance(perfeye_data, str):
            try:
                import json
                perfeye_dict = json.loads(perfeye_data)
                value = perfeye_dict.get(metric_key, 'N/A')
                return str(value) if value != 'N/A' else 'N/A'
            except:
                return 'N/A'
        elif isinstance(perfeye_data, dict):
            value = perfeye_data.get(metric_key, 'N/A')
            return str(value) if value != 'N/A' else 'N/A'

        return 'N/A'

    def _format_value(self, value: Any) -> str:
        """格式化值"""
        if value is None:
            return 'N/A'
        elif isinstance(value, bool):
            return 'Yes' if value else 'No'
        elif isinstance(value, (list, dict)):
            return str(value)[:100] + '...' if len(str(value)) > 100 else str(value)

    def _escape_device_name(self, name: str) -> str:
        """转义设备名称中的特殊字符，避免 Markdown 格式错误

        主要处理 `|` 符号（Markdown 表格分隔符）
        """
        if not name:
            return 'N/A'
        # 将 | 替换为 - (区分符，在表格后需替换回)
        return name.replace('|', '-')
        else:
            return str(value)

    def _format_date(self, date_str: str) -> str:
        """格式化日期字符串"""
        if not date_str:
            return 'N/A'
        return date_str[:10] if len(date_str) >= 10 else date_str

    def _format_datetime(self, datetime_str: str) -> str:
        """格式化日期时间字符串"""
        if not datetime_str:
            return 'N/A'
        # 处理 ISO 格式时间
        if 'T' in datetime_str:
            return datetime_str[:19].replace('T', ' ')
        return datetime_str[:19] if len(datetime_str) >= 19 else datetime_str

    def _get_current_time(self) -> str:
        """获取当前时间"""
        from datetime import datetime
        return datetime.now().strftime('%Y-%m-%d %H:%M:%S')
