"""
性能评估器

实现多维度性能筛查标准，包括 FPS/JANK/内存 的绝对值检测、综合评估算法。
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List, Tuple, Dict, Any


class Severity(Enum):
    """严重程度枚举"""
    NORMAL = "正常"
    WARNING = "需关注"
    SEVERE = "严重"


class MetricType(Enum):
    """指标类型枚举"""
    FPS = "fps"
    JANK = "jank"
    MEMORY = "memory"


@dataclass
class PerformanceIssue:
    """性能问题"""
    metric_type: MetricType
    severity: Severity
    reason: str
    earliest_value: Optional[float] = None
    latest_value: Optional[float] = None
    change_pct: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "metric": self.metric_type.value,
            "severity": self.severity.value,
            "reason": self.reason,
            "earliest": self.earliest_value,
            "latest": self.latest_value,
            "change_pct": self.change_pct
        }


@dataclass
class PerformanceMetrics:
    """性能指标数据"""
    fps_earliest: Optional[float] = None
    fps_latest: Optional[float] = None
    jank_earliest: Optional[float] = None
    jank_latest: Optional[float] = None
    mem_earliest: Optional[float] = None
    mem_latest: Optional[float] = None

    @property
    def fps_change_pct(self) -> Optional[float]:
        return self._calc_change_pct(self.fps_earliest, self.fps_latest)

    @property
    def jank_change_pct(self) -> Optional[float]:
        return self._calc_change_pct(self.jank_earliest, self.jank_latest)

    @property
    def mem_change_pct(self) -> Optional[float]:
        return self._calc_change_pct(self.mem_earliest, self.mem_latest)

    @staticmethod
    def _calc_change_pct(earliest: Optional[float], latest: Optional[float]) -> Optional[float]:
        if earliest is None or latest is None or earliest == 0:
            return None
        return round(((latest - earliest) / earliest) * 100, 2)


@dataclass
class EvaluationResult:
    """评估结果"""
    severity: Severity
    issues: List[PerformanceIssue] = field(default_factory=list)
    fps_change_pct: float = 0.0
    jank_change_pct: float = 0.0
    mem_change_pct: float = 0.0

    @property
    def is_severe(self) -> bool:
        return self.severity == Severity.SEVERE

    @property
    def is_warning(self) -> bool:
        return self.severity == Severity.WARNING

    @property
    def is_normal(self) -> bool:
        return self.severity == Severity.NORMAL

    def to_dict(self) -> Dict[str, Any]:
        return {
            "severity": self.severity.value,
            "issues": [issue.to_dict() for issue in self.issues],
            "metrics": {
                "fps_change_pct": self.fps_change_pct,
                "jank_change_pct": self.jank_change_pct,
                "mem_change_pct": self.mem_change_pct
            }
        }


class PerformanceThresholds:
    """性能阈值配置"""

    # FPS 阈值
    FPS_SEVERE_DROP_PCT = 10.0          # FPS 下降百分比阈值（严重）
    FPS_SEVERE_WITH_LOW = 45.0          # FPS 下降时 + 低于此值为严重
    FPS_SEVERE_ABSOLUTE_LOW = 30.0      # FPS 绝对值低于此为严重
    FPS_SEVERE_CRASH_DROP_PCT = 30.0    # FPS 暴跌阈值
    FPS_WARNING_DROP_PCT = 5.0          # FPS 下降百分比阈值（需关注）
    FPS_WARNING_ABSOLUTE_RANGE = (30.0, 45.0)  # FPS 绝对值范围（需关注）
    FPS_TARGET = 60.0                   # 目标 FPS

    # JANK 阈值
    JANK_SEVERE_INCREASE_PCT = 100.0    # JANK 增加百分比阈值（严重）
    JANK_SEVERE_WITH_ABSOLUTE = 20.0    # JANK 增加时 + 高于此值为严重
    JANK_SEVERE_ABSOLUTE_HIGH = 40.0    # JANK 绝对值高于此为严重
    JANK_SEVERE_SUSTAINED_HIGH = 30.0   # JANK 持续高位阈值
    JANK_SEVERE_SUSTAINED_INCREASE = 50.0  # JANK 持续高位时的增加阈值
    JANK_WARNING_INCREASE_PCT = 50.0    # JANK 增加百分比阈值（需关注）
    JANK_WARNING_WITH_ABSOLUTE = 15.0   # JANK 增加时 + 高于此值需关注
    JANK_WARNING_ABSOLUTE_RANGE = (15.0, 30.0)  # JANK 绝对值范围（需关注）
    JANK_TARGET = 10.0                  # 目标 JANK

    # 内存阈值
    MEM_SEVERE_INCREASE_PCT = 50.0      # 内存增长百分比阈值（严重）
    MEM_SEVERE_ABSOLUTE_HIGH = 12000.0  # 内存绝对值高于此为严重 (MB)
    MEM_WARNING_INCREASE_PCT = 20.0     # 内存增长百分比阈值（需关注）
    MEM_WARNING_ABSOLUTE_RANGE = (10000.0, 12000.0)  # 内存绝对值范围（需关注）

    # 综合评估阈值
    COMBINED_TRIPLE_WORSEN_FPS = 5.0    # 三项同时恶化 - FPS 下降
    COMBINED_TRIPLE_WORSEN_JANK = 30.0  # 三项同时恶化 - JANK 增加
    COMBINED_TRIPLE_WORSEN_MEM = 20.0   # 三项同时恶化 - 内存增加
    COMBINED_DOUBLE_SEVERE_FPS = 15.0   # 两项严重 - FPS 下降
    COMBINED_DOUBLE_SEVERE_JANK = 50.0  # 两项严重 - JANK 增加


class PerformanceEvaluator:
    """性能评估器"""

    def __init__(self, thresholds: PerformanceThresholds = None):
        self.thresholds = thresholds or PerformanceThresholds()

    def evaluate(self, metrics: PerformanceMetrics) -> EvaluationResult:
        """
        综合评估性能变化

        Args:
            metrics: 性能指标数据

        Returns:
            EvaluationResult: 包含严重程度和问题列表的评估结果
        """
        issues: List[PerformanceIssue] = []
        severity = Severity.NORMAL

        # 获取变化百分比
        fps_change = metrics.fps_change_pct or 0.0
        jank_change = metrics.jank_change_pct or 0.0
        mem_change = metrics.mem_change_pct or 0.0

        # ========== FPS 评估 ==========
        fps_issues, fps_severity = self._evaluate_fps(metrics)
        issues.extend(fps_issues)
        severity = self._max_severity(severity, fps_severity)

        # ========== JANK 评估 ==========
        jank_issues, jank_severity = self._evaluate_jank(metrics)
        issues.extend(jank_issues)
        severity = self._max_severity(severity, jank_severity)

        # ========== 内存评估 ==========
        mem_issues, mem_severity = self._evaluate_memory(metrics)
        issues.extend(mem_issues)
        severity = self._max_severity(severity, mem_severity)

        # ========== 综合评估 ==========
        combined_issues, combined_severity = self._evaluate_combined(metrics)
        issues.extend(combined_issues)
        severity = self._max_severity(severity, combined_severity)

        return EvaluationResult(
            severity=severity,
            issues=issues,
            fps_change_pct=fps_change,
            jank_change_pct=jank_change,
            mem_change_pct=mem_change
        )

    def _evaluate_fps(self, metrics: PerformanceMetrics) -> Tuple[List[PerformanceIssue], Severity]:
        """评估 FPS 指标"""
        issues = []
        severity = Severity.NORMAL
        t = self.thresholds

        if metrics.fps_latest is None:
            return issues, severity

        fps_change = metrics.fps_change_pct or 0.0

        # 严重：FPS 极低
        if metrics.fps_latest < t.FPS_SEVERE_ABSOLUTE_LOW:
            severity = Severity.SEVERE
            issues.append(PerformanceIssue(
                metric_type=MetricType.FPS,
                severity=Severity.SEVERE,
                reason=f"FPS 极低 ({metrics.fps_latest:.1f} < {t.FPS_SEVERE_ABSOLUTE_LOW})",
                earliest_value=metrics.fps_earliest,
                latest_value=metrics.fps_latest,
                change_pct=fps_change
            ))

        # 严重：FPS 暴跌
        elif fps_change < -t.FPS_SEVERE_CRASH_DROP_PCT:
            severity = Severity.SEVERE
            issues.append(PerformanceIssue(
                metric_type=MetricType.FPS,
                severity=Severity.SEVERE,
                reason=f"FPS 暴跌 ({fps_change:.1f}%)",
                earliest_value=metrics.fps_earliest,
                latest_value=metrics.fps_latest,
                change_pct=fps_change
            ))

        # 严重：FPS 下降 + 偏低
        elif fps_change < -t.FPS_SEVERE_DROP_PCT and metrics.fps_latest < t.FPS_SEVERE_WITH_LOW:
            severity = Severity.SEVERE
            issues.append(PerformanceIssue(
                metric_type=MetricType.FPS,
                severity=Severity.SEVERE,
                reason=f"FPS 下降且偏低 ({fps_change:.1f}%, {metrics.fps_latest:.1f})",
                earliest_value=metrics.fps_earliest,
                latest_value=metrics.fps_latest,
                change_pct=fps_change
            ))

        # 需关注：FPS 下降
        elif fps_change < -t.FPS_WARNING_DROP_PCT:
            if severity == Severity.NORMAL:
                severity = Severity.WARNING
            issues.append(PerformanceIssue(
                metric_type=MetricType.FPS,
                severity=Severity.WARNING,
                reason=f"FPS 下降 ({fps_change:.1f}%)",
                earliest_value=metrics.fps_earliest,
                latest_value=metrics.fps_latest,
                change_pct=fps_change
            ))

        # 需关注：FPS 偏低
        elif t.FPS_WARNING_ABSOLUTE_RANGE[0] <= metrics.fps_latest < t.FPS_WARNING_ABSOLUTE_RANGE[1]:
            if severity == Severity.NORMAL:
                severity = Severity.WARNING
            issues.append(PerformanceIssue(
                metric_type=MetricType.FPS,
                severity=Severity.WARNING,
                reason=f"FPS 偏低 ({metrics.fps_latest:.1f})",
                earliest_value=metrics.fps_earliest,
                latest_value=metrics.fps_latest,
                change_pct=fps_change
            ))

        return issues, severity

    def _evaluate_jank(self, metrics: PerformanceMetrics) -> Tuple[List[PerformanceIssue], Severity]:
        """评估 JANK 指标"""
        issues = []
        severity = Severity.NORMAL
        t = self.thresholds

        if metrics.jank_latest is None:
            return issues, severity

        jank_change = metrics.jank_change_pct or 0.0

        # 严重：JANK 极高
        if metrics.jank_latest >= t.JANK_SEVERE_ABSOLUTE_HIGH:
            severity = Severity.SEVERE
            issues.append(PerformanceIssue(
                metric_type=MetricType.JANK,
                severity=Severity.SEVERE,
                reason=f"JANK 极高 ({metrics.jank_latest:.1f} >= {t.JANK_SEVERE_ABSOLUTE_HIGH})",
                earliest_value=metrics.jank_earliest,
                latest_value=metrics.jank_latest,
                change_pct=jank_change
            ))

        # 严重：JANK 暴增
        elif jank_change > t.JANK_SEVERE_INCREASE_PCT and metrics.jank_latest >= t.JANK_SEVERE_WITH_ABSOLUTE:
            severity = Severity.SEVERE
            issues.append(PerformanceIssue(
                metric_type=MetricType.JANK,
                severity=Severity.SEVERE,
                reason=f"JANK 暴增 ({jank_change:.1f}%, {metrics.jank_latest:.1f})",
                earliest_value=metrics.jank_earliest,
                latest_value=metrics.jank_latest,
                change_pct=jank_change
            ))

        # 严重：JANK 持续高位
        elif metrics.jank_latest >= t.JANK_SEVERE_SUSTAINED_HIGH and jank_change > t.JANK_SEVERE_SUSTAINED_INCREASE:
            severity = Severity.SEVERE
            issues.append(PerformanceIssue(
                metric_type=MetricType.JANK,
                severity=Severity.SEVERE,
                reason=f"JANK 持续高位 ({jank_change:.1f}%, {metrics.jank_latest:.1f})",
                earliest_value=metrics.jank_earliest,
                latest_value=metrics.jank_latest,
                change_pct=jank_change
            ))

        # 需关注：JANK 增加
        elif t.JANK_WARNING_INCREASE_PCT < jank_change <= t.JANK_SEVERE_INCREASE_PCT and metrics.jank_latest >= t.JANK_WARNING_WITH_ABSOLUTE:
            if severity in [Severity.NORMAL]:
                severity = Severity.WARNING
            issues.append(PerformanceIssue(
                metric_type=MetricType.JANK,
                severity=Severity.WARNING,
                reason=f"JANK 增加 ({jank_change:.1f}%, {metrics.jank_latest:.1f})",
                earliest_value=metrics.jank_earliest,
                latest_value=metrics.jank_latest,
                change_pct=jank_change
            ))

        # 需关注：JANK 偏高
        elif t.JANK_WARNING_ABSOLUTE_RANGE[0] <= metrics.jank_latest < t.JANK_WARNING_ABSOLUTE_RANGE[1]:
            if severity in [Severity.NORMAL]:
                severity = Severity.WARNING
            issues.append(PerformanceIssue(
                metric_type=MetricType.JANK,
                severity=Severity.WARNING,
                reason=f"JANK 偏高 ({metrics.jank_latest:.1f})",
                earliest_value=metrics.jank_earliest,
                latest_value=metrics.jank_latest,
                change_pct=jank_change
            ))

        return issues, severity

    def _evaluate_memory(self, metrics: PerformanceMetrics) -> Tuple[List[PerformanceIssue], Severity]:
        """评估内存指标"""
        issues = []
        severity = Severity.NORMAL
        t = self.thresholds

        if metrics.mem_latest is None:
            return issues, severity

        mem_change = metrics.mem_change_pct or 0.0

        # 严重：内存暴涨
        if mem_change > t.MEM_SEVERE_INCREASE_PCT:
            severity = Severity.SEVERE
            issues.append(PerformanceIssue(
                metric_type=MetricType.MEMORY,
                severity=Severity.SEVERE,
                reason=f"内存暴涨 ({mem_change:.1f}%)",
                earliest_value=metrics.mem_earliest,
                latest_value=metrics.mem_latest,
                change_pct=mem_change
            ))

        # 严重：内存极高
        elif metrics.mem_latest > t.MEM_SEVERE_ABSOLUTE_HIGH:
            severity = Severity.SEVERE
            issues.append(PerformanceIssue(
                metric_type=MetricType.MEMORY,
                severity=Severity.SEVERE,
                reason=f"内存极高 ({metrics.mem_latest:.0f} MB > {t.MEM_SEVERE_ABSOLUTE_HIGH})",
                earliest_value=metrics.mem_earliest,
                latest_value=metrics.mem_latest,
                change_pct=mem_change
            ))

        # 需关注：内存增长
        elif t.MEM_WARNING_INCREASE_PCT < mem_change <= t.MEM_SEVERE_INCREASE_PCT:
            if severity in [Severity.NORMAL]:
                severity = Severity.WARNING
            issues.append(PerformanceIssue(
                metric_type=MetricType.MEMORY,
                severity=Severity.WARNING,
                reason=f"内存增长 ({mem_change:.1f}%)",
                earliest_value=metrics.mem_earliest,
                latest_value=metrics.mem_latest,
                change_pct=mem_change
            ))

        # 需关注：内存偏高
        elif t.MEM_WARNING_ABSOLUTE_RANGE[0] <= metrics.mem_latest < t.MEM_WARNING_ABSOLUTE_RANGE[1]:
            if severity in [Severity.NORMAL]:
                severity = Severity.WARNING
            issues.append(PerformanceIssue(
                metric_type=MetricType.MEMORY,
                severity=Severity.WARNING,
                reason=f"内存偏高 ({metrics.mem_latest:.0f} MB)",
                earliest_value=metrics.mem_earliest,
                latest_value=metrics.mem_latest,
                change_pct=mem_change
            ))

        return issues, severity

    def _evaluate_combined(self, metrics: PerformanceMetrics) -> Tuple[List[PerformanceIssue], Severity]:
        """综合评估多指标"""
        issues = []
        severity = Severity.NORMAL
        t = self.thresholds

        fps_change = metrics.fps_change_pct or 0.0
        jank_change = metrics.jank_change_pct or 0.0
        mem_change = metrics.mem_change_pct or 0.0

        # 三项同时恶化
        if (fps_change < -t.COMBINED_TRIPLE_WORSEN_FPS and
            jank_change > t.COMBINED_TRIPLE_WORSEN_JANK and
            mem_change > t.COMBINED_TRIPLE_WORSEN_MEM):
            severity = Severity.SEVERE
            issues.append(PerformanceIssue(
                metric_type=MetricType.FPS,  # 用 FPS 代表综合
                severity=Severity.SEVERE,
                reason=f"综合性能恶化（FPS↓{fps_change:.1f}%+JANK↑{jank_change:.1f}%+内存↑{mem_change:.1f}%）",
                earliest_value=None,
                latest_value=None,
                change_pct=None
            ))

        # 两项严重
        elif fps_change < -t.COMBINED_DOUBLE_SEVERE_FPS and jank_change > t.COMBINED_DOUBLE_SEVERE_JANK:
            severity = Severity.SEVERE
            issues.append(PerformanceIssue(
                metric_type=MetricType.FPS,
                severity=Severity.SEVERE,
                reason=f"FPS和JANK同时恶化（FPS↓{fps_change:.1f}%+JANK↑{jank_change:.1f}%）",
                earliest_value=None,
                latest_value=None,
                change_pct=None
            ))

        return issues, severity

    @staticmethod
    def _max_severity(s1: Severity, s2: Severity) -> Severity:
        """返回更严重的级别"""
        severity_order = {Severity.NORMAL: 0, Severity.WARNING: 1, Severity.SEVERE: 2}
        return s1 if severity_order[s1] >= severity_order[s2] else s2


def format_trend(metric_type: MetricType, earliest: Optional[float], latest: Optional[float]) -> str:
    """
    格式化趋势变化显示

    Args:
        metric_type: 指标类型
        earliest: 最早值
        latest: 最新值

    Returns:
        格式化后的趋势字符串，格式为 "最早值 → 最新值 (图标变化%)"
    """
    if earliest is None or latest is None:
        if latest is not None:
            return f"➡️ — → {latest:.2f}"
        elif earliest is not None:
            return f"➡️ {earliest:.2f} → ❌"
        return "➡️ 无数据"

    if earliest == 0:
        change_pct = 0.0
    else:
        change_pct = ((latest - earliest) / earliest) * 100

    # 根据指标类型和变化方向确定图标
    if metric_type == MetricType.FPS:
        # FPS 上升是好事，下降是坏事
        if change_pct > 2:
            icon = "✅"  # 好事
        elif change_pct < -2:
            icon = "🔻"  # 坏事
        else:
            icon = "➡️"  # 稳定
    elif metric_type == MetricType.JANK:
        # JANK 下降是好事，上升是坏事
        if change_pct < -5:
            icon = "✅"  # 好事
        elif change_pct > 5:
            icon = "🔺"  # 坏事
        else:
            icon = "➡️"  # 稳定
    elif metric_type == MetricType.MEMORY:
        # 内存下降是好事，上升是坏事
        if change_pct < -5:
            icon = "✅"  # 好事
        elif change_pct > 5:
            icon = "🔺"  # 坏事
        else:
            icon = "➡️"  # 稳定
    else:
        icon = "➡️"

    # 格式化百分比显示
    sign = "+" if change_pct > 0 else ""

    return f"{earliest:.2f} → {latest:.2f} ({icon}{sign}{change_pct:.1f}%)"


def get_severity_icon(severity: Severity) -> str:
    """获取严重程度图标"""
    icons = {
        Severity.NORMAL: "🟢",
        Severity.WARNING: "🟡",
        Severity.SEVERE: "🔴"
    }
    return icons.get(severity, "➡️")
