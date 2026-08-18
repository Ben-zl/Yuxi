"""Unity Profiler Analyzer - Comparison Preprocessor

This module performs statistical analysis on comparison data before passing to AI.
It computes deltas, percentages, trends, and classifications to provide structured
insights for report generation.
"""

from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime


class ComparisonPreprocessor:
    """Preprocess comparison data with statistical analysis

    Takes raw comparison data from ComparisonCollector and performs:
    - Delta calculations (value changes)
    - Percentage calculations
    - Trend analysis (improvement/degradation)
    - Function-level comparison
    - Module-level comparison
    - Memory comparison
    - Severity classification
    - Recommendation generation
    """

    # Thresholds for determining significance
    SIGNIFICANT_CHANGE_PCT = 5.0   # 5% change is significant
    MAJOR_CHANGE_PCT = 15.0        # 15% change is major

    # Hotspot time threshold (ms)
    HOTSPOT_TIME_THRESHOLD = 1.0

    def __init__(self, raw_comparison_data: Dict[str, Any]):
        """Initialize preprocessor with raw comparison data

        Args:
            raw_comparison_data: Data from ComparisonCollector
        """
        self.raw_data = raw_comparison_data

    def analyze_all(self) -> Dict[str, Any]:
        """Perform all preprocessing analyses

        Returns:
            Dictionary containing all analysis results
        """
        return {
            "metrics_delta": self._compute_metrics_delta(),
            "functions_delta": self._compute_functions_delta(),
            "memory_delta": self._compute_memory_delta(),
            "rendering_delta": self._compute_rendering_delta(),
            "performance_categories": self._categorize_performance_changes(),
            "recommendations": self._generate_recommendations(),
        }

    def _compute_metrics_delta(self) -> Dict[str, Any]:
        """Compute delta for key metrics

        Returns:
            Metrics comparison with delta, percentage, trend, and status
        """
        report_a = self.raw_data.get("report_a", {})
        report_b = self.raw_data.get("report_b", {})

        # Extract case info
        case_info_a = report_a.get("case_info", {})
        case_info_b = report_b.get("case_info", {})

        # Extract summary
        summry_a = case_info_a.get("summry", {})
        summry_b = case_info_b.get("summry", {})

        # Extract CPU performance data
        cpu_perf_a = report_a.get("cpu_performance", {})
        cpu_perf_b = report_b.get("cpu_performance", {})

        # Extract memory info
        mem_info_a = report_a.get("memory_info", {})
        mem_info_b = report_b.get("memory_info", {})
        mem_base_a = mem_info_a.get("base_data", {})
        mem_base_b = mem_info_b.get("base_data", {})

        # Extract graphic profile
        graphic_a = report_a.get("graphic_profile", {})
        graphic_b = report_b.get("graphic_profile", {})
        graphic_base_a = graphic_a.get("base_data", {})
        graphic_base_b = graphic_b.get("base_data", {})

        # ========== Compute FPS delta ==========
        fps_a = summry_a.get("avg_fps", 0)
        fps_b = summry_b.get("avg_fps", 0)
        fps_delta = self._compute_delta(fps_a, fps_b, higher_is_better=True)

        # ========== Compute frame time delta ==========
        frame_time_a = case_info_a.get("frameTime", 0)
        frame_time_b = case_info_b.get("frameTime", 0)
        # Handle case where frameTime is a dict
        if isinstance(frame_time_a, dict):
            frame_time_a = frame_time_a.get("avg", 0)
        if isinstance(frame_time_b, dict):
            frame_time_b = frame_time_b.get("avg", 0)
        frame_time_delta = self._compute_delta(frame_time_a, frame_time_b, higher_is_better=False)

        # ========== Compute CPU time deltas ==========
        # Physics time (time series - need to compute average)
        physics_time_a = cpu_perf_a.get("physicsTime", {})
        physics_time_b = cpu_perf_b.get("physicsTime", {})
        if isinstance(physics_time_a, dict) and "frames" in physics_time_a:
            frames_a = physics_time_a["frames"]
            if isinstance(frames_a, list) and frames_a:
                physics_time_a = sum(frames_a) / len(frames_a)
            else:
                physics_time_a = 0
        else:
            physics_time_a = 0

        if isinstance(physics_time_b, dict) and "frames" in physics_time_b:
            frames_b = physics_time_b["frames"]
            if isinstance(frames_b, list) and frames_b:
                physics_time_b = sum(frames_b) / len(frames_b)
            else:
                physics_time_b = 0
        else:
            physics_time_b = 0
        physics_time_delta = self._compute_delta(physics_time_a, physics_time_b, higher_is_better=False)

        # Rendering time (time series)
        rendering_time_a = cpu_perf_a.get("renderingTime", {})
        rendering_time_b = cpu_perf_b.get("renderingTime", {})
        if isinstance(rendering_time_a, dict) and "frames" in rendering_time_a:
            frames_a = rendering_time_a["frames"]
            if isinstance(frames_a, list) and frames_a:
                rendering_time_a = sum(frames_a) / len(frames_a)
            else:
                rendering_time_a = 0
        else:
            rendering_time_a = 0

        if isinstance(rendering_time_b, dict) and "frames" in rendering_time_b:
            frames_b = rendering_time_b["frames"]
            if isinstance(frames_b, list) and frames_b:
                rendering_time_b = sum(frames_b) / len(frames_b)
            else:
                rendering_time_b = 0
        else:
            rendering_time_b = 0
        rendering_time_delta = self._compute_delta(rendering_time_a, rendering_time_b, higher_is_better=False)

        # Script time (time series)
        script_time_a = cpu_perf_a.get("scriptTime", {})
        script_time_b = cpu_perf_b.get("scriptTime", {})
        if isinstance(script_time_a, dict) and "frames" in script_time_a:
            frames_a = script_time_a["frames"]
            if isinstance(frames_a, list) and frames_a:
                script_time_a = sum(frames_a) / len(frames_a)
            else:
                script_time_a = 0
        else:
            script_time_a = 0

        if isinstance(script_time_b, dict) and "frames" in script_time_b:
            frames_b = script_time_b["frames"]
            if isinstance(frames_b, list) and frames_b:
                script_time_b = sum(frames_b) / len(frames_b)
            else:
                script_time_b = 0
        else:
            script_time_b = 0
        script_time_delta = self._compute_delta(script_time_a, script_time_b, higher_is_better=False)

        # UI time (time series)
        ui_time_a = cpu_perf_a.get("uiTime", {})
        ui_time_b = cpu_perf_b.get("uiTime", {})
        if isinstance(ui_time_a, dict) and "frames" in ui_time_a:
            frames_a = ui_time_a["frames"]
            if isinstance(frames_a, list) and frames_a:
                ui_time_a = sum(frames_a) / len(frames_a)
            else:
                ui_time_a = 0
        else:
            ui_time_a = 0

        if isinstance(ui_time_b, dict) and "frames" in ui_time_b:
            frames_b = ui_time_b["frames"]
            if isinstance(frames_b, list) and frames_b:
                ui_time_b = sum(frames_b) / len(frames_b)
            else:
                ui_time_b = 0
        else:
            ui_time_b = 0
        ui_time_delta = self._compute_delta(ui_time_a, ui_time_b, higher_is_better=False)

        # ========== Compute memory delta ==========
        mem_total_a = mem_base_a.get("max_reserved_total", 0)
        mem_total_b = mem_base_b.get("max_reserved_total", 0)
        # Handle dict values
        if isinstance(mem_total_a, dict):
            mem_total_a = 0
        if isinstance(mem_total_b, dict):
            mem_total_b = 0
        memory_delta = self._compute_delta(mem_total_a, mem_total_b, higher_is_better=False)

        # ========== Compute DrawCall deltas ==========
        drawcall_a = graphic_base_a.get("avg_DrawCalls", 0)
        drawcall_b = graphic_base_b.get("avg_DrawCalls", 0)
        drawcall_delta = self._compute_delta(drawcall_a, drawcall_b, higher_is_better=False)

        # Max DrawCalls
        max_drawcall_a = graphic_base_a.get("max_DrawCalls", 0)
        max_drawcall_b = graphic_base_b.get("max_DrawCalls", 0)
        max_drawcall_delta = self._compute_delta(max_drawcall_a, max_drawcall_b, higher_is_better=False)

        # Avg SetPassCalls
        setpass_a = graphic_base_a.get("avg_SetPassCalls", 0)
        setpass_b = graphic_base_b.get("avg_SetPassCalls", 0)
        setpass_delta = self._compute_delta(setpass_a, setpass_b, higher_is_better=False)

        # Avg Triangles
        avg_triangles_a = graphic_base_a.get("avg_Triangles", 0)
        avg_triangles_b = graphic_base_b.get("avg_Triangles", 0)
        avg_triangles_delta = self._compute_delta(avg_triangles_a, avg_triangles_b, higher_is_better=False)

        # Max Triangles
        max_triangles_a = graphic_base_a.get("max_Triangles", 0)
        max_triangles_b = graphic_base_b.get("max_Triangles", 0)
        max_triangles_delta = self._compute_delta(max_triangles_a, max_triangles_b, higher_is_better=False)

        # Avg Vertices
        avg_vertices_a = graphic_base_a.get("avg_Vertices", 0)
        avg_vertices_b = graphic_base_b.get("avg_Vertices", 0)
        avg_vertices_delta = self._compute_delta(avg_vertices_a, avg_vertices_b, higher_is_better=False)

        # ========== Compute GC deltas ==========
        gc_count_a = cpu_perf_a.get("TotalGC", 0)
        gc_count_b = cpu_perf_b.get("TotalGC", 0)
        if isinstance(gc_count_a, dict):
            gc_count_a = len(gc_count_a.get("x_data", []))
        if isinstance(gc_count_b, dict):
            gc_count_b = len(gc_count_b.get("x_data", []))
        gc_count_delta = self._compute_delta(gc_count_a, gc_count_b, higher_is_better=False)

        gc_time_a = cpu_perf_a.get("gcTime", 0)
        gc_time_b = cpu_perf_b.get("gcTime", 0)
        if isinstance(gc_time_a, dict):
            gc_time_a = 0
        if isinstance(gc_time_b, dict):
            gc_time_b = 0
        gc_time_delta = self._compute_delta(gc_time_a, gc_time_b, higher_is_better=False)

        # GC memory (from memory_info)
        gc_mem_a = mem_info_a.get("total_GC_kb", 0)
        gc_mem_b = mem_info_b.get("total_GC_kb", 0)
        if isinstance(gc_mem_a, dict):
            gc_mem_a = 0
        if isinstance(gc_mem_b, dict):
            gc_mem_b = 0
        gc_mem_delta = self._compute_delta(gc_mem_a, gc_mem_b, higher_is_better=False)

        # GC time in ms (from memory_info)
        gc_ms_a = mem_info_a.get("total_GC_ms", 0)
        gc_ms_b = mem_info_b.get("total_GC_ms", 0)
        if isinstance(gc_ms_a, dict):
            gc_ms_a = 0
        if isinstance(gc_ms_b, dict):
            gc_ms_b = 0
        gc_ms_delta = self._compute_delta(gc_ms_a, gc_ms_b, higher_is_better=False)

        return {
            "fps": fps_delta,
            "frame_time": frame_time_delta,
            "physics_time": physics_time_delta,
            "rendering_time": rendering_time_delta,
            "script_time": script_time_delta,
            "ui_time": ui_time_delta,
            "memory_total": memory_delta,
            "draw_calls": drawcall_delta,
            "max_draw_calls": max_drawcall_delta,
            "setpass_calls": setpass_delta,
            "avg_triangles": avg_triangles_delta,
            "max_triangles": max_triangles_delta,
            "avg_vertices": avg_vertices_delta,
            "gc_count": gc_count_delta,
            "gc_time": gc_time_delta,
            "gc_memory_kb": gc_mem_delta,
            "gc_time_ms": gc_ms_delta,
        }

    def _compute_functions_delta(self) -> Dict[str, Any]:
        """Compute delta for hotspot functions

        Uses two different data sources:
        - func_data_summary: For degraded/improved analysis (complete function list)
        - fun_top API: For new/lost hotspot analysis (top functions from API)

        Returns:
            Function comparison with degraded, improved, new, and lost hotspots
        """
        func_summary_a = self.raw_data.get("func_summary_a", {})
        func_summary_b = self.raw_data.get("func_summary_b", {})
        fun_top_a = self.raw_data.get("fun_top_a", [])
        fun_top_b = self.raw_data.get("fun_top_b", [])

        # ========== Degraded & Improved: Use func_data_summary (complete list) ==========
        func_list_a = func_summary_a.get("func_list", [])
        func_list_b = func_summary_b.get("func_list", [])

        # Build function maps from func_data_summary
        # Merge by name to handle duplicate entries
        map_a = {}
        for func in func_list_a:
            name = func.get("name", "")
            if name:
                if name not in map_a:
                    map_a[name] = func.copy()
                    map_a[name]["_occurrences"] = 1
                else:
                    # Accumulate self_time for duplicates
                    map_a[name]["self_time"] += func.get("self_time", 0)
                    map_a[name]["calls"] += func.get("calls", 0)
                    map_a[name]["_occurrences"] += 1

        map_b = {}
        for func in func_list_b:
            name = func.get("name", "")
            if name:
                if name not in map_b:
                    map_b[name] = func.copy()
                    map_b[name]["_occurrences"] = 1
                else:
                    map_b[name]["self_time"] += func.get("self_time", 0)
                    map_b[name]["calls"] += func.get("calls", 0)
                    map_b[name]["_occurrences"] += 1

        # Find degraded functions (in both, but time increased)
        degraded = []
        for name, func_b in map_b.items():
            if name in map_a:
                func_a = map_a[name]
                time_a = func_a.get("self_time", 0)
                time_b = func_b.get("self_time", 0)
                delta = time_b - time_a

                if delta > 0:
                    delta_pct = (delta / time_a * 100) if time_a > 0 else 0
                    degraded.append({
                        "name": name,
                        "module": self._classify_function_module(name),
                        "time_a": time_a,
                        "time_b": time_b,
                        "delta": delta,
                        "delta_pct": delta_pct,
                        "calls_a": func_a.get("calls", 0),
                        "calls_b": func_b.get("calls", 0),
                        "calls_delta": func_b.get("calls", 0) - func_a.get("calls", 0),
                    })

        # Sort by delta descending
        degraded.sort(key=lambda x: x["delta"], reverse=True)

        # Find improved functions (in both, but time decreased)
        improved = []
        for name, func_b in map_b.items():
            if name in map_a:
                func_a = map_a[name]
                time_a = func_a.get("self_time", 0)
                time_b = func_b.get("self_time", 0)
                delta = time_b - time_a

                if delta < 0:
                    delta_pct = (delta / time_a * 100) if time_a > 0 else 0
                    improved.append({
                        "name": name,
                        "module": self._classify_function_module(name),
                        "time_a": time_a,
                        "time_b": time_b,
                        "delta": delta,
                        "delta_pct": delta_pct,
                        "calls_a": func_a.get("calls", 0),
                        "calls_b": func_b.get("calls", 0),
                        "calls_delta": func_b.get("calls", 0) - func_a.get("calls", 0),
                    })

        # Sort by delta ascending (most improved first)
        improved.sort(key=lambda x: x["delta"])

        # ========== New & Lost Hotspots: Use fun_top API (top functions) ==========
        # Build maps from fun_top API data
        # fun_top structure: {fun_name, selftime_avg, selftime_max, calls, ...}
        fun_top_map_a = {f.get("fun_name"): f for f in fun_top_a}
        fun_top_map_b = {f.get("fun_name"): f for f in fun_top_b}

        # Find new hotspots (in fun_top B but not A, with significant avg self time)
        new_hotspots = []
        for name, func_b in fun_top_map_b.items():
            if name not in fun_top_map_a:
                # Use selftime_avg for threshold check
                avg_time_b = func_b.get("selftime_avg", 0)
                if avg_time_b >= self.HOTSPOT_TIME_THRESHOLD:
                    new_hotspots.append({
                        "name": name,
                        "module": self._classify_function_module(name),
                        "selftime_avg_b": avg_time_b,
                        "calls_b": func_b.get("calls", 0),
                        "valid_frame_count_b": func_b.get("valid_frame_count", 0),
                    })

        # Sort by avg self time descending
        new_hotspots.sort(key=lambda x: x["selftime_avg_b"], reverse=True)

        # Find lost hotspots (in fun_top A but not B, with significant avg self time)
        lost_hotspots = []
        for name, func_a in fun_top_map_a.items():
            if name not in fun_top_map_b:
                avg_time_a = func_a.get("selftime_avg", 0)
                if avg_time_a >= self.HOTSPOT_TIME_THRESHOLD:
                    lost_hotspots.append({
                        "name": name,
                        "module": self._classify_function_module(name),
                        "selftime_avg_a": avg_time_a,
                        "calls_a": func_a.get("calls", 0),
                        "valid_frame_count_a": func_a.get("valid_frame_count", 0),
                    })

        # Sort by avg self time descending
        lost_hotspots.sort(key=lambda x: x["selftime_avg_a"], reverse=True)

        return {
            "degraded_top_20": degraded[:20],
            "improved_top_10": improved[:10],
            "new_hotspots": new_hotspots[:20],
            "lost_hotspots": lost_hotspots[:10],
        }

    def _compute_memory_delta(self) -> Dict[str, Any]:
        """Compute memory usage delta

        Returns:
            Memory comparison data
        """
        report_a = self.raw_data.get("report_a", {})
        report_b = self.raw_data.get("report_b", {})

        mem_info_a = report_a.get("memory_info", {})
        mem_info_b = report_b.get("memory_info", {})
        mem_base_a = mem_info_a.get("base_data", {})
        mem_base_b = mem_info_b.get("base_data", {})

        # Total memory
        mem_total_a = mem_base_a.get("max_reserved_total", 0)
        mem_total_b = mem_base_b.get("max_reserved_total", 0)
        if isinstance(mem_total_a, dict):
            mem_total_a = 0
        if isinstance(mem_total_b, dict):
            mem_total_b = 0
        mem_total_delta = self._compute_delta(mem_total_a, mem_total_b, higher_is_better=False)

        # Mono heap
        mem_mono_a = mem_base_a.get("max_reserved_mono", 0)
        mem_mono_b = mem_base_b.get("max_reserved_mono", 0)
        if isinstance(mem_mono_a, dict):
            mem_mono_a = 0
        if isinstance(mem_mono_b, dict):
            mem_mono_b = 0
        mem_mono_delta = self._compute_delta(mem_mono_a, mem_mono_b, higher_is_better=False)

        # GC memory (from memory_info)
        gc_mem_a = mem_info_a.get("total_GC_kb", 0)
        gc_mem_b = mem_info_b.get("total_GC_kb", 0)
        if isinstance(gc_mem_a, dict):
            gc_mem_a = 0
        if isinstance(gc_mem_b, dict):
            gc_mem_b = 0
        gc_mem_delta = self._compute_delta(gc_mem_a, gc_mem_b, higher_is_better=False)

        # GC time in ms (from memory_info)
        gc_ms_a = mem_info_a.get("total_GC_ms", 0)
        gc_ms_b = mem_info_b.get("total_GC_ms", 0)
        if isinstance(gc_ms_a, dict):
            gc_ms_a = 0
        if isinstance(gc_ms_b, dict):
            gc_ms_b = 0
        gc_ms_delta = self._compute_delta(gc_ms_a, gc_ms_b, higher_is_better=False)

        # GC data (from cpu_performance)
        cpu_perf_a = report_a.get("cpu_performance", {})
        cpu_perf_b = report_b.get("cpu_performance", {})

        gc_count_a = cpu_perf_a.get("TotalGC", 0)
        gc_count_b = cpu_perf_b.get("TotalGC", 0)
        if isinstance(gc_count_a, dict):
            gc_count_a = len(gc_count_a.get("x_data", []))
        if isinstance(gc_count_b, dict):
            gc_count_b = len(gc_count_b.get("x_data", []))
        gc_count_delta = self._compute_delta(gc_count_a, gc_count_b, higher_is_better=False)

        gc_time_a = cpu_perf_a.get("gcTime", 0)
        gc_time_b = cpu_perf_b.get("gcTime", 0)
        if isinstance(gc_time_a, dict):
            gc_time_a = 0
        if isinstance(gc_time_b, dict):
            gc_time_b = 0
        gc_time_delta = self._compute_delta(gc_time_a, gc_time_b, higher_is_better=False)

        return {
            "memory_total": mem_total_delta,
            "memory_mono": mem_mono_delta,
            "gc_count": gc_count_delta,
            "gc_time": gc_time_delta,
            "gc_memory_kb": gc_mem_delta,
            "gc_time_ms": gc_ms_delta,
        }

    def _compute_rendering_delta(self) -> Dict[str, Any]:
        """Compute rendering performance delta

        Returns:
            Rendering comparison data
        """
        report_a = self.raw_data.get("report_a", {})
        report_b = self.raw_data.get("report_b", {})

        graphic_a = report_a.get("graphic_profile", {})
        graphic_b = report_b.get("graphic_profile", {})
        graphic_base_a = graphic_a.get("base_data", {})
        graphic_base_b = graphic_b.get("base_data", {})

        # DrawCalls (avg)
        drawcall_a = graphic_base_a.get("avg_DrawCalls", 0)
        drawcall_b = graphic_base_b.get("avg_DrawCalls", 0)
        drawcall_delta = self._compute_delta(drawcall_a, drawcall_b, higher_is_better=False)

        # Max DrawCalls
        max_drawcall_a = graphic_base_a.get("max_DrawCalls", 0)
        max_drawcall_b = graphic_base_b.get("max_DrawCalls", 0)
        max_drawcall_delta = self._compute_delta(max_drawcall_a, max_drawcall_b, higher_is_better=False)

        # SetPassCalls
        setpass_a = graphic_base_a.get("avg_SetPassCalls", 0)
        setpass_b = graphic_base_b.get("avg_SetPassCalls", 0)
        setpass_delta = self._compute_delta(setpass_a, setpass_b, higher_is_better=False)

        # Triangles (avg)
        triangles_a = graphic_base_a.get("avg_Triangles", 0)
        triangles_b = graphic_base_b.get("avg_Triangles", 0)
        triangles_delta = self._compute_delta(triangles_a, triangles_b, higher_is_better=False)

        # Max Triangles
        max_triangles_a = graphic_base_a.get("max_Triangles", 0)
        max_triangles_b = graphic_base_b.get("max_Triangles", 0)
        max_triangles_delta = self._compute_delta(max_triangles_a, max_triangles_b, higher_is_better=False)

        # Vertices
        vertices_a = graphic_base_a.get("avg_Vertices", 0)
        vertices_b = graphic_base_b.get("avg_Vertices", 0)
        vertices_delta = self._compute_delta(vertices_a, vertices_b, higher_is_better=False)

        return {
            "draw_calls": drawcall_delta,
            "max_draw_calls": max_drawcall_delta,
            "setpass_calls": setpass_delta,
            "triangles": triangles_delta,
            "max_triangles": max_triangles_delta,
            "vertices": vertices_delta,
        }

    def _categorize_performance_changes(self) -> Dict[str, Any]:
        """Categorize performance changes by module

        Returns:
            Performance categories with affected functions
        """
        functions_delta = self._compute_functions_delta()

        categories = {
            "rendering": {"issues": [], "affected_functions": []},
            "script": {"issues": [], "affected_functions": []},
            "physics": {"issues": [], "affected_functions": []},
            "animation": {"issues": [], "affected_functions": []},
            "ui": {"issues": [], "affected_functions": []},
            "other": {"issues": [], "affected_functions": []},
        }

        # Categorize degraded functions by module
        for func in functions_delta.get("degraded_top_20", []):
            module = func.get("module", "other")
            if module not in categories:
                module = "other"

            categories[module]["affected_functions"].append(func["name"])

            # Add issue description
            if func["delta_pct"] > 50:
                severity = "severe"
            elif func["delta_pct"] > 20:
                severity = "moderate"
            else:
                severity = "minor"

            categories[module]["issues"].append({
                "function": func["name"],
                "severity": severity,
                "delta_pct": func["delta_pct"],
                "delta_ms": func["delta"],
            })

        return categories

    def _generate_recommendations(self) -> List[Dict[str, Any]]:
        """Generate optimization recommendations

        Returns:
            List of recommendations with priority, category, and suggestion
        """
        recommendations = []
        metrics_delta = self._compute_metrics_delta()
        functions_delta = self._compute_functions_delta()
        performance_categories = self._categorize_performance_changes()

        # FPS degradation recommendation
        fps_delta = metrics_delta.get("fps", {})
        if fps_delta.get("delta", 0) < -2:  # FPS decreased by more than 2
            recommendations.append({
                "category": "整体性能",
                "priority": "high" if fps_delta["delta"] < -5 else "medium",
                "issue": f"平均 FPS 从 {fps_delta.get('value_a', 0):.1f} 下降到 {fps_delta.get('value_b', 0):.1f} ({fps_delta['delta']:.1f}, {fps_delta['delta_pct']:.1f}%)",
                "suggestion": "检查热点函数变化，优先优化耗时增长最多的函数",
                "affected_functions": [f["name"] for f in functions_delta.get("degraded_top_20", [])[:5]],
            })

        # Memory degradation recommendation
        mem_delta = metrics_delta.get("memory_total", {})
        if mem_delta.get("delta_pct", 0) > 10:  # Memory increased by more than 10%
            recommendations.append({
                "category": "内存",
                "priority": "high" if mem_delta["delta_pct"] > 20 else "medium",
                "issue": f"总内存从 {mem_delta.get('value_a', 0):.1f} MB 增加到 {mem_delta.get('value_b', 0):.1f} MB ({mem_delta['delta']:.1f} MB, {mem_delta['delta_pct']:.1f}%)",
                "suggestion": "检查内存分配和释放，查找可能的内存泄漏",
                "affected_functions": [],
            })

        # GC degradation recommendation
        gc_count_delta = metrics_delta.get("gc_count", {})
        if gc_count_delta.get("delta_pct", 0) > 10:  # GC count increased by more than 10%
            recommendations.append({
                "category": "GC",
                "priority": "medium",
                "issue": f"GC 次数从 {gc_count_delta.get('value_a', 0)} 增加到 {gc_count_delta.get('value_b', 0)} ({gc_count_delta['delta']}, {gc_count_delta['delta_pct']:.1f}%)",
                "suggestion": "减少运行时内存分配，使用对象池技术",
                "affected_functions": [],
            })

        # Rendering degradation recommendation
        drawcall_delta = metrics_delta.get("draw_calls", {})
        if drawcall_delta.get("delta_pct", 0) > 5:  # DrawCalls increased by more than 5%
            recommendations.append({
                "category": "渲染",
                "priority": "medium",
                "issue": f"DrawCalls 从 {drawcall_delta.get('value_a', 0):.1f} 增加到 {drawcall_delta.get('value_b', 0):.1f} ({drawcall_delta['delta']:.1f}, {drawcall_delta['delta_pct']:.1f}%)",
                "suggestion": "检查渲染批次，使用合批、GPU Instancing 等技术减少 DrawCall",
                "affected_functions": [f["name"] for f in functions_delta.get("degraded_top_20", []) if f.get("module") == "Rendering"][:5],
            })

        # Module-specific recommendations
        for module_name, module_data in performance_categories.items():
            if module_name == "other":
                continue

            issues = module_data.get("issues", [])
            severe_issues = [i for i in issues if i["severity"] == "severe"]

            if severe_issues:
                recommendations.append({
                    "category": module_name,
                    "priority": "high",
                    "issue": f"{len(severe_issues)} 个函数出现严重性能退化",
                    "suggestion": f"重点检查 {module_name} 模块的以下函数：{', '.join([i['function'][:30] for i in severe_issues[:3]])}",
                    "affected_functions": [i["function"] for i in severe_issues],
                })

        # Sort by priority
        priority_order = {"high": 0, "medium": 1, "low": 2}
        recommendations.sort(key=lambda x: priority_order.get(x["priority"], 3))

        return recommendations

    def _compute_delta(
        self,
        value_a: float,
        value_b: float,
        higher_is_better: bool = False
    ) -> Dict[str, Any]:
        """Compute delta between two values

        Args:
            value_a: First value (baseline)
            value_b: Second value (comparison)
            higher_is_better: Whether higher values are better (e.g., FPS)

        Returns:
            Dictionary with value_a, value_b, delta, delta_pct, trend, status
        """
        delta = value_b - value_a
        delta_pct = (delta / value_a * 100) if value_a > 0 else 0

        # Determine trend
        if delta > 0:
            trend = "📈 上升"
        elif delta < 0:
            trend = "📉 下降"
        else:
            trend = "→ 持平"

        # Determine status
        abs_delta_pct = abs(delta_pct)
        if abs_delta_pct < self.SIGNIFICANT_CHANGE_PCT:
            status = "✅ 稳定"
        elif abs_delta_pct < self.MAJOR_CHANGE_PCT:
            status = "⚠️ 轻微变化"
        else:
            status = "🔴 显著变化"

        # Determine if this is good or bad
        if delta == 0:
            evaluation = "neutral"
        elif higher_is_better:
            evaluation = "good" if delta > 0 else "bad"
        else:
            evaluation = "good" if delta < 0 else "bad"

        return {
            "value_a": value_a,
            "value_b": value_b,
            "delta": delta,
            "delta_pct": delta_pct,
            "trend": trend,
            "status": status,
            "evaluation": evaluation,
        }

    def _classify_function_module(self, func_name: str) -> str:
        """Classify function into module based on name patterns

        Args:
            func_name: Function name

        Returns:
            Module name
        """
        if not func_name:
            return "other"

        # Rendering patterns
        rendering_patterns = ["Draw", "Render", "Camera", "Light", "Shadow",
                            "Shader", "Mesh", "Material", "Texture", "Graphics",
                            "GPU", "ScriptableRenderer", "ForwardRenderer", "Batch"]
        for pattern in rendering_patterns:
            if pattern in func_name:
                return "Rendering"

        # Script patterns
        script_patterns = ["Mono.", "Script", "Behaviour", "Coroutine", "Assembly-"]
        for pattern in script_patterns:
            if pattern in func_name:
                return "Script"

        # Physics patterns
        physics_patterns = ["Physics.", "Rigidbody", "Collider", "FixedUpdate"]
        for pattern in physics_patterns:
            if pattern in func_name:
                return "Physics"

        # Animation patterns
        animation_patterns = ["Animation.", "Animator", "State", "Clip"]
        for pattern in animation_patterns:
            if pattern in func_name:
                return "Animation"

        # UI patterns
        ui_patterns = ["EventSystem", "GraphicRaycaster", "Canvas", "UI."]

        for pattern in ui_patterns:
            if pattern in func_name:
                return "UI"

        return "other"
