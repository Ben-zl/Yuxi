"""Unity Profiler Analyzer - Jank Report Generator

This module takes pre-analyzed jank data and formats it for AI consumption.
It provides structured data with clear sections for report generation.

The output is designed to be easily consumed by AI to generate high-quality
Markdown reports with minimal additional processing.
"""

from typing import Dict, Any, List

# Handle both relative and absolute imports
try:
    from ..analyzers.jank_analyzer import JankAnalyzer
except (ImportError, ValueError):
    from analyzers.jank_analyzer import JankAnalyzer


class JankReporter:
    """Generate enhanced jank analysis report data

    Takes raw jank collector data, performs Python-based analysis,
    and outputs structured data optimized for AI report generation.
    """

    def __init__(self, raw_jank_data: dict):
        """Initialize jank reporter

        Args:
            raw_jank_data: Raw jank data from JankCollector
        """
        self.raw_data = raw_jank_data
        self.analyzer = JankAnalyzer(raw_jank_data)

    def generate_report_data(self) -> dict:
        """Generate complete report data structure

        Returns:
            Enhanced report data ready for AI consumption
        """
        # Perform all analysis
        analysis = self.analyzer.analyze_all()

        # Build report structure
        report = {
            "metadata": self._build_metadata(),
            "summary": self._build_summary(analysis),
            "top_jank_frames": self._build_top_frames_section(),
            "patterns": self._build_patterns_section(analysis["patterns"]),
            "function_analysis": self._build_function_analysis(analysis["function_clusters"]),
            "severity_analysis": self._build_severity_section(analysis["severity_distribution"]),
            "module_analysis": self._build_module_section(analysis["module_impact"]),
            "recommendations": analysis["recommendations"],
            "key_findings": analysis["key_findings"],
            "tables": self._build_tables(analysis),
        }

        return report

    def _build_metadata(self) -> dict:
        """Build report metadata"""
        return {
            "case_id": self.raw_data.get("case_id", "Unknown"),
            "total_frames": self.raw_data.get("total_frames", 0),
            "jank_count": self.raw_data.get("jank_count", 0),
            "analysis_type": "jank_frame_analysis",
        }

    def _build_summary(self, analysis: dict) -> dict:
        """Build summary section"""
        raw_summary = self.raw_data.get("summary", {})

        return {
            "jank_rate_percentage": raw_summary.get("jank_rate", 0),
            "avg_frame_time_ms": raw_summary.get("avg_frame_time", 0),
            "max_frame_time_ms": raw_summary.get("max_frame_time", 0),
            "min_frame_time_ms": raw_summary.get("min_frame_time", 0),
            "total_jank_frames": self.raw_data.get("jank_count", 0),
            "primary_cause": analysis["key_findings"]["primary_cause"],
            "has_patterns": analysis["key_findings"]["has_periodic_pattern"],
        }

    def _build_top_frames_section(self) -> dict:
        """Build top jank frames section with table data"""
        top_frames = self.raw_data.get("top_jank_frames", [])

        frames_table = []
        for frame in top_frames:
            top_func = frame.get("top_function", {})

            frames_table.append({
                "frame": frame.get("frame"),
                "frame_time_ms": frame.get("frame_time", 0),
                "target_fps": frame.get("target_fps", 60),
                "top_function": top_func.get("name", "N/A")[:60],
                "top_function_time_ms": top_func.get("SelfTime", 0),
                "jank_cause": frame.get("jank_cause", "Unknown"),
                "screenshot_url": frame.get("screenshot_url"),
                "screenshot_timestamp": frame.get("screenshot_timestamp"),
                "screenshot_offset": frame.get("screenshot_offset"),
            })

        # Build detailed frames with top 10 functions
        detailed_frames = []
        for frame in top_frames[:10]:  # Top 10 frames
            functions = frame.get("functions", [])
            sorted_funcs = sorted(functions, key=lambda f: f.get("SelfTime", 0), reverse=True)

            detailed_frames.append({
                "frame": frame.get("frame"),
                "frame_time_ms": frame.get("frame_time", 0),
                "jank_cause": frame.get("jank_cause", "Unknown"),
                "screenshot_url": frame.get("screenshot_url"),
                "screenshot_timestamp": frame.get("screenshot_timestamp"),
                "screenshot_offset": frame.get("screenshot_offset"),
                "top_functions": [
                    {
                        "name": f.get("name", "N/A")[:60],
                        "self_time_ms": f.get("SelfTime", 0),
                        "total_time_ms": f.get("TotalTime", 0),
                        "calls": f.get("Calls", 0),
                        "gc_alloc": f.get("TotalGC", 0),
                    }
                    for f in sorted_funcs[:10]
                ]
            })

        return {
            "summary_table": frames_table,
            "detailed_frames": detailed_frames,
        }

    def _build_patterns_section(self, patterns: dict) -> dict:
        """Build patterns analysis section"""
        return {
            "periodic_patterns": [
                {
                    "period_frames": p["period_frames"],
                    "period_seconds": p["period_seconds"],
                    "occurrences": p["occurrences"],
                    "avg_frame_time_ms": p["avg_frame_time"],
                    "description": p["description"],
                }
                for p in patterns.get("periodic", [])
            ],
            "burst_patterns": [
                {
                    "frame_range": b["frame_range"],
                    "frame_count": b["frame_count"],
                    "total_duration_ms": b["total_duration"],
                    "avg_frame_time_ms": b["avg_frame_time"],
                    "max_frame_time_ms": b["max_frame_time"],
                }
                for b in patterns.get("burst", [])
            ],
            "continuous_patterns": [
                {
                    "start_frame": c["start_frame"],
                    "end_frame": c["end_frame"],
                    "frame_count": c["frame_count"],
                    "total_duration_ms": c["total_duration"],
                    "avg_frame_time_ms": c["avg_frame_time"],
                    "max_frame_time_ms": c["max_frame_time"],
                }
                for c in patterns.get("continuous", [])
            ],
        }

    def _build_function_analysis(self, clusters: dict) -> dict:
        """Build function analysis section"""
        return {
            "frequent_functions": [
                {
                    "name": f["name"][:60],
                    "frequency": f["frequency"],
                    "avg_impact_ms": f["avg_impact"],
                    "total_impact_ms": f["total_impact"],
                }
                for f in clusters.get("frequent", [])
            ],
            "high_impact_functions": [
                {
                    "name": f["name"][:60],
                    "frequency": f["frequency"],
                    "avg_impact_ms": f["avg_impact"],
                    "total_impact_ms": f["total_impact"],
                }
                for f in clusters.get("high_impact", [])
            ],
        }

    def _build_severity_section(self, severity: dict) -> dict:
        """Build severity distribution section"""
        return {
            "minor": {
                "count": severity["minor"]["count"],
                "percentage": severity["minor"]["percentage"],
                "description": "33-50ms (1-2 frames at 30fps)",
            },
            "moderate": {
                "count": severity["moderate"]["count"],
                "percentage": severity["moderate"]["percentage"],
                "description": "50-100ms (2-3 frames)",
            },
            "severe": {
                "count": severity["severe"]["count"],
                "percentage": severity["severe"]["percentage"],
                "description": "100-200ms (3-6 frames)",
            },
            "extreme": {
                "count": severity["extreme"]["count"],
                "percentage": severity["extreme"]["percentage"],
                "description": ">200ms (6+ frames)",
            },
        }

    def _build_module_section(self, module_impact: dict) -> dict:
        """Build module impact section"""
        by_time = module_impact.get("by_total_time", [])

        return {
            "by_total_time": [
                {
                    "module": m["module"],
                    "frame_count": m["frame_count"],
                    "total_time_ms": m["total_time"],
                    "avg_time_ms": m["avg_time"],
                    "percentage": self._calculate_percentage(m["total_time"], by_time),
                }
                for m in by_time
            ],
            "by_frame_count": [
                {
                    "module": m["module"],
                    "frame_count": m["frame_count"],
                    "total_time_ms": m["total_time"],
                    "avg_time_ms": m["avg_time"],
                }
                for m in module_impact.get("by_frame_count", [])
            ],
        }

    def _calculate_percentage(self, value: float, all_values: List[dict]) -> float:
        """Calculate percentage of value compared to total"""
        total = sum(v.get("total_time", 0) for v in all_values)
        if total == 0:
            return 0
        return (value / total) * 100

    def _build_tables(self, analysis: dict) -> dict:
        """Build ready-to-use table data for Markdown generation"""
        tables = {}

        # Top frames table
        top_frames = self.raw_data.get("top_jank_frames", [])
        tables["top_frames"] = {
            "headers": ["帧号", "帧时间 (ms)", "目标帧率", "Top 1 耗时函数", "耗时 (ms)", "模块"],
            "rows": [
                [
                    str(f.get("frame")),
                    f"{f.get('frame_time', 0):.2f}",
                    "60 FPS",
                    f.get("top_function", {}).get("name", "N/A")[:50],
                    f"{f.get('top_function', {}).get('SelfTime', 0):.2f}",
                    f.get("jank_cause", "N/A"),
                ]
                for f in top_frames
            ]
        }

        # Module distribution table
        by_time = analysis["module_impact"].get("by_total_time", [])
        tables["module_distribution"] = {
            "headers": ["模块", "卡顿帧数", "总耗时 (ms)", "平均耗时 (ms)", "占比"],
            "rows": [
                [
                    m["module"],
                    str(m["frame_count"]),
                    f"{m['total_time']:.2f}",
                    f"{m['avg_time']:.2f}",
                    f"{self._calculate_percentage(m['total_time'], by_time):.1f}%",
                ]
                for m in by_time
            ]
        }

        # Severity distribution table
        severity = analysis["severity_distribution"]
        tables["severity"] = {
            "headers": ["严重程度", "数量", "占比", "描述"],
            "rows": [
                [
                    "轻微 (Minor)",
                    str(severity["minor"]["count"]),
                    f"{severity['minor']['percentage']:.1f}%",
                    "33-50ms",
                ],
                [
                    "中等 (Moderate)",
                    str(severity["moderate"]["count"]),
                    f"{severity['moderate']['percentage']:.1f}%",
                    "50-100ms",
                ],
                [
                    "严重 (Severe)",
                    str(severity["severe"]["count"]),
                    f"{severity['severe']['percentage']:.1f}%",
                    "100-200ms",
                ],
                [
                    "极端 (Extreme)",
                    str(severity["extreme"]["count"]),
                    f"{severity['extreme']['percentage']:.1f}%",
                    ">200ms",
                ],
            ]
        }

        # Frequent functions table
        frequent = analysis["function_clusters"].get("frequent", [])
        if frequent:
            tables["frequent_functions"] = {
                "headers": ["函数名", "出现次数", "平均影响 (ms)", "总影响 (ms)"],
                "rows": [
                    [
                        f["name"][:60],
                        str(f["frequency"]),
                        f"{f['avg_impact']:.2f}",
                        f"{f['total_impact']:.2f}",
                    ]
                    for f in frequent[:10]
                ]
            }

        return tables

    def format_for_ai(self) -> str:
        """Format analysis for AI prompt

        Returns a structured text representation optimized for AI understanding.
        This can be used as context in AI prompts.
        """
        report = self.generate_report_data()

        lines = []
        lines.append("# Jank Analysis Summary")
        lines.append("")

        # Summary
        summary = report["summary"]
        lines.append("## Overview")
        lines.append(f"- Total Jank Frames: {summary['total_jank_frames']}")
        lines.append(f"- Jank Rate: {summary['jank_rate_percentage']:.2f}%")
        lines.append(f"- Average Frame Time: {summary['avg_frame_time_ms']:.2f}ms")
        lines.append(f"- Max Frame Time: {summary['max_frame_time_ms']:.2f}ms")
        lines.append(f"- Primary Cause: {summary['primary_cause']}")
        lines.append("")

        # Key findings
        findings = report["key_findings"]
        lines.append("## Key Findings")
        lines.append(f"- Top problematic functions: {', '.join(findings['top_problematic_functions'][:3])}")
        lines.append(f"- Most severe jank: Frame {findings['most_severe_jank'].get('frame', 'N/A')} at {findings['most_severe_jank'].get('frame_time', 0):.2f}ms")
        lines.append(f"- Periodic patterns detected: {findings['has_periodic_pattern']}")
        lines.append("")

        # Recommendations
        lines.append("## Top Recommendations")
        for i, rec in enumerate(report["recommendations"][:5], 1):
            lines.append(f"{i}. [{rec['priority'].upper()}] {rec['category']}")
            lines.append(f"   - Issue: {rec['issue']}")
            lines.append(f"   - Suggestion: {rec['suggestion']}")
            lines.append("")

        return "\n".join(lines)
