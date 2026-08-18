"""Unity Profiler Analyzer - Data Collectors"""

# Handle both relative and absolute imports
try:
    from .base_collector import BaseCollector
    from .overview_collector import OverviewCollector
    from .hotspot_collector import HotspotCollector
    from .jank_collector import JankCollector
    # from .memory_collector import MemoryCollector
except (ImportError, ValueError):
    # Absolute imports for direct execution
    from collectors.base_collector import BaseCollector
    from collectors.overview_collector import OverviewCollector
    from collectors.hotspot_collector import HotspotCollector
    from collectors.jank_collector import JankCollector
    # from collectors.memory_collector import MemoryCollector

__all__ = ["BaseCollector", "OverviewCollector", "HotspotCollector", "JankCollector"]  # "MemoryCollector"]
