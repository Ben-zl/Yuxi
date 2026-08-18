"""Unity Profiler Analyzer - Overview Reporter"""

from .base_reporter import BaseReporter
from .markdown_formatter import MarkdownFormatter


class OverviewReporter(BaseReporter):
    """Reporter for performance overview reports"""

    def generate(self, analysis_result: dict) -> str:
        """Generate Markdown overview report

        Args:
            analysis_result: Analysis result from ProfilerAnalyzer.analyze_overview()

        Returns:
            Markdown formatted overview report
        """
        lines = []

        # Header
        lines.append(self._format_header(analysis_result))

        # Metadata
        lines.append(self._format_metadata(analysis_result))

        # Metrics
        lines.append(self._format_metrics(analysis_result))

        # Detailed metrics table
        lines.append(self._format_detailed_metrics(analysis_result))

        # Footer
        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append("*报告生成时间*: " + self._get_timestamp())

        return "\n".join(lines)

    def _format_detailed_metrics(self, analysis_result: dict) -> str:
        """Format detailed metrics table

        Args:
            analysis_result: Analysis result

        Returns:
            Formatted metrics table section
        """
        lines = []
        lines.append(self.formatter.h3("详细指标"))
        lines.append("")

        metrics = analysis_result.get("metrics", {})

        # Create metrics table
        headers = ["指标", "数值", "评估"]
        rows = []

        # FPS
        fps = metrics.get("fps", 0)
        fps_eval = analysis_result.get("evaluation", {}).get("fps", "N/A")
        rows.append(["平均 FPS", self.formatter.format_number(fps, 0), fps_eval])

        # Frame Time
        frame_time = metrics.get("frame_time", 0)
        rows.append(["平均帧时间", self.formatter.format_time(frame_time), ""])

        # Memory
        mem_total = metrics.get("memory_total", 0)
        mem_status = analysis_result.get("evaluation", {}).get("memory", "N/A")
        rows.append(["总内存", f"{mem_total} MB", mem_status])

        # DrawCalls
        draw_calls = metrics.get("draw_calls", 0)
        dc_status = analysis_result.get("evaluation", {}).get("rendering", "N/A")
        rows.append(["DrawCalls", self.formatter.format_number(draw_calls, 0), dc_status])

        # GC
        gc_count = metrics.get("gc_count", 0)
        gc_time = metrics.get("gc_time", 0)
        rows.append(["GC 次数", str(gc_count), ""])
        rows.append(["GC 平均耗时", self.formatter.format_time(gc_time), ""])

        lines.append(self.formatter.table(headers, rows))
        lines.append("")

        return "\n".join(lines)

    @staticmethod
    def _get_timestamp() -> str:
        """Get current timestamp

        Returns:
            Formatted timestamp string
        """
        from datetime import datetime
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
