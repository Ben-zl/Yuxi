"""
智能ID解析器 - 根据任务名+时间范围查找流水线ID或任务ID

核心功能:
1. find_pipeline_id_or_task_id() - 根据任务名+时间范围查找流水线ID或任务ID
2. get_analysis_recommendation() - 根据任务数量和流水线数量推荐分析类型
"""

import datetime
from typing import Dict, List, Any, Optional
from collections import Counter
import automation_api


class IDResolver:
    """智能ID解析器"""

    def find_pipeline_id_or_task_id(
        self, task_name: str, start_time: str, end_time: str
    ) -> Dict[str, Any]:
        """
        根据任务名和时间范围查找流水线ID或任务ID

        Args:
            task_name: 任务名称（支持模糊匹配）
            start_time: 开始时间 (YYYY-MM-DD)
            end_time: 结束时间 (YYYY-MM-DD)

        Returns:
            {
                'id_type': 'pipeline' | 'multiple_tasks' | 'single_task' | 'none',
                'pipeline_id': int,
                'pipeline_name': str,
                'task_id': int,
                'task_name': str,
                'tasks': List[Dict],
                'recommendation': 'trend' | 'detail',
                'error': str  # 如果出错
            }
        """
        try:
            # 构造查询参数（与 task.py 保持一致）
            filters = {}
            filters["buildName"] = task_name
            filters["startTime"] = f"{start_time} 00:00:00"
            filters["endTime"] = f"{end_time} 23:59:59"

            # 查询任务列表（使用 filters 参数）
            tasks_data = automation_api.get_tasks(filters=filters)

            if not tasks_data or "data" not in tasks_data:
                return {
                    "id_type": "none",
                    "error": "未找到匹配的任务",
                    "query": query_params
                }

            tasks = tasks_data.get("data", {}).get("list", [])

            if not tasks:
                return {
                    "id_type": "none",
                    "error": "未找到匹配的任务",
                    "query": query_params
                }

            # 统计 pipelineId 分布
            pipeline_ids = [t.get("pipelineId") for t in tasks if t.get("pipelineId")]
            pipeline_counter = Counter(pipeline_ids)

            # 决策逻辑
            if len(pipeline_counter) == 1:
                # 只有一个流水线
                pipeline_id = list(pipeline_counter.keys())[0]
                pipeline_name = tasks[0].get("pipelineName", "")

                # 检查是否是单个任务
                if len(tasks) == 1:
                    return {
                        "id_type": "single_task",
                        "task_id": tasks[0].get("buildId"),
                        "task_name": tasks[0].get("buildName"),
                        "pipeline_id": pipeline_id,
                        "pipeline_name": pipeline_name,
                        "tasks": tasks,
                        "recommendation": "detail"
                    }
                else:
                    # 多个任务属于同一流水线
                    return {
                        "id_type": "pipeline",
                        "pipeline_id": pipeline_id,
                        "pipeline_name": pipeline_name,
                        "tasks": tasks,
                        "task_count": len(tasks),
                        "recommendation": "trend"
                    }
            else:
                # 多个流水线
                return {
                    "id_type": "multiple_pipelines",
                    "pipelines": {
                        pid: {
                            "pipeline_id": pid,
                            "pipeline_name": next((t.get("pipelineName") for t in tasks if t.get("pipelineId") == pid), ""),
                            "task_count": count,
                            "tasks": [t for t in tasks if t.get("pipelineId") == pid]
                        }
                        for pid, count in pipeline_counter.items()
                    },
                    "tasks": tasks,
                    "task_count": len(tasks),
                    "pipeline_count": len(pipeline_counter),
                    "recommendation": "manual_select"
                }

        except Exception as e:
            return {
                "id_type": "none",
                "error": f"查询失败: {str(e)}",
                "query": {"task_name": task_name, "start_time": start_time, "end_time": end_time}
            }

    def get_analysis_recommendation(
        self, task_count: int, pipeline_count: int, days_range: int
    ) -> str:
        """
        智能推荐分析类型

        Args:
            task_count: 任务数量
            pipeline_count: 流水线数量
            days_range: 时间范围天数

        Returns:
            'trend' | 'detail' | 'comparison' | 'manual_select'
        """
        # 多个流水线，需要手动选择
        if pipeline_count > 1:
            return "manual_select"

        # 单个任务，推荐详情分析
        if task_count == 1:
            return "detail"

        # 时间范围 >= 3 天且是单个流水线，推荐趋势分析
        if days_range >= 3 and pipeline_count == 1:
            return "trend"

        # 时间范围 < 3 天，推荐详情分析
        if days_range < 3:
            return "detail"

        # 默认推荐详情分析
        return "detail"

    def calculate_date_range_days(self, start_time: str, end_time: str) -> int:
        """
        计算时间范围天数

        Args:
            start_time: 开始时间 (YYYY-MM-DD)
            end_time: 结束时间 (YYYY-MM-DD)

        Returns:
            天数
        """
        try:
            start_date = datetime.datetime.strptime(start_time, "%Y-%m-%d").date()
            end_date = datetime.datetime.strptime(end_time, "%Y-%m-%d").date()
            delta = end_date - start_date
            return delta.days + 1  # 包含当天
        except (ValueError, TypeError):
            return 0
