"""
稳定性测试预处理器测试用例

测试 StabilityPreprocessor 的崩溃统计和 Crasheye URL 提取功能
"""

import pytest
from utils.stability_preprocessor import StabilityPreprocessor


class TestStabilityPreprocessor:
    """稳定性预处理器测试"""

    @pytest.fixture
    def preprocessor(self):
        """创建预处理器实例"""
        return StabilityPreprocessor()

    @pytest.fixture
    def sample_task_detail(self):
        """示例任务详情数据"""
        return {
            "buildId": 133302,
            "buildName": "日常稳定性 release（PC）-#99",
            "startTime": "2026-02-26T03:00:00",
            "endTime": "2026-02-26T09:00:00",
            "executeTime": 21600,
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
                                "Crasheye_12345": "https://crasheye.testplus.cn/dump/12345",
                                "Crasheye_12346": "https://crasheye.testplus.cn/dump/12346"
                            }
                        }
                    ]
                }
            ]
        }

    # ========== 崩溃统计测试 ==========

    def test_calculate_crash_stats_basic(self, preprocessor, sample_task_detail):
        """测试基本崩溃统计"""
        case_details = sample_task_detail["caseDetails"]
        crash_stats = preprocessor._calculate_crash_stats(case_details)

        assert crash_stats["total_devices"] == 2
        assert crash_stats["crash_devices"] == 1
        assert crash_stats["crash_rate"] == 50.0
        assert len(crash_stats["details"]) == 1

    def test_calculate_crash_stats_with_crasheye_urls(self, preprocessor, sample_task_detail):
        """测试崩溃统计包含 Crasheye URL"""
        case_details = sample_task_detail["caseDetails"]
        crash_stats = preprocessor._calculate_crash_stats(case_details)

        # 验证崩溃详情包含 URL
        detail = crash_stats["details"][0]
        assert "dn" in detail  # 设备名称
        assert "st" in detail  # 设备状态
        assert "cfg" in detail  # 配置级别
        assert "urls" in detail  # Crasheye URL 列表

        # 验证 URL 列表
        assert len(detail["urls"]) == 2
        assert "https://crasheye.testplus.cn/dump/12345" in detail["urls"]
        assert "https://crasheye.testplus.cn/dump/12346" in detail["urls"]

    def test_calculate_crash_stats_no_crashes(self, preprocessor):
        """测试无崩溃情况"""
        case_details = [
            {
                "caseId": 1,
                "deviceDetail": [
                    {
                        "deviceId": 101,
                        "deviceName": "设备A",
                        "status": "SUCCESS",
                        "deviceStatus": 1,
                        "reportData": {}
                    }
                ]
            }
        ]
        crash_stats = preprocessor._calculate_crash_stats(case_details)

        assert crash_stats["total_devices"] == 1
        assert crash_stats["crash_devices"] == 0
        assert crash_stats["crash_rate"] == 0.0
        assert len(crash_stats["details"]) == 0

    def test_calculate_crash_stats_excludes_offline_devices(self, preprocessor):
        """测试离线设备不参与崩溃统计"""
        case_details = [
            {
                "caseId": 1,
                "deviceDetail": [
                    {
                        "deviceId": 101,
                        "deviceName": "在线设备",
                        "status": "FAILED",
                        "deviceStatus": 1,  # 在线
                        "reportData": {
                            "Crasheye_123": "https://crasheye.testplus.cn/dump/123"
                        }
                    },
                    {
                        "deviceId": 102,
                        "deviceName": "离线设备",
                        "status": "FAILED",
                        "deviceStatus": 0,  # 离线
                        "reportData": {
                            "Crasheye_456": "https://crasheye.testplus.cn/dump/456"
                        }
                    }
                ]
            }
        ]
        crash_stats = preprocessor._calculate_crash_stats(case_details)

        # 只有在线设备参与统计
        assert crash_stats["total_devices"] == 1
        assert crash_stats["crash_devices"] == 1

    def test_calculate_crash_stats_with_config_level(self, preprocessor, sample_task_detail):
        """测试崩溃统计包含设备配置级别"""
        case_details = sample_task_detail["caseDetails"]
        crash_stats = preprocessor._calculate_crash_stats(case_details)

        detail = crash_stats["details"][0]
        # RTX3060 应该被分类为中配
        assert detail["cfg"] == "medium"

    # ========== 完整预处理测试 ==========

    def test_preprocess_full_stability_data(self, preprocessor, sample_task_detail):
        """测试完整稳定性数据预处理"""
        result = preprocessor.preprocess(sample_task_detail)

        # 验证基本字段
        assert "st" in result
        assert "et" in result

        # 验证统计字段存在
        assert "duration_stats" in result
        assert "memory_stats" in result
        assert "crash_stats" in result
        assert "config_stats" in result

        # 验证崩溃统计包含 URL
        assert "urls" in result["crash_stats"]["details"][0]

    # ========== Crasheye URL 提取测试 ==========

    def test_extract_crasheye_urls_basic(self, preprocessor):
        """测试基本 Crasheye URL 提取"""
        report_data = {
            "Crasheye_12345": "https://crasheye.testplus.cn/dump/12345",
            "Crasheye_12346": "https://crasheye.testplus.cn/dump/12346",
            "other_key": "other_value"
        }
        urls = preprocessor._extract_crasheye_urls(report_data)

        assert len(urls) == 2
        assert "https://crasheye.testplus.cn/dump/12345" in urls
        assert "https://crasheye.testplus.cn/dump/12346" in urls

    def test_extract_crasheye_urls_empty(self, preprocessor):
        """测试空 reportData 的 URL 提取"""
        urls = preprocessor._extract_crasheye_urls({})
        assert urls == []

    def test_extract_crasheye_urls_no_crasheye_keys(self, preprocessor):
        """测试无 Crasheye 键的 URL 提取"""
        report_data = {
            "perfeye": "some-uuid",
            "other_key": "other_value"
        }
        urls = preprocessor._extract_crasheye_urls(report_data)
        assert urls == []

    def test_extract_crasheye_urls_with_nested_data(self, preprocessor):
        """测试包含嵌套数据的 URL 提取"""
        report_data = {
            "Crasheye_detail": {
                "url": "https://crasheye.testplus.cn/dump/789",
                "count": 2
            }
        }
        # 应该只提取字符串类型的 URL
        urls = preprocessor._extract_crasheye_urls(report_data)
        # 嵌套结构不是直接的 URL，应该被跳过或提取 URL 字段
        # 这里根据实际实现调整断言


class TestStabilityPreprocessorEdgeCases:
    """边界条件测试"""

    @pytest.fixture
    def preprocessor(self):
        return StabilityPreprocessor()

    def test_empty_case_details(self, preprocessor):
        """测试空用例详情"""
        result = preprocessor.preprocess({
            "buildId": 1,
            "caseDetails": []
        })

        assert "error" in result
        assert result["error"] == "暂无用例数据"

    def test_missing_case_details(self, preprocessor):
        """测试缺失用例详情"""
        result = preprocessor.preprocess({
            "buildId": 1
        })

        assert "error" in result

    def test_device_with_string_perfeye_data(self, preprocessor):
        """测试 perfeyeData 为字符串的情况"""
        case_details = [
            {
                "caseId": 1,
                "deviceDetail": [
                    {
                        "deviceId": 101,
                        "deviceName": "设备A",
                        "status": "SUCCESS",
                        "deviceStatus": 1,
                        "startTime": "2026-02-26T03:00:00",
                        "endTime": "2026-02-26T09:00:00",
                        "perfeyeData": '{"LabelMemory.PeakMemory(MB)": 8000.5}',
                        "reportData": {}
                    }
                ]
            }
        ]

        memory_stats = preprocessor._calculate_memory_stats(case_details)
        assert memory_stats["overall"]["n"] == 1
        assert memory_stats["overall"]["avg"] == 8000.5

    def test_device_with_string_report_data(self, preprocessor):
        """测试 reportData 为字符串的情况"""
        case_details = [
            {
                "caseId": 1,
                "deviceDetail": [
                    {
                        "deviceId": 101,
                        "deviceName": "设备A",
                        "status": "FAILED",
                        "deviceStatus": 1,
                        "reportData": '{"Crasheye_123": "https://crasheye.testplus.cn/dump/123"}'
                    }
                ]
            }
        ]

        crash_stats = preprocessor._calculate_crash_stats(case_details)
        assert crash_stats["crash_devices"] == 1
        assert len(crash_stats["details"][0]["urls"]) == 1
