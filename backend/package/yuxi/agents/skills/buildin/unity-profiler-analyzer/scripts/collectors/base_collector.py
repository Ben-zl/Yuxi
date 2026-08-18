"""Unity Profiler Analyzer - Base Collector"""

from abc import ABC, abstractmethod
from typing import Optional

# Handle both relative and absolute imports
try:
    from ..cache import CacheManager
    from ..utils.config import Config
except (ImportError, ValueError):
    # Absolute imports for direct execution
    from cache.cache_manager import CacheManager
    from utils.config import Config


class BaseCollector(ABC):
    """Base data collector

    Supports:
    - Progressive data fetching (on-demand)
    - Automatic cache management
    - Error retry logic
    """

    def __init__(
        self,
        client,  # UboxProfilerClient instance
        cache_manager: CacheManager,
        config: Optional[Config] = None
    ):
        """Initialize collector

        Args:
            client: Ubox Profiler API client
            cache_manager: Cache manager instance
            config: Application configuration
        """
        self.client = client
        self.cache = cache_manager
        self.config = config or Config()

    @abstractmethod
    def collect(
        self,
        case_id: str,
        use_cache: bool = True
    ) -> dict:
        """Collect data (prefers cache)

        Args:
            case_id: Case ID (UUID)
            use_cache: Whether to use cache

        Returns:
            Collected data
        """
        pass

    def _fetch_from_api(self, case_id: str) -> dict:
        """Fetch data from API (subclass implementation)

        Args:
            case_id: Case ID

        Returns:
            Data from API
        """
        raise NotImplementedError(f"{self.__class__.__name__}._fetch_from_api not implemented")

    def _save_to_cache(self, cache_key: str, data: dict):
        """Save data to cache

        Args:
            cache_key: Cache key (can be simple case_id or composite key like "case_id_sort_by")
            data: Data to cache
        """
        self.cache.set(cache_key, self.data_type, data)

    def _load_from_cache(self, cache_key: str) -> Optional[dict]:
        """Load data from cache

        Args:
            cache_key: Cache key (can be simple case_id or composite key like "case_id_sort_by")

        Returns:
            Cached data, or None if not found
        """
        return self.cache.get(cache_key, self.data_type)

    @property
    @abstractmethod
    def data_type(self) -> str:
        """Data type identifier for caching

        Returns:
            Data type string (e.g., "overview", "hotspots", "jank", "memory")
        """
        raise NotImplementedError(f"{self.__class__.__name__}.data_type not implemented")
