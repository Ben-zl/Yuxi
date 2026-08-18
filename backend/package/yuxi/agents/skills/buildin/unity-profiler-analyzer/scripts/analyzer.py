"""Unity Profiler Performance Analyzer - Main Analyzer"""

import sys
import os

# Try multiple paths to find ubox_profiler module
def add_ubox_to_path():
    """Add ubox-api to Python path"""
    current_dir = os.path.dirname(os.path.abspath(__file__))

    # Path candidates in order of preference
    # We need to find the ubox-api directory (which contains ubox_profiler subdirectory)
    path_candidates = [
        # When in Claude Code's skills directory (skills/utils/ubox-api)
        os.path.abspath(os.path.join(current_dir, "../../../utils")),
        # Relative to unity-profiler-analyzer directory (when in Agent-skills repo)
        os.path.abspath(os.path.join(current_dir, "../../utils")),
        # When skill is in Claude Code's skill directory (sibling to Agent-skills)
        os.path.abspath(os.path.join(current_dir, "../../../Agent-skills/utils")),
        # When utils is a sibling to unity-profiler-analyzer
        os.path.abspath(os.path.join(current_dir, "../../../../utils")),
    ]

    # Check each candidate path for ubox-api directory
    for path in path_candidates:
        ubox_api_path = os.path.join(path, "ubox-api")
        ubox_profiler_path = os.path.join(ubox_api_path, "ubox_profiler")

        # Check if both ubox-api and ubox_profiler exist
        if os.path.exists(ubox_profiler_path) and os.path.exists(os.path.join(ubox_profiler_path, "__init__.py")):
            sys.path.insert(0, ubox_api_path)
            return ubox_api_path

    # Fallback: try to find ubox-api in any parent directory
    parent = current_dir
    for _ in range(5):  # Check up to 5 parent levels
        parent = os.path.dirname(parent)

        # Check for utils/ubox-api/ubox_profiler
        utils_ubox_path = os.path.join(parent, "utils", "ubox-api")
        ubox_profiler_path = os.path.join(utils_ubox_path, "ubox_profiler")

        if os.path.exists(ubox_profiler_path) and os.path.exists(os.path.join(ubox_profiler_path, "__init__.py")):
            sys.path.insert(0, utils_ubox_path)
            return utils_ubox_path

        # Check for direct ubox-api/ubox_profiler
        direct_ubox_path = os.path.join(parent, "ubox-api")
        ubox_profiler_path = os.path.join(direct_ubox_path, "ubox_profiler")

        if os.path.exists(ubox_profiler_path) and os.path.exists(os.path.join(ubox_profiler_path, "__init__.py")):
            sys.path.insert(0, direct_ubox_path)
            return direct_ubox_path

    # Raise error if not found
    raise ImportError(
        f"Could not find ubox_profiler module. "
        f"Please ensure ubox-api is available in a utils directory. "
        f"Current directory: {current_dir}"
    )

# Add ubox-api to path
try:
    add_ubox_to_path()
except ImportError as e:
    # If auto-detection fails, try importing anyway (might be installed globally)
    pass

from ubox_profiler import UboxProfilerClient

# Handle both relative and absolute imports
try:
    from .cache.cache_manager import CacheManager
    from .collectors import OverviewCollector, HotspotCollector, JankCollector
    from .utils.config import Config
    from .utils.thresholds import Thresholds
    from .utils.helpers import get_fps_status, get_memory_status, get_drawcall_status
    # JankReporter for AI-Ready mode
    from .reporters.jank_reporter import JankReporter
    # Comparison classes for report comparison
    from .comparers import ComparisonCollector, ComparisonReporter
except (ImportError, ValueError):
    # Absolute imports for direct execution
    from cache.cache_manager import CacheManager
    from collectors import OverviewCollector, HotspotCollector, JankCollector
    from utils.config import Config
    from utils.thresholds import Thresholds
    from utils.helpers import get_fps_status, get_memory_status, get_drawcall_status
    # JankReporter for AI-Ready mode
    from reporters.jank_reporter import JankReporter
    # Comparison classes for report comparison
    from comparers import ComparisonCollector, ComparisonReporter


class ProfilerAnalyzer:
    """Unity Profiler performance analysis main class

    Features:
    - Progressive data fetching
    - Local cache management
    - Big data preprocessing
    - Multi-dimensional analysis
    """

    def __init__(
        self,
        base_url: str,
        project_id: str,
        cache_dir: str = "./cache",
        cache_ttl: int = 86400,
        enable_cache: bool = True,
        enable_preprocess: bool = True,
        platform: str = "mobile"
    ):
        """Initialize the analyzer

        Args:
            base_url: API base URL
            project_id: Project ID (APPKEY)
            cache_dir: Cache directory path
            cache_ttl: Cache TTL in seconds
            enable_cache: Whether to enable caching
            enable_preprocess: Whether to enable preprocessing
            platform: Target platform ('mobile' or 'pc', default: 'mobile')
        """
        # Normalize base_url - remove /api suffix if present (endpoints already include /api/)
        normalized_base_url = base_url.rstrip("/")
        if normalized_base_url.endswith("/api"):
            normalized_base_url = normalized_base_url[:-4].rstrip("/")

        # Initialize ubox client
        self.client = UboxProfilerClient(
            base_url=normalized_base_url,
            project_id=project_id
        )

        # Initialize cache manager
        self.cache_manager = CacheManager(cache_dir, cache_ttl)
        self.enable_cache = enable_cache
        self.enable_preprocess = enable_preprocess

        # Initialize configuration
        self.config = Config()
        self.platform = platform
        self.thresholds = Thresholds(platform=platform)

        # Initialize collectors
        self.overview_collector = OverviewCollector(
            self.client,
            self.cache_manager,
            self.config
        )
        self.hotspot_collector = HotspotCollector(
            self.client,
            self.cache_manager,
            self.config
        )
        self.jank_collector = JankCollector(
            self.client,
            self.cache_manager,
            self.config
        )

    def analyze_overview(
        self,
        case_id: str,
        use_cache: bool = True
    ) -> dict:
        """Analyze performance overview

        Args:
            case_id: Case ID (UUID)
            use_cache: Whether to use cache

        Returns:
            Overview analysis result
        """
        if self.config.verbose:
            print(f"Analyzing overview for case: {case_id}")

        # Collect data
        data = self.overview_collector.collect(case_id, use_cache)

        # Analyze and evaluate
        result = self._analyze_overview_data(data)

        return result

    def analyze_hotspots(
        self,
        case_id: str,
        top_n: int = 20,
        sort_by: str = "self_time",
        min_calls: int = 0,
        exclude_unity_entry: bool = True,
        use_cache: bool = True
    ) -> dict:
        """Analyze hotspot functions

        Args:
            case_id: Case ID
            top_n: Number of top functions
            sort_by: Sort metric (self_time/total_time/valid_frame_avg/high_cost_frames)
            min_calls: Minimum call count threshold (for filtering valid frames)
            exclude_unity_entry: Whether to exclude Unity common entry point functions (default: True)
            use_cache: Whether to use cache

        Returns:
            Hotspot analysis result
        """
        if self.config.verbose:
            print(f"Analyzing hotspots for case: {case_id}")
            if exclude_unity_entry:
                print("Excluding Unity entry point functions...")

        # Collect data
        data = self.hotspot_collector.collect(case_id, use_cache, top_n, sort_by, min_calls, exclude_unity_entry)

        # Check if big data
        if data.get("is_big_data") and self.enable_preprocess:
            if self.config.verbose:
                print(f"Big data detected ({data['total_functions']} functions), preprocessing...")

            # TODO: Implement preprocessing
            # For now, just use the top functions
            pass

        # Analyze and sort
        result = self._analyze_hotspot_data(data, top_n, sort_by)

        return result

    def analyze_janks(
        self,
        case_id: str,
        top_n: int = 10,
        analyze_details: bool = True,
        use_cache: bool = True
    ) -> dict:
        """Analyze jank frames (frames exceeding target frame time)

        Args:
            case_id: Case ID
            top_n: Number of top jank frames to analyze in detail
            analyze_details: Whether to analyze frame performance details
            use_cache: Whether to use cache

        Returns:
            Jank analysis result containing:
            - jank_frames: Complete list of jank frames
            - top_jank_frames: Top N jank frames with function details
            - summary: Statistical summary
            - total_frames: Total frame count
            - jank_count: Number of jank frames
        """
        if self.config.verbose:
            print(f"Analyzing jank frames for case: {case_id}")

        # Collect data
        data = self.jank_collector.collect(case_id, use_cache, top_n, analyze_details)

        # Enhance with classification
        if analyze_details and data.get("top_jank_frames"):
            for frame in data["top_jank_frames"]:
                frame["jank_cause"] = self.jank_collector.classify_jank_cause(frame)

        return data

    def analyze_janks_with_ai_ready(
        self,
        case_id: str,
        top_n: int = 10,
        analyze_details: bool = True,
        use_cache: bool = True,
        with_screenshots: bool = False,
        project_id: str = None,
        use_timestamp_screenshots: bool = False,
        max_screenshot_offset_seconds: float = 10.0
    ) -> dict:
        """Analyze jank frames with Python preprocessing for AI consumption

        This method performs comprehensive statistical analysis on the jank data
        before passing to AI, including:
        - Pattern recognition (periodic, burst, continuous)
        - Function frequency and impact analysis
        - Severity classification
        - Module-based grouping
        - Auto-generated recommendations
        - Screenshot URLs (if with_screenshots=True)

        Args:
            case_id: Case ID
            top_n: Number of top jank frames to analyze in detail
            analyze_details: Whether to analyze frame performance details
            use_cache: Whether to use cache
            with_screenshots: Whether to include screenshot URLs in the result
            project_id: Project ID (APPKEY) - required for screenshot URLs
            use_timestamp_screenshots: Use timestamp-based screenshot matching (default: False)
                When True, matches screenshots by calculating frame timestamps from
                frameTime.frames data with calibration from raw profiler files
            max_screenshot_offset_seconds: Maximum time difference for screenshot matching
                (only used when use_timestamp_screenshots=True)

        Returns:
            Enhanced jank analysis result with preprocessed insights
        """
        if self.config.verbose:
            print(f"Analyzing jank frames with AI-ready preprocessing for case: {case_id}")
            if with_screenshots:
                if use_timestamp_screenshots:
                    print("Screenshot fetching enabled (timestamp-based matching)...")
                else:
                    print("Screenshot fetching enabled (frame number matching)...")

        # Step 1: Collect raw data (with screenshots if requested)
        if with_screenshots:
            # Use project_id from client config if not provided
            if not project_id:
                project_id = self.client.config.project_id

            if use_timestamp_screenshots:
                # Use new timestamp-based screenshot matching
                raw_data = self.jank_collector.collect_with_timestamp_screenshots(
                    case_id, use_cache, top_n, analyze_details, project_id,
                    max_screenshot_offset_seconds
                )
            else:
                # Use legacy frame number matching
                raw_data = self.jank_collector.collect_with_screenshots(
                    case_id, use_cache, top_n, analyze_details, project_id
                )
        else:
            raw_data = self.jank_collector.collect(case_id, use_cache, top_n, analyze_details)

        # Add case_id and project_id to raw data for reporter
        raw_data["case_id"] = case_id
        raw_data["project_id"] = project_id or self.client.config.project_id

        # Step 2: Python preprocessing with JankReporter
        # Generate AI-ready report data
        reporter = JankReporter(raw_data)
        ai_ready_data = reporter.generate_report_data()

        if self.config.verbose:
            print(f"Preprocessing complete: {len(ai_ready_data['recommendations'])} recommendations generated")

        return ai_ready_data

    def get_cache_stats(self) -> dict:
        """Get cache statistics

        Returns:
            Cache statistics
        """
        return self.cache_manager.get_stats()

    def clear_cache(self, case_id: str = None):
        """Clear cache

        Args:
            case_id: Case ID to clear. If None, clears all cache.
        """
        self.cache_manager.clear(case_id)

    def _analyze_overview_data(self, data: dict) -> dict:
        """Analyze overview data

        Args:
            data: Collected overview data

        Returns:
            Analysis result
        """
        result = {
            "case_id": "",
            "report_info": {},
            "metrics": {},
            "evaluation": {},
        }

        # Extract report info
        report_info = data.get("report_info", {})
        result["report_info"] = report_info
        result["case_id"] = report_info.get("UUID", "")

        # Extract and analyze case info
        case_info = data.get("case_info", {})

        # Extract FPS from summary data
        summry = case_info.get("summry", {})
        fps = summry.get("avg_fps", 0)
        result["metrics"]["fps"] = fps

        # Extract frame time (may need similar handling)
        frame_time = case_info.get("frameTime", {})
        if isinstance(frame_time, dict):
            # If frameTime is also a structured data, try to get average
            # For now, set to 0 if we can't determine the structure
            result["metrics"]["frame_time"] = 0
        else:
            result["metrics"]["frame_time"] = frame_time

        # Evaluate FPS
        fps_status, fps_icon = get_fps_status(fps)
        result["evaluation"]["fps"] = fps_status

        # Extract CPU performance
        cpu_perf = data.get("cpu_performance", {})

        # Handle GC data - may be time series or summary value
        total_gc = cpu_perf.get("TotalGC", 0)
        if isinstance(total_gc, dict):
            # Time series data - use x_data length as GC count approximation
            x_data = total_gc.get("x_data", [])
            result["metrics"]["gc_count"] = len(x_data) if x_data else 0
        else:
            result["metrics"]["gc_count"] = total_gc

        gc_time = cpu_perf.get("gcTime", 0)
        if isinstance(gc_time, dict):
            # Time series data - can't easily aggregate, set to 0 for now
            result["metrics"]["gc_time"] = 0
        else:
            result["metrics"]["gc_time"] = gc_time

        # Extract memory info
        mem_info = data.get("memory_info", {})
        mem_base_data = mem_info.get("base_data", {})

        # Get memory values - use max_reserved_total as reference (in MB)
        total_used = mem_base_data.get("max_reserved_total", 0)
        if isinstance(total_used, dict):
            total_used = 0

        mono_used = mem_base_data.get("max_reserved_mono", 0)
        if isinstance(mono_used, dict):
            mono_used = 0

        result["metrics"]["memory_total"] = total_used
        result["metrics"]["memory_mono"] = mono_used

        # Evaluate memory
        mem_status, mem_icon = get_memory_status(total_used)
        result["evaluation"]["memory"] = mem_status

        # Extract graphic profile
        graphic = data.get("graphic_profile", {})
        graphic_base = graphic.get("base_data", {})

        # Get average values from graphic base_data
        draw_calls = graphic_base.get("avg_DrawCalls", 0)
        triangles = graphic_base.get("avg_Triangles", 0)
        vertices = graphic_base.get("avg_Vertices", 0)

        result["metrics"]["draw_calls"] = draw_calls
        result["metrics"]["triangles"] = triangles
        result["metrics"]["vertices"] = vertices

        # Evaluate rendering
        dc_status, dc_icon = get_drawcall_status(draw_calls)
        result["evaluation"]["rendering"] = dc_status

        return result

    def _analyze_hotspot_data(
        self,
        data: dict,
        top_n: int,
        sort_by: str
    ) -> dict:
        """Analyze hotspot data

        Args:
            data: Collected hotspot data
            top_n: Number of top functions
            sort_by: Sort metric

        Returns:
            Hotspot analysis result
        """
        result = {
            "total_functions": data.get("total_functions", 0),
            "is_big_data": data.get("is_big_data", False),
            "top_functions": [],
            "summary": {}
        }

        # Extract top functions
        fun_top = data.get("fun_top", [])
        if isinstance(fun_top, list):
            result["top_functions"] = fun_top[:top_n]

        # Generate summary statistics
        if fun_top:
            total_self_time = sum(f.get("self_time", 0) for f in fun_top)
            total_total_time = sum(f.get("total_time", 0) for f in fun_top)
            total_calls = sum(f.get("calls", 0) for f in fun_top)

            result["summary"] = {
                "total_self_time": total_self_time,
                "total_total_time": total_total_time,
                "total_calls": total_calls,
                "avg_self_time": total_self_time / len(fun_top) if fun_top else 0,
                "avg_total_time": total_total_time / len(fun_top) if fun_top else 0,
                "avg_calls": total_calls / len(fun_top) if fun_top else 0,
            }

        return result

    def analyze_modules_by_hotspots(
        self,
        case_id: str,
        top_n: int = 10,
        exclude_unity_entry: bool = True,
        use_cache: bool = True
    ) -> dict:
        """Analyze hotspot functions grouped by module

        Args:
            case_id: Case ID
            top_n: Number of top functions per module (default: 10)
            exclude_unity_entry: Whether to exclude Unity common entry point functions (default: True)
            use_cache: Whether to use cache

        Returns:
            Module-based hotspot analysis result
        """
        if self.config.verbose:
            print(f"Analyzing module hotspots for case: {case_id}")
            if exclude_unity_entry:
                print("Excluding Unity entry point functions...")

        # Get function list from get_func_data_summary
        func_list = self.hotspot_collector._get_func_list(case_id, use_cache)

        # Filter by Unity Entry if needed
        if exclude_unity_entry:
            filtered_funcs = []
            for func in func_list:
                if not self.hotspot_collector.is_unity_entry(func):
                    filtered_funcs.append(func)
            func_list = filtered_funcs

        # Group by module using get_func_data_summary data
        module_groups = self.hotspot_collector.group_by_module(func_list, top_n)

        # Prepare result
        result = {
            "case_id": case_id,
            "total_functions": len(func_list),
            "modules": {}
        }

        # Format module data for display
        for module_name, funcs in module_groups.items():
            if funcs:  # Only include modules with functions
                result["modules"][module_name] = {
                    "top_count": len(funcs),
                    "total_self_time": sum(f.get("self_time", 0) for f in funcs),
                    "total_total_time": sum(f.get("total_time", 0) for f in funcs),
                    "total_calls": sum(f.get("calls", 0) for f in funcs),
                    "top_functions": funcs
                }

        return result

    def compare_reports(
        self,
        case_id_a: str,
        case_id_b: str,
        use_cache: bool = True,
        hotspot_top_n: int = 100
    ) -> dict:
        """Compare two performance reports with AI-ready preprocessing

        This method performs comprehensive comparison analysis including:
        - Key metrics comparison (FPS, memory, DrawCalls, etc.)
        - Function-level comparison (degraded, improved, new, lost hotspots)
        - Memory comparison
        - Rendering performance comparison
        - Statistical analysis (deltas, percentages, trends)
        - Auto-generated recommendations

        Args:
            case_id_a: First case ID (baseline)
            case_id_b: Second case ID (comparison target)
            use_cache: Whether to use cache
            hotspot_top_n: Number of top hotspot functions to compare

        Returns:
            Enhanced comparison analysis result with AI-ready structure
        """
        if self.config.verbose:
            print(f"Comparing reports: {case_id_a} vs {case_id_b}")

        # Step 1: Collect data for both cases
        if self.config.verbose:
            print("Collecting data for both reports...")

        collector = ComparisonCollector(
            self.client,
            self.cache_manager,
            self.config
        )
        raw_data = collector.collect_comparison_data(
            case_id_a, case_id_b,
            use_cache=use_cache,
            hotspot_top_n=hotspot_top_n
        )

        # Step 2: Python preprocessing with ComparisonReporter
        # Generate AI-ready report data
        if self.config.verbose:
            print("Performing statistical analysis and preprocessing...")

        reporter = ComparisonReporter(raw_data)
        ai_ready_data = reporter.generate_report_data()

        if self.config.verbose:
            print(f"Preprocessing complete: {len(ai_ready_data['recommendations'])} recommendations generated")

        return ai_ready_data
