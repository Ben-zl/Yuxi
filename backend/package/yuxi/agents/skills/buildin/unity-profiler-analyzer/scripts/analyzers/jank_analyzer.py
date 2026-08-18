"""Unity Profiler Analyzer - Jank Data Pre-Analyzer

This module performs statistical analysis on jank frame data before passing to AI.
It extracts patterns, groups related issues, and provides structured insights.

Features:
- Pattern recognition (periodic, burst, continuous jank)
- Function frequency analysis
- Module-based grouping
- Correlation analysis
- Severity classification
"""

from typing import List, Dict, Any, Tuple, Optional
from collections import Counter, defaultdict
import statistics


class JankAnalyzer:
    """Pre-analyzer for jank frame data

    Performs statistical analysis and pattern recognition on raw jank data
    to provide structured insights for AI report generation.
    """

    # Severity thresholds (in milliseconds)
    SEVERITY_LEVELS = {
        "minor": 33,      # 33ms - 50ms (1-2 frames at 30fps)
        "moderate": 50,   # 50ms - 100ms (2-3 frames)
        "severe": 100,    # 100ms - 200ms (3-6 frames)
        "extreme": 200,   # > 200ms (6+ frames)
    }

    # Pattern detection thresholds
    PERIODIC_TOLERANCE = 0.2  # 20% tolerance for periodicity
    MIN_PATTERN_FRAMES = 3    # Minimum frames to detect a pattern

    def __init__(self, jank_data: dict):
        """Initialize jank analyzer

        Args:
            jank_data: Raw jank data from JankCollector
        """
        self.raw_data = jank_data
        self.jank_frames = jank_data.get("jank_frames_with_time", [])
        self.top_jank_frames = jank_data.get("top_jank_frames", [])
        self.summary = jank_data.get("summary", {})
        self.total_frames = jank_data.get("total_frames", 0)
        self.jank_count = jank_data.get("jank_count", 0)

        # Analysis results cache
        self._patterns = None
        self._function_clusters = None
        self._severity_distribution = None
        self._module_impact = None

    def analyze_all(self) -> dict:
        """Perform complete analysis

        Returns:
            Enhanced data structure with all analysis results
        """
        return {
            "original_data": self.raw_data,
            "patterns": self.detect_patterns(),
            "function_clusters": self.cluster_functions(),
            "severity_distribution": self.classify_severity(),
            "module_impact": self.analyze_module_impact(),
            "recommendations": self.generate_recommendations(),
            "key_findings": self.extract_key_findings(),
        }

    def detect_patterns(self) -> dict:
        """Detect jank patterns

        Returns:
            Pattern analysis results
        """
        if self._patterns is not None:
            return self._patterns

        if not self.jank_frames:
            self._patterns = {"periodic": [], "burst": [], "continuous": []}
            return self._patterns

        # Sort by frame number
        sorted_frames = sorted(self.jank_frames, key=lambda f: f.get("frame", 0))
        frame_numbers = [f.get("frame", 0) for f in sorted_frames]
        frame_times = [f.get("frame_time", 0) for f in sorted_frames]

        patterns = {
            "periodic": self._detect_periodic_patterns(frame_numbers, frame_times),
            "burst": self._detect_burst_patterns(sorted_frames),
            "continuous": self._detect_continuous_patterns(sorted_frames),
        }

        self._patterns = patterns
        return patterns

    def _detect_periodic_patterns(
        self,
        frame_numbers: List[int],
        frame_times: List[float]
    ) -> List[dict]:
        """Detect periodic jank patterns

        Args:
            frame_numbers: Sorted frame numbers
            frame_times: Corresponding frame times

        Returns:
            List of periodic patterns detected
        """
        if len(frame_numbers) < self.MIN_PATTERN_FRAMES:
            return []

        patterns = []

        # Calculate intervals between consecutive jank frames
        intervals = []
        for i in range(1, len(frame_numbers)):
            interval = frame_numbers[i] - frame_numbers[i - 1]
            intervals.append(interval)

        if not intervals:
            return []

        # Group similar intervals
        interval_groups = defaultdict(list)
        for i, interval in enumerate(intervals):
            # Find existing group with similar interval
            found = False
            for base_interval in list(interval_groups.keys()):
                if abs(interval - base_interval) / base_interval <= self.PERIODIC_TOLERANCE:
                    interval_groups[base_interval].append((frame_numbers[i], interval))
                    found = True
                    break

            if not found:
                interval_groups[interval].append((frame_numbers[i], interval))

        # Find periodic patterns (groups with 3+ occurrences)
        for base_interval, occurrences in interval_groups.items():
            if len(occurrences) >= self.MIN_PATTERN_FRAMES:
                # Calculate average frame time for this pattern
                avg_frame_time = statistics.mean([
                    frame_times[frame_numbers.index(f[0])] for f in occurrences
                ])

                patterns.append({
                    "period_frames": base_interval,
                    "period_seconds": base_interval / 60.0,  # Assume 60 FPS
                    "occurrences": len(occurrences),
                    "frame_numbers": [f[0] for f in occurrences],
                    "avg_frame_time": avg_frame_time,
                    "description": f"Every {base_interval} frames (~{base_interval/60.0:.1f}s)",
                })

        # Sort by occurrences
        patterns.sort(key=lambda p: p["occurrences"], reverse=True)

        return patterns[:5]  # Return top 5 patterns

    def _detect_burst_patterns(self, sorted_frames: List[dict]) -> List[dict]:
        """Detect burst jank patterns (closely grouped frames)

        Args:
            sorted_frames: Frames sorted by frame number

        Returns:
            List of burst patterns
        """
        bursts = []
        if not sorted_frames:
            return bursts

        # Group consecutive frames within 10 frames of each other
        current_burst = [sorted_frames[0]]
        burst_threshold = 10  # frames

        for i in range(1, len(sorted_frames)):
            prev_frame = current_burst[-1].get("frame", 0)
            curr_frame = sorted_frames[i].get("frame", 0)

            if curr_frame - prev_frame <= burst_threshold:
                current_burst.append(sorted_frames[i])
            else:
                # End current burst
                if len(current_burst) >= 2:
                    bursts.append(current_burst)
                current_burst = [sorted_frames[i]]

        # Don't forget the last burst
        if len(current_burst) >= 2:
            bursts.append(current_burst)

        # Format burst data
        formatted_bursts = []
        for burst in bursts:
            frame_times = [f.get("frame_time", 0) for f in burst]
            total_duration = sum(frame_times)

            formatted_bursts.append({
                "frame_range": f"{burst[0].get('frame')} - {burst[-1].get('frame')}",
                "frame_count": len(burst),
                "total_duration": total_duration,
                "avg_frame_time": statistics.mean(frame_times),
                "max_frame_time": max(frame_times),
                "frames": [f.get("frame") for f in burst],
            })

        # Sort by total duration
        formatted_bursts.sort(key=lambda b: b["total_duration"], reverse=True)

        return formatted_bursts[:5]  # Return top 5 bursts

    def _detect_continuous_patterns(self, sorted_frames: List[dict]) -> List[dict]:
        """Detect continuous jank patterns (3+ consecutive jank frames)

        Args:
            sorted_frames: Frames sorted by frame number

        Returns:
            List of continuous patterns
        """
        continuous = []

        # Find consecutive frames
        for i in range(len(sorted_frames) - 2):
            f1 = sorted_frames[i].get("frame", 0)
            f2 = sorted_frames[i + 1].get("frame", 0)
            f3 = sorted_frames[i + 2].get("frame", 0)

            # Check if 3 consecutive frames
            if f2 == f1 + 1 and f3 == f2 + 1:
                # Find the extent of this continuous pattern
                start_idx = i
                end_idx = i + 2

                # Extend pattern forward
                while end_idx + 1 < len(sorted_frames):
                    next_frame = sorted_frames[end_idx + 1].get("frame", 0)
                    curr_frame = sorted_frames[end_idx].get("frame", 0)
                    if next_frame == curr_frame + 1:
                        end_idx += 1
                    else:
                        break

                # Extract pattern
                pattern_frames = sorted_frames[start_idx:end_idx + 1]
                frame_times = [f.get("frame_time", 0) for f in pattern_frames]

                continuous.append({
                    "start_frame": pattern_frames[0].get("frame"),
                    "end_frame": pattern_frames[-1].get("frame"),
                    "frame_count": len(pattern_frames),
                    "total_duration": sum(frame_times),
                    "avg_frame_time": statistics.mean(frame_times),
                    "max_frame_time": max(frame_times),
                })

                # Skip to end of this pattern
                i = end_idx

        # Remove duplicates and sort
        unique_continuous = []
        seen = set()
        for pattern in continuous:
            key = (pattern["start_frame"], pattern["end_frame"])
            if key not in seen:
                seen.add(key)
                unique_continuous.append(pattern)

        unique_continuous.sort(key=lambda p: p["frame_count"], reverse=True)

        return unique_continuous[:5]  # Return top 5

    def cluster_functions(self) -> dict:
        """Cluster functions by similarity and frequency

        Returns:
            Function cluster analysis
        """
        if self._function_clusters is not None:
            return self._function_clusters

        if not self.top_jank_frames:
            self._function_clusters = {"frequent": [], "high_impact": [], "correlated": []}
            return self._function_clusters

        # Collect all functions from top jank frames
        function_counter = Counter()
        function_impact = defaultdict(float)  # frame_time * frequency
        function_module = {}

        for frame in self.top_jank_frames:
            frame_time = frame.get("frame_time", 0)
            functions = frame.get("functions", [])

            # Sort by SelfTime to find top functions in this frame
            sorted_funcs = sorted(functions, key=lambda f: f.get("SelfTime", 0), reverse=True)

            # Take top 5 functions from each frame
            for func in sorted_funcs[:5]:
                func_name = func.get("name", "Unknown")
                self_time = func.get("SelfTime", 0)

                function_counter[func_name] += 1
                function_impact[func_name] += self_time
                function_module[func_name] = func.get("level", 0)

        # Frequent functions (appear in 3+ frames)
        frequent = [
            {
                "name": name,
                "frequency": count,
                "avg_impact": function_impact[name] / count,
                "total_impact": function_impact[name],
            }
            for name, count in function_counter.most_common()
            if count >= 3
        ]

        # High impact functions (top 10 by total impact)
        high_impact = [
            {
                "name": name,
                "frequency": function_counter[name],
                "avg_impact": function_impact[name] / function_counter[name],
                "total_impact": function_impact[name],
            }
            for name, _ in sorted(function_impact.items(), key=lambda x: x[1], reverse=True)
        ][:10]

        self._function_clusters = {
            "frequent": frequent,
            "high_impact": high_impact,
            "correlated": [],  # Could add correlation analysis later
        }

        return self._function_clusters

    def classify_severity(self) -> dict:
        """Classify jank frames by severity

        Returns:
            Severity distribution
        """
        if self._severity_distribution is not None:
            return self._severity_distribution

        if not self.jank_frames:
            self._severity_distribution = {
                "minor": {"count": 0, "percentage": 0, "frames": []},
                "moderate": {"count": 0, "percentage": 0, "frames": []},
                "severe": {"count": 0, "percentage": 0, "frames": []},
                "extreme": {"count": 0, "percentage": 0, "frames": []},
            }
            return self._severity_distribution

        severity = {
            "minor": {"count": 0, "percentage": 0, "frames": []},
            "moderate": {"count": 0, "percentage": 0, "frames": []},
            "severe": {"count": 0, "percentage": 0, "frames": []},
            "extreme": {"count": 0, "percentage": 0, "frames": []},
        }

        total = len(self.jank_frames)

        for frame in self.jank_frames:
            frame_time = frame.get("frame_time", 0)
            frame_num = frame.get("frame", 0)

            if frame_time < self.SEVERITY_LEVELS["moderate"]:
                category = "minor"
            elif frame_time < self.SEVERITY_LEVELS["severe"]:
                category = "moderate"
            elif frame_time < self.SEVERITY_LEVELS["extreme"]:
                category = "severe"
            else:
                category = "extreme"

            severity[category]["count"] += 1
            severity[category]["frames"].append(frame_num)

        # Calculate percentages
        for category in severity.values():
            category["percentage"] = (category["count"] / total * 100) if total > 0 else 0

        self._severity_distribution = severity
        return severity

    def analyze_module_impact(self) -> dict:
        """Analyze impact by module type

        Returns:
            Module impact analysis
        """
        if self._module_impact is not None:
            return self._module_impact

        if not self.top_jank_frames:
            self._module_impact = {}
            return self._module_impact

        module_stats = defaultdict(lambda: {"count": 0, "total_time": 0, "frames": []})

        for frame in self.top_jank_frames:
            jank_cause = frame.get("jank_cause", "Unknown")
            frame_time = frame.get("frame_time", 0)
            frame_num = frame.get("frame", 0)

            module_stats[jank_cause]["count"] += 1
            module_stats[jank_cause]["total_time"] += frame_time
            module_stats[jank_cause]["frames"].append(frame_num)

        # Convert to list and sort by total time
        module_list = [
            {
                "module": module,
                "frame_count": stats["count"],
                "total_time": stats["total_time"],
                "avg_time": stats["total_time"] / stats["count"],
                "frames": stats["frames"],
            }
            for module, stats in module_stats.items()
        ]

        module_list.sort(key=lambda m: m["total_time"], reverse=True)

        self._module_impact = {
            "by_total_time": module_list,
            "by_frame_count": sorted(module_list, key=lambda m: m["frame_count"], reverse=True),
        }

        return self._module_impact

    def generate_recommendations(self) -> List[dict]:
        """Generate optimization recommendations based on analysis

        Returns:
            List of recommendations with priority
        """
        recommendations = []

        # Analyze module impact
        module_impact = self.analyze_module_impact()
        by_time = module_impact.get("by_total_time", [])

        # Check for specific patterns
        patterns = self.detect_patterns()

        if patterns.get("periodic"):
            top_periodic = patterns["periodic"][0]
            recommendations.append({
                "priority": "high" if top_periodic["avg_frame_time"] > 50 else "medium",
                "category": "Periodic Jank",
                "issue": f"Periodic jank detected every {top_periodic['period_frames']} frames",
                "impact": f"Affects {top_periodic['occurrences']} frames, avg {top_periodic['avg_frame_time']:.1f}ms",
                "suggestion": "Check for timed events, garbage collection, or physics FixedUpdate",
            })

        # Module-specific recommendations
        for module in by_time[:3]:
            module_name = module["module"]
            frame_count = module["frame_count"]

            if module_name == "Rendering":
                recommendations.append({
                    "priority": "high" if frame_count >= 3 else "medium",
                    "category": "Rendering",
                    "issue": f"{frame_count} frames affected by rendering issues",
                    "impact": f"Total {module['total_time']:.1f}ms",
                    "suggestion": "Reduce DrawCalls, optimize shaders, use GPU instancing",
                })
            elif module_name == "Physics":
                recommendations.append({
                    "priority": "high" if frame_count >= 3 else "medium",
                    "category": "Physics",
                    "issue": f"{frame_count} frames affected by physics simulation",
                    "impact": f"Total {module['total_time']:.1f}ms",
                    "suggestion": "Adjust Fixed Timestep, reduce collider count, use simplified colliders",
                })
            elif module_name == "Script":
                recommendations.append({
                    "priority": "medium",
                    "category": "Script Execution",
                    "issue": f"{frame_count} frames affected by script execution",
                    "impact": f"Total {module['total_time']:.1f}ms",
                    "suggestion": "Optimize Update loops, use object pooling, reduce allocations",
                })
            elif module_name == "GC":
                recommendations.append({
                    "priority": "high",
                    "category": "Garbage Collection",
                    "issue": "GC triggering frame jank",
                    "impact": f"Total {module['total_time']:.1f}ms",
                    "suggestion": "Reduce allocations, use object pools, pre-allocate collections",
                })
            elif module_name == "Resource":
                recommendations.append({
                    "priority": "high",
                    "category": "Resource Loading",
                    "issue": f"{frame_count} frames affected by resource loading",
                    "impact": f"Total {module['total_time']:.1f}ms",
                    "suggestion": "Use async loading, preload resources, implement loading screens",
                })

        # Severity-based recommendations
        severity = self.classify_severity()
        extreme_count = severity["extreme"]["count"]

        if extreme_count > 0:
            recommendations.append({
                "priority": "critical",
                "category": "Extreme Jank",
                "issue": f"{extreme_count} frames with >200ms frame time",
                "impact": "Severe performance degradation",
                "suggestion": "URGENT: Investigate and fix extreme jank frames immediately",
            })

        # Sort by priority
        priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        recommendations.sort(key=lambda r: priority_order.get(r["priority"], 4))

        return recommendations

    def extract_key_findings(self) -> dict:
        """Extract key findings for summary

        Returns:
            Key findings summary
        """
        findings = {
            "total_jank_frames": self.jank_count,
            "jank_rate": self.summary.get("jank_rate", 0),
            "avg_frame_time": self.summary.get("avg_frame_time", 0),
            "max_frame_time": self.summary.get("max_frame_time", 0),
            "primary_cause": self._get_primary_cause(),
            "top_problematic_functions": self._get_top_functions(),
            "most_severe_jank": self._get_most_severe_jank(),
            "has_periodic_pattern": len(self.detect_patterns().get("periodic", [])) > 0,
        }

        return findings

    def _get_primary_cause(self) -> str:
        """Get primary jank cause"""
        module_impact = self.analyze_module_impact()
        by_time = module_impact.get("by_total_time", [])

        if by_time:
            return by_time[0]["module"]
        return "Unknown"

    def _get_top_functions(self) -> List[str]:
        """Get top problematic functions"""
        clusters = self.cluster_functions()
        high_impact = clusters.get("high_impact", [])

        return [f["name"] for f in high_impact[:5]]

    def _get_most_severe_jank(self) -> dict:
        """Get most severe jank frame"""
        if not self.jank_frames:
            return {}

        sorted_by_time = sorted(self.jank_frames, key=lambda f: f.get("frame_time", 0), reverse=True)
        most_severe = sorted_by_time[0]

        return {
            "frame": most_severe.get("frame"),
            "frame_time": most_severe.get("frame_time"),
            "severity": self._classify_frame_severity(most_severe.get("frame_time", 0)),
        }

    def _classify_frame_severity(self, frame_time: float) -> str:
        """Classify a single frame's severity"""
        if frame_time < self.SEVERITY_LEVELS["moderate"]:
            return "minor"
        elif frame_time < self.SEVERITY_LEVELS["severe"]:
            return "moderate"
        elif frame_time < self.SEVERITY_LEVELS["extreme"]:
            return "severe"
        else:
            return "extreme"
