"""Unity Profiler Analyzer - Base Reporter"""

from abc import ABC, abstractmethod
from .markdown_formatter import MarkdownFormatter


class BaseReporter(ABC):
    """Base reporter for generating analysis reports"""

    def __init__(self, formatter: MarkdownFormatter = None):
        """Initialize reporter

        Args:
            formatter: Markdown formatter instance
        """
        self.formatter = formatter or MarkdownFormatter()

    @abstractmethod
    def generate(self, analysis_result: dict) -> str:
        """Generate Markdown report

        Args:
            analysis_result: Analysis result from analyzer

        Returns:
            Markdown formatted report
        """
        pass

    def _format_header(self, analysis_result: dict) -> str:
        """Format report header

        Args:
            analysis_result: Analysis result

        Returns:
            Formatted header string
        """
        lines = []
        lines.append(self.formatter.h1("Unity Profiler 性能分析报告"))
        lines.append("")
        lines.append(self.formatter.h2("📊 性能概况"))
        lines.append("")

        return "\n".join(lines)

    def _format_metadata(self, analysis_result: dict) -> str:
        """Format report metadata

        Args:
            analysis_result: Analysis result

        Returns:
            Formatted metadata section
        """
        report_info = analysis_result.get("report_info", {})
        lines = []

        lines.append(self.formatter.h3("基础信息"))
        lines.append("")

        metadata = {
            "游戏名称": report_info.get("gameName", "N/A"),
            "案例名称": report_info.get("caseName", "N/A"),
            "游戏版本": report_info.get("GameVersion", "N/A"),
            "测试设备": self._format_device(report_info.get("Device", {})),
            "测试时间": report_info.get("Time", "N/A"),
            "总帧数": report_info.get("FrameCount", "N/A"),
        }

        for key, value in metadata.items():
            lines.append(f"- **{key}**: {value}")

        lines.append("")

        return "\n".join(lines)

    def _format_device(self, device: dict) -> str:
        """Format device information

        Args:
            device: Device data dictionary

        Returns:
            Formatted device string
        """
        if not device:
            return "N/A"

        parts = []
        if device.get("deviceModel"):
            parts.append(device["deviceModel"])
        if device.get("deviceType"):
            parts.append(f"({device['deviceType']})")
        if device.get("operatingSystem"):
            parts.append(f"- {device['operatingSystem']}")
        if device.get("graphicsDeviceName"):
            parts.append(f"GPU: {device['graphicsDeviceName']}")

        return " ".join(parts) if parts else "N/A"

    def _format_metrics(self, analysis_result: dict) -> str:
        """Format metrics section

        Args:
            analysis_result: Analysis result

        Returns:
            Formatted metrics section
        """
        lines = []
        lines.append(self.formatter.h3("性能指标"))
        lines.append("")

        metrics = analysis_result.get("metrics", {})
        evaluation = analysis_result.get("evaluation", {})

        # FPS metrics
        fps = metrics.get("fps", 0)
        fps_status = evaluation.get("fps", "N/A")
        lines.append(f"**平均 FPS**: {self.formatter.format_number(fps)} ({fps_status})")

        # Memory metrics
        mem_total = metrics.get("memory_total", 0)
        mem_status = evaluation.get("memory", "N/A")
        lines.append(f"**总内存**: {self.formatter.format_number(mem_total)} MB ({mem_status})")

        # Rendering metrics
        draw_calls = metrics.get("draw_calls", 0)
        dc_status = evaluation.get("rendering", "N/A")
        lines.append(f"**DrawCalls**: {self.formatter.format_number(draw_calls)} ({dc_status})")

        lines.append("")

        return "\n".join(lines)
