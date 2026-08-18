"""Unity Profiler Analyzer - Comparison Collector

This module collects data for two performance reports to enable comparison analysis.
It uses existing collectors (OverviewCollector, HotspotCollector) to fetch data
for both case_id_a and case_id_b.
"""

from typing import Dict, Any, Optional, List

# Handle both relative and absolute imports
try:
    from ..collectors import OverviewCollector, HotspotCollector
    from ..cache import CacheManager
    from ..utils.config import Config
except (ImportError, ValueError):
    # Absolute imports for direct execution
    from collectors import OverviewCollector, HotspotCollector
    from cache.cache_manager import CacheManager
    from utils.config import Config


class ComparisonCollector:
    """Collector for comparing two performance reports

    Collects data from two cases (case_a and case_b) using existing collectors.
    Returns raw comparison data for further preprocessing by ComparisonPreprocessor.
    """

    # Hotspot threshold for identifying "hot" functions
    HOTSPOT_TIME_THRESHOLD = 1.0  # ms

    def __init__(
        self,
        client,
        cache_manager: CacheManager,
        config: Optional[Config] = None
    ):
        """Initialize comparison collector

        Args:
            client: UboxProfilerClient instance
            cache_manager: Cache manager instance
            config: Optional configuration
        """
        self.client = client
        self.cache_manager = cache_manager
        self.config = config or Config()

        # Initialize existing collectors
        self.overview_collector = OverviewCollector(client, cache_manager, config)
        self.hotspot_collector = HotspotCollector(client, cache_manager, config)

    def collect_comparison_data(
        self,
        case_id_a: str,
        case_id_b: str,
        use_cache: bool = True,
        hotspot_top_n: int = 100
    ) -> Dict[str, Any]:
        """Collect comparison data for two reports

        Args:
            case_id_a: First case ID (baseline)
            case_id_b: Second case ID (comparison target)
            use_cache: Whether to use cache
            hotspot_top_n: Number of top hotspot functions to collect

        Returns:
            Raw comparison data containing:
            - case_id_a, case_id_b: Case IDs
            - report_a: Overview data for case A
            - report_b: Overview data for case B
            - func_summary_a: Complete function list from func_data_summary (for degraded/improved)
            - func_summary_b: Complete function list from func_data_summary (for degraded/improved)
            - fun_top_a: Top functions from fun_top API (for new/lost)
            - fun_top_b: Top functions from fun_top API (for new/lost)
        """
        # Collect overview data for both cases
        report_a = self.overview_collector.collect(case_id_a, use_cache)
        report_b = self.overview_collector.collect(case_id_b, use_cache)

        # Collect func_data_summary for degraded/improved analysis
        # This provides complete function list from MinIO
        func_summary_a = self._get_func_summary(case_id_a, use_cache)
        func_summary_b = self._get_func_summary(case_id_b, use_cache)

        # Collect fun_top for new/lost hotspot analysis
        # This provides top functions from API
        fun_top_a = self._get_fun_top(case_id_a, use_cache)
        fun_top_b = self._get_fun_top(case_id_b, use_cache)

        # Build comparison data structure
        comparison_data = {
            "case_id_a": case_id_a,
            "case_id_b": case_id_b,
            "report_a": report_a,
            "report_b": report_b,
            "func_summary_a": func_summary_a,
            "func_summary_b": func_summary_b,
            "fun_top_a": fun_top_a,
            "fun_top_b": fun_top_b,
        }

        return comparison_data

    def _get_func_summary(self, case_id: str, use_cache: bool = True) -> Dict[str, Any]:
        """Get complete function list from func_data_summary

        Args:
            case_id: Case ID
            use_cache: Whether to use cache

        Returns:
            Function list with keys: func_list (list), total_functions (int)
        """
        try:
            func_list = self.hotspot_collector._get_func_list(case_id, use_cache)
            return {
                "func_list": func_list,
                "total_functions": len(func_list),
            }
        except Exception as e:
            if self.config.debug:
                print(f"Error getting func_summary for {case_id}: {e}")
            return {"func_list": [], "total_functions": 0}

    def _get_fun_top(self, case_id: str, use_cache: bool = True) -> List[Dict]:
        """Get top functions from fun_top API

        Args:
            case_id: Case ID
            use_cache: Whether to use cache

        Returns:
            List of top functions from fun_top API
        """
        try:
            response = self.client.report.get_fun_top(case_id, 0, 0)
            if response.get("code") == 0:
                fun_top_data = response.get("data", [])
                if isinstance(fun_top_data, list):
                    return fun_top_data
            return []
        except Exception as e:
            if self.config.debug:
                print(f"Error getting fun_top for {case_id}: {e}")
            return []

    def get_module_data(
        self,
        case_id: str,
        use_cache: bool = True
    ) -> Dict[str, Any]:
        """Get module-level performance data for a case

        Args:
            case_id: Case ID
            use_cache: Whether to use cache

        Returns:
            Module performance data
        """
        # Get overview data to extract module info
        overview = self.overview_collector.collect(case_id, use_cache)

        # Extract module timing from case_info if available
        case_info = overview.get("case_info", {})
        frame_time = case_info.get("frameTime", {})

        # Extract module breakdown (if available in the API)
        # This will be used for module-level comparison
        modules = {}
        if isinstance(frame_time, dict):
            # Try to extract module timing data
            for key, value in frame_time.items():
                if isinstance(value, (int, float)):
                    modules[key] = value

        return {
            "case_id": case_id,
            "modules": modules,
        }

    def get_memory_data(
        self,
        case_id: str,
        use_cache: bool = True
    ) -> Dict[str, Any]:
        """Get memory data for a case

        Args:
            case_id: Case ID
            use_cache: Whether to use cache

        Returns:
            Memory usage data
        """
        overview = self.overview_collector.collect(case_id, use_cache)
        memory_info = overview.get("memory_info", {})
        cpu_perf = overview.get("cpu_performance", {})

        return {
            "case_id": case_id,
            "memory_info": memory_info,
            "gc_count": cpu_perf.get("TotalGC", 0),
            "gc_time": cpu_perf.get("gcTime", 0),
        }
