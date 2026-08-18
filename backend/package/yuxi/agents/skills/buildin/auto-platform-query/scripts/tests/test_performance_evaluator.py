"""
性能评估器测试

测试 PerformanceEvaluator 和 format_trend 功能
"""

import pytest
import sys
from pathlib import Path

# 添加 utils 目录到路径
utils_path = Path(__file__).parent.parent / "utils"
sys.path.insert(0, str(utils_path))

from performance_evaluator import (
    PerformanceEvaluator,
    PerformanceMetrics,
    PerformanceIssue,
    Severity,
    MetricType,
    format_trend,
    get_severity_icon,
    PerformanceThresholds
)


class TestPerformanceThresholds:
    """测试性能阈值配置"""

    def test_thresholds_exist(self):
        """验证阈值常量存在"""
        t = PerformanceThresholds()

        # FPS 阈值
        assert t.FPS_SEVERE_DROP_PCT == 10.0
        assert t.FPS_SEVERE_WITH_LOW == 45.0
        assert t.FPS_SEVERE_ABSOLUTE_LOW == 30.0
        assert t.FPS_SEVERE_CRASH_DROP_PCT == 30.0
        assert t.FPS_WARNING_DROP_PCT == 5.0
        assert t.FPS_TARGET == 60.0

        # JANK 阈值
        assert t.JANK_SEVERE_INCREASE_PCT == 100.0
        assert t.JANK_SEVERE_WITH_ABSOLUTE == 20.0
        assert t.JANK_SEVERE_ABSOLUTE_HIGH == 40.0
        assert t.JANK_TARGET == 10.0

        # 内存阈值
        assert t.MEM_SEVERE_INCREASE_PCT == 50.0
        assert t.MEM_SEVERE_ABSOLUTE_HIGH == 12000.0


class TestPerformanceMetrics:
    """测试性能指标数据类"""

    def test_change_pct_calculation(self):
        """测试变化百分比计算"""
        metrics = PerformanceMetrics(
            fps_earliest=60.0,
            fps_latest=50.0,  # 下降 16.67%
            jank_earliest=10.0,
            jank_latest=25.0,  # 增加 150%
            mem_earliest=8000.0,
            mem_latest=9000.0   # 增加 12.5%
        )

        # FPS: (50 - 60) / 60 * 100 = -16.67%
        assert metrics.fps_change_pct == pytest.approx(-16.67, rel=0.1)
        # JANK: (25 - 10) / 10 * 100 = 150%
        assert metrics.jank_change_pct == pytest.approx(150.0, rel=0.1)
        # MEM: (9000 - 8000) / 8000 * 100 = 12.5%
        assert metrics.mem_change_pct == pytest.approx(12.5, rel=0.1)

    def test_none_values(self):
        """测试空值处理"""
        metrics = PerformanceMetrics()
        assert metrics.fps_change_pct is None
        assert metrics.jank_change_pct is None
        assert metrics.mem_change_pct is None

    def test_zero_earliest(self):
        """测试最早值为0的情况"""
        metrics = PerformanceMetrics(
            fps_earliest=0.0,
            fps_latest=50.0
        )
        assert metrics.fps_change_pct is None


class TestPerformanceEvaluator:
    """测试性能评估器"""

    @pytest.fixture
    def evaluator(self):
        return PerformanceEvaluator()

    def test_normal_performance(self, evaluator):
        """测试正常性能"""
        metrics = PerformanceMetrics(
            fps_earliest=60.0,
            fps_latest=65.0,  # FPS 上升 8.33%
            jank_earliest=10.0,
            jank_latest=8.0,   # JANK 下降 20%
            mem_earliest=8000.0,
            mem_latest=7800.0   # 内存下降 2.5%
        )

        result = evaluator.evaluate(metrics)

        assert result.is_normal
        assert len(result.issues) == 0

    def test_fps_severe_low(self, evaluator):
        """测试 FPS 极低（严重）"""
        metrics = PerformanceMetrics(
            fps_latest=25.0  # 低于 30
        )

        result = evaluator.evaluate(metrics)

        assert result.is_severe
        assert any("FPS 极低" in issue.reason for issue in result.issues)

    def test_fps_severe_crash(self, evaluator):
        """测试 FPS 暴跌（严重）"""
        metrics = PerformanceMetrics(
            fps_earliest=70.0,
            fps_latest=45.0  # 下降 35.71% > 30%
        )

        result = evaluator.evaluate(metrics)

        assert result.is_severe
        assert any("FPS 暴跌" in issue.reason for issue in result.issues)

    def test_fps_severe_drop_with_low(self, evaluator):
        """测试 FPS 下降且偏低（严重）"""
        metrics = PerformanceMetrics(
            fps_earliest=50.0,
            fps_latest=40.0  # 下降 20% > 10%，且最新值 40 < 45
        )

        result = evaluator.evaluate(metrics)

        assert result.is_severe
        assert any("下降且偏低" in issue.reason for issue in result.issues)

    def test_fps_warning_drop(self, evaluator):
        """测试 FPS 下降（需关注）"""
        metrics = PerformanceMetrics(
            fps_earliest=65.0,
            fps_latest=60.0  # 下降 7.69%，在 5%-10% 之间
        )

        result = evaluator.evaluate(metrics)

        assert result.is_warning
        assert any("FPS 下降" in issue.reason for issue in result.issues)

    def test_jank_severe_high(self, evaluator):
        """测试 JANK 极高（严重）"""
        metrics = PerformanceMetrics(
            jank_latest=45.0  # >= 40
        )

        result = evaluator.evaluate(metrics)

        assert result.is_severe
        assert any("JANK 极高" in issue.reason for issue in result.issues)

    def test_jank_severe_increase(self, evaluator):
        """测试 JANK 暴增（严重）"""
        metrics = PerformanceMetrics(
            jank_earliest=10.0,
            jank_latest=25.0  # 增加 150% > 100%，且最新值 25 >= 20
        )

        result = evaluator.evaluate(metrics)

        assert result.is_severe
        assert any("JANK 暴增" in issue.reason for issue in result.issues)

    def test_jank_warning_high(self, evaluator):
        """测试 JANK 偏高（需关注）"""
        metrics = PerformanceMetrics(
            jank_latest=20.0  # 15-30 之间
        )

        result = evaluator.evaluate(metrics)

        assert result.is_warning

    def test_memory_severe_increase(self, evaluator):
        """测试内存暴涨（严重）"""
        metrics = PerformanceMetrics(
            mem_earliest=8000.0,
            mem_latest=13000.0  # 增加 62.5% > 50%
        )

        result = evaluator.evaluate(metrics)

        assert result.is_severe
        assert any("内存暴涨" in issue.reason for issue in result.issues)

    def test_memory_severe_absolute(self, evaluator):
        """测试内存极高（严重）"""
        metrics = PerformanceMetrics(
            mem_latest=13000.0  # > 12000
        )

        result = evaluator.evaluate(metrics)

        assert result.is_severe
        assert any("内存极高" in issue.reason for issue in result.issues)

    def test_combined_triple_worsen(self, evaluator):
        """测试三项同时恶化（严重）"""
        metrics = PerformanceMetrics(
            fps_earliest=70.0,
            fps_latest=60.0,   # 下降 14.3% > 5%
            jank_earliest=10.0,
            jank_latest=20.0,  # 增加 100% > 30%
            mem_earliest=8000.0,
            mem_latest=11000.0  # 增加 37.5% > 20%
        )

        result = evaluator.evaluate(metrics)

        assert result.is_severe
        assert any("综合性能恶化" in issue.reason for issue in result.issues)


class TestFormatTrend:
    """测试趋势格式化"""

    def test_fps_improvement(self):
        """测试 FPS 改善"""
        result = format_trend(MetricType.FPS, 60.0, 70.0)
        assert "✅" in result
        assert "60.00 → 70.00" in result
        assert "+16.7" in result  # 大约 16.67%

    def test_fps_decline(self):
        """测试 FPS 下降"""
        result = format_trend(MetricType.FPS, 70.0, 60.0)
        assert "🔻" in result
        assert "70.00 → 60.00" in result

    def test_fps_stable(self):
        """测试 FPS 稳定"""
        result = format_trend(MetricType.FPS, 65.0, 66.0)
        assert "➡️" in result  # 变化 < 2%

    def test_jank_improvement(self):
        """测试 JANK 改善"""
        result = format_trend(MetricType.JANK, 20.0, 10.0)
        assert "✅" in result  # 下降是好事

    def test_jank_worsen(self):
        """测试 JANK 恶化"""
        result = format_trend(MetricType.JANK, 10.0, 25.0)
        assert "🔺" in result  # 上升是坏事

    def test_memory_improvement(self):
        """测试内存改善"""
        result = format_trend(MetricType.MEMORY, 9000.0, 8000.0)
        assert "✅" in result

    def test_memory_worsen(self):
        """测试内存恶化"""
        result = format_trend(MetricType.MEMORY, 8000.0, 10000.0)
        assert "🔺" in result

    def test_none_values(self):
        """测试空值"""
        result = format_trend(MetricType.FPS, None, 60.0)
        assert "➡️" in result
        assert "60.00" in result

    def test_both_none(self):
        """测试全部为空"""
        result = format_trend(MetricType.FPS, None, None)
        assert "无数据" in result


class TestSeverityIcon:
    """测试严重程度图标"""

    def test_icons(self):
        assert get_severity_icon(Severity.NORMAL) == "🟢"
        assert get_severity_icon(Severity.WARNING) == "🟡"
        assert get_severity_icon(Severity.SEVERE) == "🔴"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
