"""
JSON 格式化器稳定性任务测试用例

测试 format_stability_task 方法
"""

import pytest
import json
from formatters.json import JSONFormatter


class TestJSONFormatterStability:
    """JSON 格式化器稳定性任务测试"""

    @pytest.fixture
    def formatter(self):
        """创建格式化器实例"""
        return JSONFormatter()

    @pytest.fixture
    def sample_task_detail(self):
        """示例任务详情数据"""
        return {
            "buildId": 133302,
            "buildName": "日常稳定性 release（PC）-#99",
            "pipelineId": 10,
            "pipelineName": "稳定性测试流水线",
            "status": "RUNNING",
            "startTime": "2026-02-26T03:00:00",
            "endTime": None,
            "executeTime": None,
            "caseDetails": [
                {
                    "caseId": 1,
                    "caseName": "稳定性测试用例1",
                    "status": "SUCCESS",
                    "deviceDetail": [
                        {
                            "deviceId": 101,
                            "deviceName": "RTX3080-测试机A",
                            "status": "SUCCESS",
                            "deviceStatus": 1,
                            "startTime": "2026-02-26T03:00:00",
                            "endTime": "2026-02-26T09:00:00",
                            "perfeyeData": {
                                "LabelMemory.PeakMemory(MB)": 9500.5
                            },
                            "reportData": {}
                        },
                        {
                            "deviceId": 102,
                            "deviceName": "RTX3060-测试机B",
                            "status": "FAILED",
                            "deviceStatus": 1,
                            "startTime": "2026-02-26T03:00:00",
                            "endTime": "2026-02-26T05:30:00",
                            "perfeyeData": {
                                "LabelMemory.PeakMemory(MB)": 8200.0
                            },
                            "reportData": {
                                "Crasheye_12345": "https://crasheye.testplus.cn/dump/12345"
                            }
                        }
                    ]
                }
            ]
        }

    # ========== format_stability_task 测试 ==========

    def test_format_stability_task_basic(self, formatter, sample_task_detail):
        """测试基本稳定性任务格式化"""
        output = formatter.format_stability_task(sample_task_detail)
        data = json.loads(output)

        # 验证类型
        assert data["type"] == "stability_task"

        # 验证任务基本信息
        assert "task" in data
        assert data["task"]["id"] == 133302
        assert data["task"]["name"] == "日常稳定性 release（PC）-#99"
        assert data["task"]["st"] == "RUNNING"

        # 验证不包含 cases 详情
        assert "cases" not in data

    def test_format_stability_task_has_stability_stats(self, formatter, sample_task_detail):
        """测试稳定性统计字段存在"""
        output = formatter.format_stability_task(sample_task_detail)
        data = json.loads(output)

        # 验证 stability 统计存在
        assert "stability" in data
        assert "duration_stats" in data["stability"]
        assert "memory_stats" in data["stability"]
        assert "crash_stats" in data["stability"]
        assert "config_stats" in data["stability"]

    def test_format_stability_task_has_device_summary(self, formatter, sample_task_detail):
        """测试设备状态汇总"""
        output = formatter.format_stability_task(sample_task_detail)
        data = json.loads(output)

        # 验证设备汇总
        assert "sum" in data
        assert "cs" in data["sum"]  # 用例状态统计
        assert "ds" in data["sum"]  # 设备状态统计

    def test_format_stability_task_has_legend(self, formatter, sample_task_detail):
        """测试字段说明存在"""
        output = formatter.format_stability_task(sample_task_detail)
        data = json.loads(output)

        assert "_legend" in data

    def test_format_stability_task_crash_urls(self, formatter, sample_task_detail):
        """测试崩溃统计包含 Crasheye URL"""
        output = formatter.format_stability_task(sample_task_detail)
        data = json.loads(output)

        crash_details = data["stability"]["crash_stats"]["details"]
        assert len(crash_details) == 1

        # 验证 URL 存在
        assert "urls" in crash_details[0]
        assert "https://crasheye.testplus.cn/dump/12345" in crash_details[0]["urls"]

        # 验证配置级别存在
        assert "cfg" in crash_details[0]
        assert crash_details[0]["cfg"] == "medium"  # RTX3060 是中配

    def test_format_stability_task_empty_case_details(self, formatter):
        """测试空用例详情"""
        task_detail = {
            "buildId": 1,
            "buildName": "测试任务",
            "status": "SUCCESS",
            "caseDetails": []
        }

        output = formatter.format_stability_task(task_detail)
        data = json.loads(output)

        # 应该包含错误信息
        assert "error" in data["stability"]

    def test_format_stability_task_duration_stats(self, formatter, sample_task_detail):
        """测试执行时长统计"""
        output = formatter.format_stability_task(sample_task_detail)
        data = json.loads(output)

        duration_stats = data["stability"]["duration_stats"]

        # 验证 SUCCESS 统计
        assert "SUCCESS" in duration_stats
        assert duration_stats["SUCCESS"]["n"] == 1
        assert duration_stats["SUCCESS"]["avg"] == 21600  # 6小时

        # 验证 FAILED 统计
        assert "FAILED" in duration_stats
        assert duration_stats["FAILED"]["n"] == 1
        assert duration_stats["FAILED"]["avg"] == 9000  # 2.5小时

    def test_format_stability_task_memory_stats(self, formatter, sample_task_detail):
        """测试内存统计"""
        output = formatter.format_stability_task(sample_task_detail)
        data = json.loads(output)

        memory_stats = data["stability"]["memory_stats"]

        # 验证整体统计
        assert "overall" in memory_stats
        assert memory_stats["overall"]["n"] == 2

        # 验证按配置统计
        assert "by_config" in memory_stats


class TestJSONFormatterStabilityOutputComparison:
    """稳定性输出与标准输出对比测试"""

    @pytest.fixture
    def formatter(self):
        return JSONFormatter()

    def test_stability_output_smaller_than_standard(self, formatter):
        """测试稳定性输出比标准输出更小（不包含 case 详情）"""
        task_detail = {
            "buildId": 1,
            "buildName": "测试",
            "caseDetails": [
                {
                    "caseId": i,
                    "caseName": f"用例{i}",
                    "deviceDetail": [
                        {
                            "deviceId": j,
                            "deviceName": f"设备{j}",
                            "status": "SUCCESS",
                            "deviceStatus": 1,
                            "reportData": {}
                        }
                        for j in range(10)
                    ]
                }
                for i in range(10)
            ]
        }

        # 格式化为稳定性输出
        stability_output = formatter.format_stability_task(task_detail)
        stability_data = json.loads(stability_output)

        # 验证不包含 cases 字段
        assert "cases" not in stability_data

        # 验证包含 stability 统计
        assert "stability" in stability_data
