"""Unity Profiler Analyzer - Overview Collector"""

from typing import Optional

# Handle both relative and absolute imports
try:
    from .base_collector import BaseCollector
except (ImportError, ValueError):
    # Absolute imports for direct execution
    from collectors.base_collector import BaseCollector


class OverviewCollector(BaseCollector):
    """Collector for performance overview data"""

    def collect(
        self,
        case_id: str,
        use_cache: bool = True
    ) -> dict:
        """Collect overview data

        Args:
            case_id: Case ID
            use_cache: Whether to use cache

        Returns:
            Overview data containing:
            - report_info: Basic report metadata
            - case_info: Case overview and key metrics
            - cpu_performance: CPU performance data
            - memory_info: Memory usage data
            - graphic_profile: Rendering performance data
        """
        # Try to load from cache
        if use_cache:
            cached = self._load_from_cache(case_id)
            if cached is not None:
                return cached

        # Fetch from API
        data = {
            "report_info": self._get_report_info(case_id),
            "case_info": self._get_case_info(case_id),
            "cpu_performance": self._get_cpu_performance(case_id),
            "memory_info": self._get_memory_info(case_id),
            "graphic_profile": self._get_graphic_profile(case_id),
        }

        # Save to cache
        self._save_to_cache(case_id, data)

        return data

    def _fetch_from_api(self, case_id: str) -> dict:
        """Fetch overview data from API"""
        # Override parent method to provide specific implementation
        return self.collect(case_id, use_cache=False)

    def _get_report_info(self, case_id: str) -> dict:
        """Get report metadata"""
        try:
            response = self.client.report.get_report_info(case_id)
            if response.get("code") == 0:
                return response.get("data", {})
            return {}
        except Exception as e:
            if self.config.debug:
                print(f"Error getting report info: {e}")
            return {}

    def _get_case_info(self, case_id: str) -> dict:
        """Get case overview"""
        try:
            response = self.client.report.get_case_info(case_id)
            if response.get("code") == 0:
                return response.get("data", {})
            return {}
        except Exception as e:
            if self.config.debug:
                print(f"Error getting case info: {e}")
            return {}

    def _get_cpu_performance(self, case_id: str) -> dict:
        """Get CPU performance data"""
        try:
            response = self.client.cpu.get_cpu_performance(case_id)
            if response.get("code") == 0:
                return response.get("data", {})
            return {}
        except Exception as e:
            if self.config.debug:
                print(f"Error getting CPU performance: {e}")
            return {}

    def _get_memory_info(self, case_id: str) -> dict:
        """Get memory information"""
        try:
            response = self.client.memory.get_memory_info(case_id)
            if response.get("code") == 0:
                return response.get("data", {})
            return {}
        except Exception as e:
            if self.config.debug:
                print(f"Error getting memory info: {e}")
            return {}

    def _get_graphic_profile(self, case_id: str) -> dict:
        """Get rendering performance"""
        try:
            response = self.client.graphic.get_graphic_profile(case_id)
            if response.get("code") == 0:
                return response.get("data", {})
            return {}
        except Exception as e:
            if self.config.debug:
                print(f"Error getting graphic profile: {e}")
            return {}

    @property
    def data_type(self) -> str:
        """Data type identifier"""
        return "overview"
