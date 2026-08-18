"""Unity Profiler Analyzer - Comparison Reporter

This module takes preprocessed comparison data and formats it for AI consumption.
It provides structured data with clear sections for report generation.

The output is designed to be easily consumed by AI to generate high-quality
Markdown reports with minimal additional processing.

IMPORTANT: This class does NOT generate Markdown reports. It only structures
the data and provides table data for AI agent to use in report generation.
"""

from typing import Dict, Any, List
from datetime import datetime

# Handle both relative and absolute imports
try:
    from .comparison_preprocessor import ComparisonPreprocessor
except (ImportError, ValueError):
    from comparers.comparison_preprocessor import ComparisonPreprocessor


class ComparisonReporter:
    """Generate enhanced comparison analysis report data

    Takes raw comparison data, performs Python-based analysis using
    ComparisonPreprocessor, and outputs structured data optimized for
    AI report generation.

    Following the same pattern as JankReporter:
    - Python preprocessing for statistical analysis
    - AI-ready JSON output with tables and recommendations
    - NO Markdown generation (that's AI's job)
    """

    def __init__(self, raw_comparison_data: dict):
        """Initialize comparison reporter

        Args:
            raw_comparison_data: Raw comparison data from ComparisonCollector
        """
        self.raw_data = raw_comparison_data
        self.preprocessor = ComparisonPreprocessor(raw_comparison_data)

    def generate_report_data(self) -> dict:
        """Generate complete report data structure

        Returns:
            Enhanced report data ready for AI consumption
        """
        # Perform all analysis
        analysis = self.preprocessor.analyze_all()

        # Build report structure
        report = {
            "metadata": self._build_metadata(),
            "report_info": self._build_report_info(),
            "metrics_delta": analysis["metrics_delta"],
            "functions_delta": analysis["functions_delta"],
            "memory_delta": analysis["memory_delta"],
            "rendering_delta": analysis["rendering_delta"],
            "performance_categories": analysis["performance_categories"],
            "recommendations": analysis["recommendations"],
            "tables": self._build_tables(analysis),
        }

        return report

    def _build_metadata(self) -> dict:
        """Build report metadata"""
        return {
            "case_id_a": self.raw_data.get("case_id_a", "Unknown"),
            "case_id_b": self.raw_data.get("case_id_b", "Unknown"),
            "comparison_timestamp": datetime.now().isoformat(),
            "analysis_type": "comparison_analysis",
        }

    def _build_report_info(self) -> dict:
        """Build report info for both cases"""
        report_a = self.raw_data.get("report_a", {})
        report_b = self.raw_data.get("report_b", {})

        report_info_a = report_a.get("report_info", {})
        report_info_b = report_b.get("report_info", {})

        return {
            "report_a": {
                "uuid": report_info_a.get("UUID", "Unknown"),
                "project_id": report_info_a.get("ProjectId", "Unknown"),
                "app_version": report_info_a.get("AppVersion", "Unknown"),
                "device_info": report_info_a.get("DeviceInfo", "Unknown"),
            },
            "report_b": {
                "uuid": report_info_b.get("UUID", "Unknown"),
                "project_id": report_info_b.get("ProjectId", "Unknown"),
                "app_version": report_info_b.get("AppVersion", "Unknown"),
                "device_info": report_info_b.get("DeviceInfo", "Unknown"),
            },
        }

    def _build_tables(self, analysis: dict) -> dict:
        """Build ready-to-use table data for Markdown generation

        Args:
            analysis: Analysis results from preprocessor

        Returns:
            Dictionary with table data (headers and rows)
        """
        tables = {}

        # 1. Key metrics comparison table
        metrics = analysis["metrics_delta"]
        tables["metrics_comparison"] = {
            "headers": ["指标", "报告 A", "报告 B", "变化值", "变化率", "趋势", "评估"],
            "rows": [
                [
                    "平均 FPS",
                    f"{metrics['fps']['value_a']:.2f}",
                    f"{metrics['fps']['value_b']:.2f}",
                    f"{metrics['fps']['delta']:+.2f}",
                    f"{metrics['fps']['delta_pct']:+.1f}%",
                    metrics['fps']['trend'],
                    metrics['fps']['status'],
                ],
                [
                    "平均帧时间 (ms)",
                    f"{metrics['frame_time']['value_a']:.2f}",
                    f"{metrics['frame_time']['value_b']:.2f}",
                    f"{metrics['frame_time']['delta']:+.2f}",
                    f"{metrics['frame_time']['delta_pct']:+.1f}%",
                    metrics['frame_time']['trend'],
                    metrics['frame_time']['status'],
                ],
                [
                    "物理时间 (ms)",
                    f"{metrics['physics_time']['value_a']:.2f}",
                    f"{metrics['physics_time']['value_b']:.2f}",
                    f"{metrics['physics_time']['delta']:+.2f}",
                    f"{metrics['physics_time']['delta_pct']:+.1f}%",
                    metrics['physics_time']['trend'],
                    metrics['physics_time']['status'],
                ],
                [
                    "渲染时间 (ms)",
                    f"{metrics['rendering_time']['value_a']:.2f}",
                    f"{metrics['rendering_time']['value_b']:.2f}",
                    f"{metrics['rendering_time']['delta']:+.2f}",
                    f"{metrics['rendering_time']['delta_pct']:+.1f}%",
                    metrics['rendering_time']['trend'],
                    metrics['rendering_time']['status'],
                ],
                [
                    "脚本时间 (ms)",
                    f"{metrics['script_time']['value_a']:.2f}",
                    f"{metrics['script_time']['value_b']:.2f}",
                    f"{metrics['script_time']['delta']:+.2f}",
                    f"{metrics['script_time']['delta_pct']:+.1f}%",
                    metrics['script_time']['trend'],
                    metrics['script_time']['status'],
                ],
                [
                    "UI 时间 (ms)",
                    f"{metrics['ui_time']['value_a']:.2f}",
                    f"{metrics['ui_time']['value_b']:.2f}",
                    f"{metrics['ui_time']['delta']:+.2f}",
                    f"{metrics['ui_time']['delta_pct']:+.1f}%",
                    metrics['ui_time']['trend'],
                    metrics['ui_time']['status'],
                ],
                [
                    "总内存 (MB)",
                    f"{metrics['memory_total']['value_a']:.2f}",
                    f"{metrics['memory_total']['value_b']:.2f}",
                    f"{metrics['memory_total']['delta']:+.2f}",
                    f"{metrics['memory_total']['delta_pct']:+.1f}%",
                    metrics['memory_total']['trend'],
                    metrics['memory_total']['status'],
                ],
                [
                    "DrawCalls",
                    f"{metrics['draw_calls']['value_a']:.1f}",
                    f"{metrics['draw_calls']['value_b']:.1f}",
                    f"{metrics['draw_calls']['delta']:+.1f}",
                    f"{metrics['draw_calls']['delta_pct']:+.1f}%",
                    metrics['draw_calls']['trend'],
                    metrics['draw_calls']['status'],
                ],
                [
                    "GC 次数",
                    f"{metrics['gc_count']['value_a']:.0f}",
                    f"{metrics['gc_count']['value_b']:.0f}",
                    f"{metrics['gc_count']['delta']:+.0f}",
                    f"{metrics['gc_count']['delta_pct']:+.1f}%",
                    metrics['gc_count']['trend'],
                    metrics['gc_count']['status'],
                ],
            ]
        }

        # 2. Rendering performance table
        rendering = analysis["rendering_delta"]
        tables["rendering_comparison"] = {
            "headers": ["渲染指标", "报告 A", "报告 B", "变化", "变化率", "趋势"],
            "rows": [
                [
                    "DrawCalls (平均)",
                    f"{rendering['draw_calls']['value_a']:.1f}",
                    f"{rendering['draw_calls']['value_b']:.1f}",
                    f"{rendering['draw_calls']['delta']:+.1f}",
                    f"{rendering['draw_calls']['delta_pct']:+.1f}%",
                    rendering['draw_calls']['trend'],
                ],
                [
                    "DrawCalls (最大)",
                    f"{rendering['max_draw_calls']['value_a']:.0f}",
                    f"{rendering['max_draw_calls']['value_b']:.0f}",
                    f"{rendering['max_draw_calls']['delta']:+.0f}",
                    f"{rendering['max_draw_calls']['delta_pct']:+.1f}%",
                    rendering['max_draw_calls']['trend'],
                ],
                [
                    "SetPassCalls (平均)",
                    f"{rendering['setpass_calls']['value_a']:.1f}",
                    f"{rendering['setpass_calls']['value_b']:.1f}",
                    f"{rendering['setpass_calls']['delta']:+.1f}",
                    f"{rendering['setpass_calls']['delta_pct']:+.1f}%",
                    rendering['setpass_calls']['trend'],
                ],
                [
                    "Triangles (平均)",
                    f"{rendering['triangles']['value_a']:.0f}",
                    f"{rendering['triangles']['value_b']:.0f}",
                    f"{rendering['triangles']['delta']:+.0f}",
                    f"{rendering['triangles']['delta_pct']:+.1f}%",
                    rendering['triangles']['trend'],
                ],
                [
                    "Triangles (最大)",
                    f"{rendering['max_triangles']['value_a']:.0f}",
                    f"{rendering['max_triangles']['value_b']:.0f}",
                    f"{rendering['max_triangles']['delta']:+.0f}",
                    f"{rendering['max_triangles']['delta_pct']:+.1f}%",
                    rendering['max_triangles']['trend'],
                ],
                [
                    "Vertices (平均)",
                    f"{rendering['vertices']['value_a']:.0f}",
                    f"{rendering['vertices']['value_b']:.0f}",
                    f"{rendering['vertices']['delta']:+.0f}",
                    f"{rendering['vertices']['delta_pct']:+.1f}%",
                    rendering['vertices']['trend'],
                ],
            ]
        }

        # 3. Memory comparison table
        memory = analysis["memory_delta"]
        tables["memory_comparison"] = {
            "headers": ["内存类型", "报告 A (MB)", "报告 B (MB)", "变化", "变化率", "趋势"],
            "rows": [
                [
                    "总内存",
                    f"{memory['memory_total']['value_a']:.2f}",
                    f"{memory['memory_total']['value_b']:.2f}",
                    f"{memory['memory_total']['delta']:+.2f}",
                    f"{memory['memory_total']['delta_pct']:+.1f}%",
                    memory['memory_total']['trend'],
                ],
                [
                    "Mono 堆",
                    f"{memory['memory_mono']['value_a']:.2f}",
                    f"{memory['memory_mono']['value_b']:.2f}",
                    f"{memory['memory_mono']['delta']:+.2f}",
                    f"{memory['memory_mono']['delta_pct']:+.1f}%",
                    memory['memory_mono']['trend'],
                ],
            ]
        }

        # 4. GC performance table
        tables["gc_comparison"] = {
            "headers": ["GC 指标", "报告 A", "报告 B", "变化", "变化率", "趋势"],
            "rows": [
                [
                    "GC 次数",
                    f"{memory['gc_count']['value_a']:.0f}",
                    f"{memory['gc_count']['value_b']:.0f}",
                    f"{memory['gc_count']['delta']:+.0f}",
                    f"{memory['gc_count']['delta_pct']:+.1f}%",
                    memory['gc_count']['trend'],
                ],
                [
                    "GC 总耗时 (ms)",
                    f"{memory['gc_time_ms']['value_a']:.2f}",
                    f"{memory['gc_time_ms']['value_b']:.2f}",
                    f"{memory['gc_time_ms']['delta']:+.2f}",
                    f"{memory['gc_time_ms']['delta_pct']:+.1f}%",
                    memory['gc_time_ms']['trend'],
                ],
                [
                    "GC 内存 (KB)",
                    f"{memory['gc_memory_kb']['value_a']:.2f}",
                    f"{memory['gc_memory_kb']['value_b']:.2f}",
                    f"{memory['gc_memory_kb']['delta']:+.2f}",
                    f"{memory['gc_memory_kb']['delta_pct']:+.1f}%",
                    memory['gc_memory_kb']['trend'],
                ],
            ]
        }

        # 5. Degraded functions table (Top 20)
        degraded = analysis["functions_delta"]["degraded_top_20"]
        tables["degraded_functions"] = {
            "headers": ["排名", "函数名", "模块", "报告A (ms)", "报告B (ms)", "变化", "变化率", "原因分析"],
            "rows": [
                [
                    str(i + 1),
                    f["name"][:50],
                    f["module"],
                    f"{f['time_a']:.2f}",
                    f"{f['time_b']:.2f}",
                    f"{f['delta']:+.2f}",
                    f"{f['delta_pct']:+.1f}%",
                    self._analyze_degradation_reason(f),
                ]
                for i, f in enumerate(degraded[:20])
            ]
        }

        # 6. Improved functions table (Top 10)
        improved = analysis["functions_delta"]["improved_top_10"]
        tables["improved_functions"] = {
            "headers": ["排名", "函数名", "模块", "报告A (ms)", "报告B (ms)", "变化", "变化率"],
            "rows": [
                [
                    str(i + 1),
                    f["name"][:50],
                    f["module"],
                    f"{f['time_a']:.2f}",
                    f"{f['time_b']:.2f}",
                    f"{f['delta']:+.2f}",
                    f"{f['delta_pct']:+.1f}%",
                ]
                for i, f in enumerate(improved[:10])
            ]
        }

        # 7. New hotspots table (from fun_top API)
        new_hotspots = analysis["functions_delta"]["new_hotspots"]
        tables["new_hotspots"] = {
            "headers": ["函数名", "模块", "报告B 平均耗时 (ms)", "报告B 调用次数", "有效帧数", "可能原因"],
            "rows": [
                [
                    f["name"][:50],
                    f["module"],
                    f"{f['selftime_avg_b']:.4f}",
                    f"{f['calls_b']}",
                    f"{f.get('valid_frame_count_b', 0)}",
                    self._analyze_new_hotspot_reason(f),
                ]
                for f in new_hotspots[:20]
            ]
        }

        # 8. Lost hotspots table (from fun_top API)
        lost_hotspots = analysis["functions_delta"]["lost_hotspots"]
        tables["lost_hotspots"] = {
            "headers": ["函数名", "模块", "报告A 平均耗时 (ms)", "报告A 调用次数", "有效帧数", "优化效果"],
            "rows": [
                [
                    f["name"][:50],
                    f["module"],
                    f"{f['selftime_avg_a']:.4f}",
                    f"{f['calls_a']}",
                    f"{f.get('valid_frame_count_a', 0)}",
                    "已优化/已移除",
                ]
                for f in lost_hotspots[:10]
            ]
        }

        return tables

    def _analyze_degradation_reason(self, func: dict) -> str:
        """Analyze the reason for function degradation

        Args:
            func: Function data with delta information

        Returns:
            Reason description
        """
        calls_delta = func.get("calls_delta", 0)
        delta_pct = func.get("delta_pct", 0)

        if calls_delta > 0:
            return f"调用次数增加 (+{calls_delta})"
        elif calls_delta < 0:
            return f"调用次数减少 ({calls_delta}), 但单次耗时增加"
        else:
            return "单次耗时增加"

    def _analyze_new_hotspot_reason(self, func: dict) -> str:
        """Analyze the reason for new hotspot

        Args:
            func: New hotspot function data from fun_top API

        Returns:
            Possible reason
        """
        module = func.get("module", "")
        avg_time_b = func.get("selftime_avg_b", 0)
        calls_b = func.get("calls_b", 0)
        valid_frames_b = func.get("valid_frame_count_b", 0)

        if calls_b > 1000:
            return "高频调用新增函数"
        elif avg_time_b > 10:
            return "高耗时新增函数"
        elif valid_frames_b > 100:
            return f"频繁出现的新增函数 ({valid_frames_b} 帧)"
        elif module == "Rendering":
            return "新增渲染功能"
        elif module == "Script":
            return "新增脚本逻辑"
        else:
            return "新功能/新代码"

    def format_for_ai(self) -> str:
        """Format analysis for AI prompt

        Returns a structured text representation optimized for AI understanding.
        This can be used as context in AI prompts.

        Note: This is optional. The main output is generate_report_data() which
        returns structured JSON for AI consumption.
        """
        report = self.generate_report_data()

        lines = []
        lines.append("# Comparison Analysis Summary")
        lines.append("")

        # Metadata
        metadata = report["metadata"]
        lines.append("## Comparison Overview")
        lines.append(f"- Case A: {metadata['case_id_a']}")
        lines.append(f"- Case B: {metadata['case_id_b']}")
        lines.append("")

        # Key metrics
        metrics = report["metrics_delta"]
        lines.append("## Key Metrics Changes")
        lines.append(f"- FPS: {metrics['fps']['value_a']:.2f} → {metrics['fps']['value_b']:.2f} ({metrics['fps']['delta_pct']:+.1f}%)")
        lines.append(f"- Memory: {metrics['memory_total']['value_a']:.2f} → {metrics['memory_total']['value_b']:.2f} MB ({metrics['memory_total']['delta_pct']:+.1f}%)")
        lines.append(f"- DrawCalls: {metrics['draw_calls']['value_a']:.1f} → {metrics['draw_calls']['value_b']:.1f} ({metrics['draw_calls']['delta_pct']:+.1f}%)")
        lines.append("")

        # Top degraded functions
        degraded = report["functions_delta"]["degraded_top_20"]
        lines.append("## Top Degraded Functions")
        for i, func in enumerate(degraded[:5], 1):
            lines.append(f"{i}. {func['name'][:40]}: {func['time_a']:.2f} → {func['time_b']:.2f} ms ({func['delta_pct']:+.1f}%)")
        lines.append("")

        # Top recommendations
        lines.append("## Top Recommendations")
        for i, rec in enumerate(report["recommendations"][:5], 1):
            lines.append(f"{i}. [{rec['priority'].upper()}] {rec['category']}")
            lines.append(f"   - {rec['issue']}")
            lines.append(f"   - {rec['suggestion']}")
            lines.append("")

        return "\n".join(lines)
