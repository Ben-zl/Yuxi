"""Unity Profiler Analyzer - Hotspot Collector"""

from typing import Optional, List

# Handle both relative and absolute imports
try:
    from .base_collector import BaseCollector
    from ..utils.minio_downloader import MinIODownloader
except (ImportError, ValueError):
    # Absolute imports for direct execution
    from collectors.base_collector import BaseCollector
    from utils.minio_downloader import MinIODownloader


class HotspotCollector(BaseCollector):
    """Collector for hotspot function data"""

    # Big data threshold for function list
    BIG_DATA_THRESHOLD = 1000

    # Unity common entry point functions to filter out
    # These are typically wrapper functions with low self_time but high total_time
    # We use partial matching (startswith) to handle names like "ScriptableRenderer.Execute: ForwardRenderer"
    UNITY_ENTRY_PATTERNS = frozenset([
        # Profiler functions (Unity Profiler overhead)
        "Profiler.FlushMemoryCounters",
        "Profiler.",
        "UnityEngine.Profiling.Profiler.",

        # Main loop entry points
        "PlayerLoop",
        "FixedUpdate",
        "Update",
        "LateUpdate",
        "BehaviourUpdate",
        "FixedBehaviourUpdate",
        "LateBehaviourUpdate",
        "Prefab.Update",

        # Rendering entry points (with : separator for variants)
        "ScriptableRenderer.Execute",
        "ForwardRenderer",
        "UniversalRenderer",

        # Physics entry points
        "Physics.Update",
        "Physics.SyncTransforms",
        "Physics.Processing",

        # Script run entry points
        "RunBehaviourUpdate",
        "RunFixedBehaviourUpdate",
        "RunLateBehaviourUpdate",

        # UI entry points
        "UIEvents.Update",
        "UnityEngine.UI.UIEventSystem.Update",

        # Animation entry points
        "Animation.Update",
        "Animation.Evaluate",

        # Common Unity lifecycle markers
        "Update.ScriptRunBehaviourUpdate",
        "FixedUpdate.ScriptRunFixedBehaviourUpdate",
        "LateUpdate.ScriptRunLateBehaviourUpdate",

        # Pre/Post update markers
        "PreLateUpdate",
        "PostLateUpdate",
        "PreUpdate",
        "PostUpdate",

        # Job system entry points
        "JobHelper.ScheduleChunk",
        "JobHelper.ScheduleParallel",

        # Script run entry
        "ScriptRunBehaviourUpdate",
        "ScriptRunLateBehaviourUpdate",

        # End frame markers
        "EndFrame",
        "EndRendering",
    ])

    # Module patterns for grouping functions
    # These patterns are used to categorize functions into different modules
    MODULE_PATTERNS = {
        "Rendering": [
            # Rendering related
            "Draw",
            "Render",
            "Camera",
            "Light",
            "Shadow",
            "Shader",
            "Mesh",
            "Material",
            "Texture",
            "Graphics",
            "GPU",
            "DXGI",
            "OpenGL",
            "Vulkan",
            "DirectX",
            "ScriptableRenderer",
            "ForwardRenderer",
            "UniversalRenderer",
            "LightweightRendering",
            "BatchRenderer",
            "GUI.",
            "UGUI.",
            "Canvas",
            "UIEvents.",
            "Particle",
            "Trail",
            "Line",
            "Renderer",
        ],
        "Script": [
            # Script/Mono related
            "Mono.",
            "Script",
            "Behaviour",
            "Coroutine",
            "Yield",
            "Invoke",
            "Assembly-",
            "AssetBundle.",
            "ResourceManager.",
            "SceneManager.",
            "UnityEngine.",
            "Object.",
            "Component.",
            "GameObject.",
            "Transform.",
        ],
        "Physics": [
            # Physics related
            "Physics.",
            "Rigidbody",
            "Collider",
            "Joint",
            "FixedUpdate",
            "PxScene",
            "PhysX",
            "Collision",
            "Trigger",
            "Raycast",
            "Overlap",
            "Sweep",
        ],
        "Animation": [
            # Animation related
            "Animation.",
            "Animator",
            "State",
            "Clip",
            "BlendTree",
            "Avatar",
            "IK",
            "Play",
            "Sample",
            "StateMachine",
        ],
        "UI": [
            # UI specific (not covered by Rendering)
            "EventSystem",
            "GraphicRaycaster",
            "Pointer",
            "Input",
            "Scroll",
            "Layout",
            "CanvasGroup",
            "Graphic",
            "Image",
            "Text",
            "Button",
            "Toggle",
            "Slider",
            "Dropdown",
        ]
    }

    def __init__(self, client, cache_manager, config=None):
        """Initialize hotspot collector

        Args:
            client: UboxProfilerClient instance
            cache_manager: CacheManager instance
            config: Optional Config object
        """
        super().__init__(client, cache_manager, config)
        self.minio_downloader = MinIODownloader(
            timeout=30,
            max_retries=3
        )

    def collect(
        self,
        case_id: str,
        use_cache: bool = True,
        top_n: int = 100,
        sort_by: str = "self_time",
        min_calls: int = 0,
        exclude_unity_entry: bool = True
    ) -> dict:
        """Collect hotspot function data

        Args:
            case_id: Case ID
            use_cache: Whether to use cache
            top_n: Number of top functions to retrieve
            sort_by: Sort metric (self_time/total_time/valid_frame_avg/high_cost_frames)
            min_calls: Minimum call count threshold (for filtering valid frames, default: 0)
            exclude_unity_entry: Whether to exclude Unity common entry point functions (default: True)

        Returns:
            Hotspot data containing:
            - fun_top: Top functions by sort metric
            - func_list: Complete flattened function list
            - summary: Statistical summary
            - is_big_data: Whether data exceeds threshold
        """
        # Create dimension-specific cache key to isolate different sorting dimensions
        # This prevents cache contamination where self_time data is returned for total_time requests
        cache_key = f"{case_id}_{sort_by}"

        # Try to load from cache
        if use_cache:
            cached = self._load_from_cache(cache_key)
            if cached is not None:
                return cached

        # Fetch from API using isolated dimension-specific logic
        # Each dimension has its own data processing pipeline to avoid cross-contamination
        fun_top = self._collect_by_dimension(case_id, sort_by, top_n, min_calls, exclude_unity_entry, use_cache)

        # Get complete function list for reference (not dimension-specific)
        func_list = self._get_func_list(case_id, use_cache)
        func_list = self._enhance_with_fun_top_data(case_id, func_list, use_cache)

        data = {
            "fun_top": fun_top,
            "func_list": func_list,
            "total_functions": len(func_list),
            "is_big_data": len(func_list) >= self.BIG_DATA_THRESHOLD,
        }

        # Save to cache using dimension-specific key
        self._save_to_cache(cache_key, data)

        return data

    def _fetch_from_api(self, case_id: str) -> dict:
        """Fetch hotspot data from API"""
        return self.collect(case_id, use_cache=False)

    def _collect_by_dimension(
        self,
        case_id: str,
        sort_by: str,
        top_n: int,
        min_calls: int,
        exclude_unity_entry: bool,
        use_cache: bool
    ) -> List[dict]:
        """Collect hotspot data using dimension-specific logic

        This method provides COMPLETE ISOLATION between different sorting dimensions.
        Each dimension has its own data pipeline, merge strategy, and sorting logic.

        Args:
            case_id: Case ID
            sort_by: Sort metric (self_time/total_time/valid_frame_avg/high_cost_frames)
            top_n: Number of top functions
            min_calls: Minimum call count threshold
            exclude_unity_entry: Whether to exclude Unity entry functions
            use_cache: Whether to use cache

        Returns:
            Top N functions for the specified dimension
        """
        # OPTIMIZATION: high_cost_frames dimension can skip get_func_data_summary (Step 1)
        # It only needs data from get_fun_top API, which reduces API calls and memory usage
        if sort_by == "high_cost_frames":
            return self._collect_high_cost_frames_dimension_optimized(
                case_id, top_n, min_calls, exclude_unity_entry, use_cache
            )

        # Step 1: Get raw function list from get_func_data_summary
        # This provides: self_time, total_time, calls, avg_total_time, max_total_time, gt_10ms, etc.
        func_list = self._get_func_list(case_id, use_cache)

        # Step 2: Enhance with fun_top data (contains valid_frame_count, totaltime_avg, high_frame, etc.)
        # This adds: valid_frame_count, totaltime_avg, high_frame, high_selftime, selftime_avg, etc.
        func_list = self._enhance_with_fun_top_data(case_id, func_list, use_cache)

        # Step 3: Process using dimension-specific logic
        if sort_by == "self_time":
            return self._collect_self_time_dimension(func_list, top_n, min_calls, exclude_unity_entry)
        elif sort_by == "total_time":
            return self._collect_total_time_dimension(func_list, top_n, min_calls, exclude_unity_entry)
        elif sort_by == "valid_frame_avg":
            return self._collect_valid_frame_avg_dimension(func_list, top_n, min_calls, exclude_unity_entry)
        elif sort_by == "high_cost_frames":
            # Fallback to original implementation if optimized version fails
            return self._collect_high_cost_frames_dimension(func_list, top_n, min_calls, exclude_unity_entry)
        elif sort_by == "calls":
            return self._collect_calls_dimension(func_list, top_n, min_calls, exclude_unity_entry)
        elif sort_by == "impact_score":
            return self._collect_impact_score_dimension(func_list, top_n, min_calls, exclude_unity_entry)
        elif sort_by == "avg_time":
            return self._collect_avg_time_dimension(func_list, top_n, min_calls, exclude_unity_entry)
        elif sort_by == "high_cost_frame_ratio":
            return self._collect_high_cost_frame_ratio_dimension(func_list, top_n, min_calls, exclude_unity_entry)
        else:
            # Fallback to default
            return self._collect_self_time_dimension(func_list, top_n, min_calls, exclude_unity_entry)

    def _collect_self_time_dimension(
        self,
        func_list: List[dict],
        top_n: int,
        min_calls: int,
        exclude_unity_entry: bool
    ) -> List[dict]:
        """Collect top functions by self_time dimension

        Dimension-specific logic:
        - Data source: get_func_data_summary API
        - Merge strategy: SUM self_time, MAX total_time
        - Sorting: By self_time descending
        - Filters: min_calls, exclude_unity_entry

        Args:
            func_list: Raw function list
            top_n: Number of top functions to return
            min_calls: Minimum call count threshold
            exclude_unity_entry: Whether to exclude Unity entry functions

        Returns:
            Top N functions by self_time
        """
        # Merge using self_time-specific strategy
        merged_funcs = self._merge_for_self_time(func_list)

        # Apply filters
        filtered_funcs = merged_funcs
        if min_calls > 0:
            filtered_funcs = [f for f in filtered_funcs if f.get("calls", 0) >= min_calls]
        if exclude_unity_entry:
            filtered_funcs = [f for f in filtered_funcs if not self.is_unity_entry(f)]

        # Sort by self_time descending
        sorted_funcs = sorted(
            filtered_funcs,
            key=lambda f: f.get("self_time", 0),
            reverse=True
        )

        return sorted_funcs[:top_n]

    def _collect_total_time_dimension(
        self,
        func_list: List[dict],
        top_n: int,
        min_calls: int,
        exclude_unity_entry: bool
    ) -> List[dict]:
        """Collect top functions by total_time dimension

        Dimension-specific logic:
        - Data source: get_func_data_summary API
        - Merge strategy: SUM self_time, MAX total_time
        - Sorting: By total_time descending
        - Filters: min_calls, exclude_unity_entry

        Args:
            func_list: Raw function list
            top_n: Number of top functions to return
            min_calls: Minimum call count threshold
            exclude_unity_entry: Whether to exclude Unity entry functions

        Returns:
            Top N functions by total_time
        """
        # Merge using total_time-specific strategy
        merged_funcs = self._merge_for_total_time(func_list)

        # Apply filters
        filtered_funcs = merged_funcs
        if min_calls > 0:
            filtered_funcs = [f for f in filtered_funcs if f.get("calls", 0) >= min_calls]
        if exclude_unity_entry:
            filtered_funcs = [f for f in filtered_funcs if not self.is_unity_entry(f)]

        # Sort by total_time descending
        sorted_funcs = sorted(
            filtered_funcs,
            key=lambda f: f.get("total_time", 0),
            reverse=True
        )

        return sorted_funcs[:top_n]

    def _collect_valid_frame_avg_dimension(
        self,
        func_list: List[dict],
        top_n: int,
        min_calls: int,
        exclude_unity_entry: bool
    ) -> List[dict]:
        """Collect top functions by valid_frame_avg dimension

        Dimension-specific logic:
        - Data source: fun_top API (selftime_avg field)
        - Merge strategy: MAX for valid_frame_count, MAX for selftime_avg
        - Sorting: Two-step (top 40% by valid_frame_count, then by selftime_avg)
        - Filters: min_calls, exclude_unity_entry

        Args:
            func_list: Raw function list
            top_n: Number of top functions to return
            min_calls: Minimum call count threshold
            exclude_unity_entry: Whether to exclude Unity entry functions

        Returns:
            Top N functions by valid_frame_avg
        """
        # Merge using valid_frame_avg-specific strategy (MAX for valid_frame_count)
        merged_funcs = self._merge_for_valid_frame_avg(func_list)

        # Apply filters
        filtered_funcs = merged_funcs
        if min_calls > 0:
            filtered_funcs = [f for f in filtered_funcs if f.get("calls", 0) >= min_calls]
        if exclude_unity_entry:
            filtered_funcs = [f for f in filtered_funcs if not self.is_unity_entry(f)]

        # Only analyze functions with valid frame records
        funcs_with_valid_frames = [f for f in filtered_funcs if f.get("valid_frame_count", 0) > 0]
        if not funcs_with_valid_frames:
            return []

        # Step 1: Sort by valid_frame_count descending
        sorted_by_valid_count = sorted(
            funcs_with_valid_frames,
            key=lambda f: f.get("valid_frame_count", 0),
            reverse=True
        )

        # Step 2: Take top 40% (most frequently called)
        top_40_percent_count = max(1, int(len(sorted_by_valid_count) * 0.4))
        top_40_percent = sorted_by_valid_count[:top_40_percent_count]

        # Step 3: In top 40%, sort by selftime_avg descending
        sorted_funcs = sorted(
            top_40_percent,
            key=lambda f: f.get("selftime_avg", 0),
            reverse=True
        )

        return sorted_funcs[:top_n]

    def _collect_high_cost_frames_dimension_optimized(
        self,
        case_id: str,
        top_n: int,
        min_calls: int,
        exclude_unity_entry: bool,
        use_cache: bool
    ) -> List[dict]:
        """Collect top functions by high_cost_frames dimension (OPTIMIZED VERSION)

        OPTIMIZATION: Skips get_func_data_summary (Step 1) and uses only get_fun_top API.
        This reduces API calls, MinIO downloads, and memory usage.

        Dimension-specific logic:
        - Data source: get_fun_top API ONLY (high_frame, high_selftime, high_frame_total, high_totaltime)
        - No merge strategy needed (fun_top data is already unique)
        - Sorting: By high_frame descending
        - Filters: exclude_unity_entry (min_calls not supported as fun_top lacks calls field)

        Args:
            case_id: Case ID
            top_n: Number of top functions to return
            min_calls: Minimum call count threshold (NOT SUPPORTED in optimized mode)
            exclude_unity_entry: Whether to exclude Unity entry functions
            use_cache: Whether to use cache

        Returns:
            Top N functions by high_cost_frames
        """
        try:
            # Get fun_top data from API directly
            fun_top_result = self.client.report.get_fun_top(case_id, 0, 0)

            if fun_top_result.get("code") != 0:
                if self.config and self.config.debug:
                    print(f"Error: get_fun_top API returned code {fun_top_result.get('code')}")
                return []

            fun_top_data = fun_top_result.get("data")
            if not isinstance(fun_top_data, list) or len(fun_top_data) == 0:
                return []

            # Build function list directly from fun_top data
            func_list = []
            for item in fun_top_data:
                func = {
                    "name": item.get("fun_name", ""),
                    "high_frame": item.get("high_frame", 0),
                    "high_selftime": item.get("high_selftime", 0),
                    "high_frame_total": item.get("high_frame_total", 0),
                    "high_totaltime": item.get("high_totaltime", 0),
                    "valid_frame_count": item.get("valid_frame_count", 0),
                    "selftime_avg": item.get("selftime_avg", 0),
                    "selftime_max": item.get("selftime_max", 0),
                    "totaltime_avg": item.get("totaltime_avg", 0),
                    "totaltime_max": item.get("totaltime_max", 0),
                }
                func_list.append(func)

            # Apply filters
            filtered_funcs = func_list

            # Note: min_calls filter is not supported in optimized mode
            # because fun_top API doesn't provide calls field
            if min_calls > 0:
                if self.config and self.config.debug:
                    print(f"Warning: min_calls filter ({min_calls}) is not supported in optimized high_cost_frames mode")

            if exclude_unity_entry:
                filtered_funcs = [f for f in filtered_funcs if not self.is_unity_entry(f)]

            # Sort by high_frame descending
            sorted_funcs = sorted(
                filtered_funcs,
                key=lambda f: f.get("high_frame", 0),
                reverse=True
            )

            return sorted_funcs[:top_n]

        except Exception as e:
            if self.config and self.config.debug:
                print(f"Error in optimized high_cost_frames collection: {e}")
            # Return empty list on error
            return []

    def _collect_high_cost_frames_dimension(
        self,
        func_list: List[dict],
        top_n: int,
        min_calls: int,
        exclude_unity_entry: bool
    ) -> List[dict]:
        """Collect top functions by high_cost_frames dimension (ORIGINAL IMPLEMENTATION)

        NOTE: This is the original implementation that uses get_func_data_summary (Step 1).
        The optimized version (_collect_high_cost_frames_dimension_optimized) is preferred
        and is used by default in _collect_by_dimension.

        Dimension-specific logic:
        - Data source: get_func_data_summary (Step 1) + fun_top API (Step 2)
        - Merge strategy: MAX for high_frame and high_selftime (CRITICAL: avoid double-counting)
        - Sorting: By high_frame descending (v3.3: simplified, no secondary sort)
        - Filters: min_calls, exclude_unity_entry

        Args:
            func_list: Raw function list
            top_n: Number of top functions to return
            min_calls: Minimum call count threshold
            exclude_unity_entry: Whether to exclude Unity entry functions

        Returns:
            Top N functions by high_cost_frames
        """
        # Merge using high_cost_frames-specific strategy (MAX for high_frame)
        merged_funcs = self._merge_for_high_cost_frames(func_list)

        # Apply filters
        filtered_funcs = merged_funcs
        if min_calls > 0:
            filtered_funcs = [f for f in filtered_funcs if f.get("calls", 0) >= min_calls]
        if exclude_unity_entry:
            filtered_funcs = [f for f in filtered_funcs if not self.is_unity_entry(f)]

        # Sort by high_frame descending (v3.3: simplified, no secondary sort)
        sorted_funcs = sorted(
            filtered_funcs,
            key=lambda f: f.get("high_frame", 0),
            reverse=True
        )

        return sorted_funcs[:top_n]

    def _collect_calls_dimension(
        self,
        func_list: List[dict],
        top_n: int,
        min_calls: int,
        exclude_unity_entry: bool
    ) -> List[dict]:
        """Collect top functions by calls dimension

        Dimension-specific logic:
        - Data source: get_func_data_summary API
        - Merge strategy: SUM calls
        - Sorting: By calls descending
        - Filters: min_calls, exclude_unity_entry

        Args:
            func_list: Raw function list
            top_n: Number of top functions to return
            min_calls: Minimum call count threshold
            exclude_unity_entry: Whether to exclude Unity entry functions

        Returns:
            Top N functions by calls
        """
        # Merge using default strategy (SUM calls)
        merged_funcs = self._merge_functions_by_name(func_list)

        # Apply filters
        filtered_funcs = merged_funcs
        if min_calls > 0:
            filtered_funcs = [f for f in filtered_funcs if f.get("calls", 0) >= min_calls]
        if exclude_unity_entry:
            filtered_funcs = [f for f in filtered_funcs if not self.is_unity_entry(f)]

        # Sort by calls descending
        sorted_funcs = sorted(
            filtered_funcs,
            key=lambda f: f.get("calls", 0),
            reverse=True
        )

        return sorted_funcs[:top_n]

    def _collect_impact_score_dimension(
        self,
        func_list: List[dict],
        top_n: int,
        min_calls: int,
        exclude_unity_entry: bool
    ) -> List[dict]:
        """Collect top functions by impact_score dimension

        Dimension-specific logic:
        - Data source: get_func_data_summary API
        - Formula: avg_total_time * log(calls + 1)
        - Merge strategy: Default
        - Sorting: By impact_score descending
        - Filters: min_calls, exclude_unity_entry

        Args:
            func_list: Raw function list
            top_n: Number of top functions to return
            min_calls: Minimum call count threshold
            exclude_unity_entry: Whether to exclude Unity entry functions

        Returns:
            Top N functions by impact_score
        """
        import math

        # Merge using default strategy
        merged_funcs = self._merge_functions_by_name(func_list)

        # Apply filters
        filtered_funcs = merged_funcs
        if min_calls > 0:
            filtered_funcs = [f for f in filtered_funcs if f.get("calls", 0) >= min_calls]
        if exclude_unity_entry:
            filtered_funcs = [f for f in filtered_funcs if not self.is_unity_entry(f)]

        # Calculate impact score and sort
        def get_impact_score(f):
            calls = f.get("calls", 0)
            avg_time = f.get("avg_total_time", f.get("total_time", 0) / max(calls, 1))
            weight = math.log(calls + 1) if calls > 0 else 0
            return avg_time * weight

        sorted_funcs = sorted(
            filtered_funcs,
            key=get_impact_score,
            reverse=True
        )

        return sorted_funcs[:top_n]

    def _collect_avg_time_dimension(
        self,
        func_list: List[dict],
        top_n: int,
        min_calls: int,
        exclude_unity_entry: bool
    ) -> List[dict]:
        """Collect top functions by avg_time dimension

        Dimension-specific logic:
        - Data source: get_func_data_summary API
        - Merge strategy: Default
        - Sorting: By avg_total_time descending
        - Filters: min_calls, exclude_unity_entry

        Args:
            func_list: Raw function list
            top_n: Number of top functions to return
            min_calls: Minimum call count threshold
            exclude_unity_entry: Whether to exclude Unity entry functions

        Returns:
            Top N functions by avg_time
        """
        # Merge using default strategy
        merged_funcs = self._merge_functions_by_name(func_list)

        # Apply filters
        filtered_funcs = merged_funcs
        if min_calls > 0:
            filtered_funcs = [f for f in filtered_funcs if f.get("calls", 0) >= min_calls]
        if exclude_unity_entry:
            filtered_funcs = [f for f in filtered_funcs if not self.is_unity_entry(f)]

        # Sort by avg_total_time descending
        sorted_funcs = sorted(
            filtered_funcs,
            key=lambda f: f.get("avg_total_time", f.get("total_time", 0) / max(f.get("calls", 1), 1)),
            reverse=True
        )

        return sorted_funcs[:top_n]

    def _collect_high_cost_frame_ratio_dimension(
        self,
        func_list: List[dict],
        top_n: int,
        min_calls: int,
        exclude_unity_entry: bool
    ) -> List[dict]:
        """Collect top functions by high_cost_frame_ratio dimension

        Dimension-specific logic:
        - Data source: get_func_data_summary API (gt_10ms field)
        - Formula: gt_10ms / calls
        - Merge strategy: Default
        - Sorting: By ratio descending
        - Filters: min_calls, exclude_unity_entry, only functions with gt_10ms > 0

        Args:
            func_list: Raw function list
            top_n: Number of top functions to return
            min_calls: Minimum call count threshold
            exclude_unity_entry: Whether to exclude Unity entry functions

        Returns:
            Top N functions by high_cost_frame_ratio
        """
        # Merge using default strategy
        merged_funcs = self._merge_functions_by_name(func_list)

        # Apply filters
        filtered_funcs = merged_funcs
        if min_calls > 0:
            filtered_funcs = [f for f in filtered_funcs if f.get("calls", 0) >= min_calls]
        if exclude_unity_entry:
            filtered_funcs = [f for f in filtered_funcs if not self.is_unity_entry(f)]

        # Only analyze functions with high-cost frame records
        filtered_funcs = [f for f in filtered_funcs if f.get("gt_10ms", 0) > 0]

        # Calculate ratio and sort
        def get_high_cost_ratio(f):
            calls = f.get("calls", 0)
            gt_10ms = f.get("gt_10ms", 0)
            return (gt_10ms / calls) if calls > 0 else 0

        sorted_funcs = sorted(
            filtered_funcs,
            key=get_high_cost_ratio,
            reverse=True
        )

        return sorted_funcs[:top_n]

    def _merge_for_self_time(self, func_list: List[dict]) -> List[dict]:
        """Merge functions for self_time dimension

        Uses default merge strategy: SUM self_time, MAX total_time

        Args:
            func_list: Function list (may contain duplicate function names)

        Returns:
            Merged function list with unique function names
        """
        return self._merge_functions_by_name(func_list)

    def _merge_for_total_time(self, func_list: List[dict]) -> List[dict]:
        """Merge functions for total_time dimension

        Uses default merge strategy: SUM self_time, MAX total_time

        Args:
            func_list: Function list (may contain duplicate function names)

        Returns:
            Merged function list with unique function names
        """
        return self._merge_functions_by_name(func_list)

    def _enhance_with_fun_top_data(
        self,
        case_id: str,
        func_list: List[dict],
        use_cache: bool = True
    ) -> List[dict]:
        """Enhance function list with fun_top data (valid_frame_count, totaltime_avg, etc.)

        Args:
            case_id: Case ID
            func_list: Function list from func_data_summary
            use_cache: Whether to use cache

        Returns:
            Enhanced function list with additional fields
        """
        try:
            # Get fun_top data from API
            fun_top_result = self.client.report.get_fun_top(case_id, 0, 0)

            if fun_top_result.get("code") != 0:
                return func_list

            fun_top_data = fun_top_result.get("data")
            if not isinstance(fun_top_data, list) or len(fun_top_data) == 0:
                return func_list

            # Create a mapping from function name to fun_top data
            fun_top_map = {}
            for item in fun_top_data:
                func_name = item.get("fun_name", "")
                if func_name:
                    fun_top_map[func_name] = {
                        "valid_frame_count": item.get("valid_frame_count", 0),
                        "totaltime_avg": item.get("totaltime_avg", 0),
                        "high_frame": item.get("high_frame", 0),
                        "high_selftime": item.get("high_selftime", 0),
                        "high_frame_total": item.get("high_frame_total", 0),
                        "high_totaltime": item.get("high_totaltime", 0),
                        "selftime_avg": item.get("selftime_avg", 0),
                        "selftime_max": item.get("selftime_max", 0),
                        "totaltime_max": item.get("totaltime_max", 0),
                    }

            # Enhance func_list with fun_top data
            enhanced_list = []
            for func in func_list:
                func_name = func.get("name", "")
                enhanced_func = func.copy()

                # Add fun_top fields if available
                if func_name in fun_top_map:
                    enhanced_func.update(fun_top_map[func_name])
                else:
                    # Set default values if not found
                    enhanced_func.update({
                        "valid_frame_count": 0,
                        "totaltime_avg": 0,
                        "high_frame": 0,
                        "high_selftime": 0,
                        "high_frame_total": 0,
                        "high_totaltime": 0,
                        "selftime_avg": 0,
                        "selftime_max": 0,
                        "totaltime_max": 0,
                    })

                enhanced_list.append(enhanced_func)

            return enhanced_list

        except Exception as e:
            if self.config and self.config.debug:
                print(f"Error enhancing with fun_top data: {e}")
            # Return original list if enhancement fails
            return func_list

    def _get_func_list(
        self,
        case_id: str,
        use_cache: bool = True
    ) -> List[dict]:
        """Get complete function list from MinIO

        Args:
            case_id: Case ID
            use_cache: Whether to use cache for downloaded data (ignored for MinIO downloads)

        Returns:
            Flattened list of all functions

        Note:
            MinIO downloads are always fetched fresh to avoid stale cache issues.
            The use_cache parameter is only used for meta data, not the actual MinIO content.
        """
        try:
            # Get MinIO URL from API
            response = self.client.cpu.get_func_data_summary(case_id, "all")

            if response.get("code") != 0:
                return []

            url = response.get("data", "")
            if not url or not isinstance(url, str):
                return []

            # Download and parse data from MinIO
            # IMPORTANT: Always use_cache=False for MinIO downloads to get fresh data
            # This prevents stale cache issues where function data becomes empty
            cache_dir = self.cache.cache_dir  # Only use for meta cache
            data = self.minio_downloader.download_json(
                url,
                use_cache=False,  # Always fetch fresh MinIO data
                cache_dir=cache_dir
            )

            # Flatten the function tree
            func_list = self._flatten_function_tree(data)

            return func_list

        except Exception as e:
            if self.config.debug:
                print(f"Error getting func list: {e}")
            return []

    def _flatten_function_tree(
        self,
        data: List[dict],
        parent: Optional[str] = None,
        level: int = 0
    ) -> List[dict]:
        """Flatten hierarchical function tree into a list

        Args:
            data: Function tree (root level is list with one item)
            parent: Parent function name
            level: Depth level in tree

        Returns:
            Flattened list of functions
        """
        result = []

        # Handle the root level (list with one item containing PlayerLoop)
        if isinstance(data, list) and len(data) > 0:
            root = data[0]
            if isinstance(root, dict):
                self._process_function_node(
                    root,
                    parent=None,
                    level=0,
                    result=result
                )

        return result

    def _process_function_node(
        self,
        node: dict,
        parent: Optional[str],
        level: int,
        result: List[dict]
    ) -> None:
        """Process a function node and add to result list

        Args:
            node: Function node dict
            parent: Parent function name
            level: Depth level
            result: Result list to append to
        """
        # Extract function info
        func_info = {
            "name": node.get("name", "N/A"),
            "self_time": node.get("self_time", 0),
            "total_time": node.get("total_time", 0),
            "avg_total_time": node.get("avg_total_time", 0),
            "max_total_time": node.get("max_total_time", 0),
            "calls": node.get("calls", 0),
            "calls_pre_frame": node.get("calls_pre_frame", 0),
            "total_time_percentage": node.get("total_time_percentage", 0),
            "gc_mem": node.get("gc_mem", 0),
            "gt_10ms": node.get("gt_10ms", 0),
            "parent": parent,
            "level": level
        }

        result.append(func_info)

        # Process children recursively
        children = node.get("children", [])
        if children:
            for child in children:
                self._process_function_node(
                    child,
                    parent=func_info["name"],
                    level=level + 1,
                    result=result
                )

    def _merge_functions_by_name(self, func_list: List[dict]) -> List[dict]:
        """Merge functions with the same name

        Args:
            func_list: Function list (may contain duplicate function names)

        Returns:
            Merged function list with unique function names
        """
        if not func_list:
            return []

        # Group by function name
        merged = {}

        for func in func_list:
            func_name = func.get("name", "")
            if not func_name:
                continue

            if func_name not in merged:
                # First occurrence, copy the function
                merged[func_name] = func.copy()
                # Reset fields that need accumulation
                merged[func_name]["_occurrences"] = 1
            else:
                # Accumulate metrics
                merged_func = merged[func_name]

                # Accumulate self_time (sum of all self times)
                merged_func["self_time"] = merged_func.get("self_time", 0) + func.get("self_time", 0)

                # Take max total_time (total time represents the call chain, max is more meaningful)
                merged_func["total_time"] = max(
                    merged_func.get("total_time", 0),
                    func.get("total_time", 0)
                )

                # Accumulate calls
                merged_func["calls"] = merged_func.get("calls", 0) + func.get("calls", 0)

                # Take max avg_total_time
                merged_func["avg_total_time"] = max(
                    merged_func.get("avg_total_time", 0),
                    func.get("avg_total_time", 0)
                )

                # Take max max_total_time
                merged_func["max_total_time"] = max(
                    merged_func.get("max_total_time", 0),
                    func.get("max_total_time", 0)
                )

                # Accumulate gc_mem
                merged_func["gc_mem"] = merged_func.get("gc_mem", 0) + func.get("gc_mem", 0)

                # Accumulate gt_10ms
                merged_func["gt_10ms"] = merged_func.get("gt_10ms", 0) + func.get("gt_10ms", 0)

                # Take max total_time_percentage
                merged_func["total_time_percentage"] = max(
                    merged_func.get("total_time_percentage", 0),
                    func.get("total_time_percentage", 0)
                )

                # For fun_top API enhanced fields
                # Accumulate valid_frame_count
                merged_func["valid_frame_count"] = merged_func.get("valid_frame_count", 0) + func.get("valid_frame_count", 0)

                # Weighted average for totaltime_avg and selftime_avg
                valid_count_1 = merged_func.get("valid_frame_count", 0) - func.get("valid_frame_count", 0)
                valid_count_2 = func.get("valid_frame_count", 0)

                if valid_count_1 + valid_count_2 > 0:
                    old_totaltime_avg = merged_func.get("totaltime_avg", 0)
                    new_totaltime_avg = func.get("totaltime_avg", 0)
                    merged_func["totaltime_avg"] = (
                        (old_totaltime_avg * valid_count_1 + new_totaltime_avg * valid_count_2) /
                        (valid_count_1 + valid_count_2)
                    )

                    old_selftime_avg = merged_func.get("selftime_avg", 0)
                    new_selftime_avg = func.get("selftime_avg", 0)
                    merged_func["selftime_avg"] = (
                        (old_selftime_avg * valid_count_1 + new_selftime_avg * valid_count_2) /
                        (valid_count_1 + valid_count_2)
                    )

                # Take max totaltime_max and selftime_max
                merged_func["totaltime_max"] = max(
                    merged_func.get("totaltime_max", 0),
                    func.get("totaltime_max", 0)
                )
                merged_func["selftime_max"] = max(
                    merged_func.get("selftime_max", 0),
                    func.get("selftime_max", 0)
                )

                # Accumulate high_frame
                merged_func["high_frame"] = merged_func.get("high_frame", 0) + func.get("high_frame", 0)

                # Accumulate high_selftime
                merged_func["high_selftime"] = merged_func.get("high_selftime", 0) + func.get("high_selftime", 0)

                # Take max high_frame_total
                merged_func["high_frame_total"] = max(
                    merged_func.get("high_frame_total", 0),
                    func.get("high_frame_total", 0)
                )

                # Take max high_totaltime
                merged_func["high_totaltime"] = max(
                    merged_func.get("high_totaltime", 0),
                    func.get("high_totaltime", 0)
                )

                merged_func["_occurrences"] += 1

        # Remove internal field and return as list
        return [func for func in merged.values()]

    def _merge_for_valid_frame_avg(self, func_list: List[dict]) -> List[dict]:
        """Merge functions for valid_frame_avg sorting (use MAX for valid_frame_count)

        For valid_frame_avg sorting, valid_frame_count should NOT be accumulated
        because it represents the number of unique frames that called this function.
        Taking MAX avoids double counting across different call paths.

        Args:
            func_list: Function list (may contain duplicate function names)

        Returns:
            Merged function list with unique function names
        """
        if not func_list:
            return []

        # Group by function name
        merged = {}

        for func in func_list:
            func_name = func.get("name", "")
            if not func_name:
                continue

            if func_name not in merged:
                # First occurrence, copy the function
                merged[func_name] = func.copy()
                # Reset fields that need accumulation
                merged[func_name]["_occurrences"] = 1
            else:
                # Accumulate metrics
                merged_func = merged[func_name]

                # Accumulate self_time (sum of all self times)
                merged_func["self_time"] = merged_func.get("self_time", 0) + func.get("self_time", 0)

                # Take max total_time (total time represents the call chain, max is more meaningful)
                merged_func["total_time"] = max(
                    merged_func.get("total_time", 0),
                    func.get("total_time", 0)
                )

                # Accumulate calls
                merged_func["calls"] = merged_func.get("calls", 0) + func.get("calls", 0)

                # Take max avg_total_time
                merged_func["avg_total_time"] = max(
                    merged_func.get("avg_total_time", 0),
                    func.get("avg_total_time", 0)
                )

                # Take max max_total_time
                merged_func["max_total_time"] = max(
                    merged_func.get("max_total_time", 0),
                    func.get("max_total_time", 0)
                )

                # Accumulate gc_mem
                merged_func["gc_mem"] = merged_func.get("gc_mem", 0) + func.get("gc_mem", 0)

                # Accumulate gt_10ms
                merged_func["gt_10ms"] = merged_func.get("gt_10ms", 0) + func.get("gt_10ms", 0)

                # Take max total_time_percentage
                merged_func["total_time_percentage"] = max(
                    merged_func.get("total_time_percentage", 0),
                    func.get("total_time_percentage", 0)
                )

                # For fun_top API enhanced fields
                # Take MAX for valid_frame_count (KEY FIX: avoid double counting)
                merged_func["valid_frame_count"] = max(
                    merged_func.get("valid_frame_count", 0),
                    func.get("valid_frame_count", 0)
                )

                # Take MAX for totaltime_avg and selftime_avg (since we use MAX for valid_frame_count)
                merged_func["totaltime_avg"] = max(
                    merged_func.get("totaltime_avg", 0),
                    func.get("totaltime_avg", 0)
                )
                merged_func["selftime_avg"] = max(
                    merged_func.get("selftime_avg", 0),
                    func.get("selftime_avg", 0)
                )

                # Take max totaltime_max and selftime_max
                merged_func["totaltime_max"] = max(
                    merged_func.get("totaltime_max", 0),
                    func.get("totaltime_max", 0)
                )
                merged_func["selftime_max"] = max(
                    merged_func.get("selftime_max", 0),
                    func.get("selftime_max", 0)
                )

                # Accumulate high_frame (different call paths may have different high-cost frames)
                merged_func["high_frame"] = merged_func.get("high_frame", 0) + func.get("high_frame", 0)

                # Accumulate high_selftime
                merged_func["high_selftime"] = merged_func.get("high_selftime", 0) + func.get("high_selftime", 0)

                # Take max high_frame_total
                merged_func["high_frame_total"] = max(
                    merged_func.get("high_frame_total", 0),
                    func.get("high_frame_total", 0)
                )

                # Take max high_totaltime
                merged_func["high_totaltime"] = max(
                    merged_func.get("high_totaltime", 0),
                    func.get("high_totaltime", 0)
                )

                merged_func["_occurrences"] += 1

        # Remove internal field and return as list
        return [func for func in merged.values()]

    def _merge_for_high_cost_frames(self, func_list: List[dict]) -> List[dict]:
        """Merge functions for high_cost_frames sorting

        IMPORTANT: For high_cost_frames sorting, we MUST use MAX for high_frame, NOT SUM!
        Reason: If the same function appears in multiple call paths in the same frame,
        summing high_frame would double-count the same frame. We want to count unique
        high-cost frames, not total occurrences across all call paths.

        Args:
            func_list: Function list (may contain duplicate function names)

        Returns:
            Merged function list with unique function names
        """
        if not func_list:
            return []

        # Group by function name
        merged = {}

        for func in func_list:
            func_name = func.get("name", "")
            if not func_name:
                continue

            if func_name not in merged:
                # First occurrence, copy the function
                merged[func_name] = func.copy()
                # Reset fields that need accumulation
                merged[func_name]["_occurrences"] = 1
            else:
                # Accumulate metrics
                merged_func = merged[func_name]

                # Accumulate self_time (sum of all self times)
                merged_func["self_time"] = merged_func.get("self_time", 0) + func.get("self_time", 0)

                # Take max total_time (total time represents the call chain, max is more meaningful)
                merged_func["total_time"] = max(
                    merged_func.get("total_time", 0),
                    func.get("total_time", 0)
                )

                # Accumulate calls
                merged_func["calls"] = merged_func.get("calls", 0) + func.get("calls", 0)

                # Take max avg_total_time
                merged_func["avg_total_time"] = max(
                    merged_func.get("avg_total_time", 0),
                    func.get("avg_total_time", 0)
                )

                # Take max max_total_time
                merged_func["max_total_time"] = max(
                    merged_func.get("max_total_time", 0),
                    func.get("max_total_time", 0)
                )

                # Accumulate gc_mem
                merged_func["gc_mem"] = merged_func.get("gc_mem", 0) + func.get("gc_mem", 0)

                # Accumulate gt_10ms
                merged_func["gt_10ms"] = merged_func.get("gt_10ms", 0) + func.get("gt_10ms", 0)

                # Take max total_time_percentage
                merged_func["total_time_percentage"] = max(
                    merged_func.get("total_time_percentage", 0),
                    func.get("total_time_percentage", 0)
                )

                # For fun_top API enhanced fields
                # For high_cost_frames, we still use MAX for valid_frame_count to avoid double counting
                merged_func["valid_frame_count"] = max(
                    merged_func.get("valid_frame_count", 0),
                    func.get("valid_frame_count", 0)
                )

                # Take max for totaltime_avg and selftime_avg
                merged_func["totaltime_avg"] = max(
                    merged_func.get("totaltime_avg", 0),
                    func.get("totaltime_avg", 0)
                )
                merged_func["selftime_avg"] = max(
                    merged_func.get("selftime_avg", 0),
                    func.get("selftime_avg", 0)
                )

                # Take max totaltime_max and selftime_max
                merged_func["totaltime_max"] = max(
                    merged_func.get("totaltime_max", 0),
                    func.get("totaltime_max", 0)
                )
                merged_func["selftime_max"] = max(
                    merged_func.get("selftime_max", 0),
                    func.get("selftime_max", 0)
                )

                # CRITICAL FIX: Use MAX for high_frame to avoid double-counting the same frame
                # Reason: Same function in different call paths of the same frame should count as 1 frame
                merged_func["high_frame"] = max(
                    merged_func.get("high_frame", 0),
                    func.get("high_frame", 0)
                )

                # For high_selftime, we also use MAX (consistent with high_frame)
                # The highest single-frame high_selftime among all call paths
                merged_func["high_selftime"] = max(
                    merged_func.get("high_selftime", 0),
                    func.get("high_selftime", 0)
                )

                # Take max high_frame_total
                merged_func["high_frame_total"] = max(
                    merged_func.get("high_frame_total", 0),
                    func.get("high_frame_total", 0)
                )

                # Take max high_totaltime
                merged_func["high_totaltime"] = max(
                    merged_func.get("high_totaltime", 0),
                    func.get("high_totaltime", 0)
                )

                merged_func["_occurrences"] += 1

        # Remove internal field and return as list
        return [func for func in merged.values()]

    def _sort_functions(
        self,
        func_list: List[dict],
        sort_by: str,
        top_n: int,
        min_calls: int = 0,
        exclude_unity_entry: bool = False
    ) -> List[dict]:
        """Sort functions by metric and get top N

        Args:
            func_list: Function list
            sort_by: Sort metric (self_time/total_time/calls/avg_time/impact_score/high_cost_frames)
            top_n: Number of top functions
            min_calls: Minimum call count threshold (for filtering valid frames)
            exclude_unity_entry: Whether to exclude Unity common entry point functions

        Returns:
            Top N functions
        """
        if not func_list:
            return []

        # Step 1: Merge functions with the same name
        # Use different merge strategies for different sorting dimensions
        if sort_by == "valid_frame_avg":
            # For valid_frame_avg, use MAX for valid_frame_count to avoid double counting
            merged_funcs = self._merge_for_valid_frame_avg(func_list)
        elif sort_by == "high_cost_frames":
            # For high_cost_frames, accumulate high_frame but use MAX for valid_frame_count
            merged_funcs = self._merge_for_high_cost_frames(func_list)
        else:
            # For other sorting dimensions, use the default merge logic
            merged_funcs = self._merge_functions_by_name(func_list)

        # Apply filters
        filtered_funcs = merged_funcs

        # Filter by minimum call count if specified
        if min_calls > 0:
            filtered_funcs = [f for f in filtered_funcs if f.get("calls", 0) >= min_calls]

        # Filter out Unity entry point functions if specified
        if exclude_unity_entry:
            filtered_funcs = [f for f in filtered_funcs if not self.is_unity_entry(f)]

        # Map sort_by to field name or compute derived metric
        # Data source reference:
        # - self_time, total_time, calls: from get_func_data_summary (Step 1)
        # - valid_frame_count, totaltime_avg, high_frame: from fun_top API (Step 2)

        if sort_by == "self_time":
            # 按自身耗时排序 - 使用 get_func_data_summary 的 self_time 字段
            # 识别函数自身执行耗时最长的函数（不包含子函数调用时间）
            sorted_funcs = sorted(
                filtered_funcs,
                key=lambda f: f.get("self_time", 0),
                reverse=True
            )
        elif sort_by == "total_time":
            # 按总耗时排序 - 使用 get_func_data_summary 的 total_time 字段
            # 识别函数及其子函数总耗时最长的函数
            sorted_funcs = sorted(
                filtered_funcs,
                key=lambda f: f.get("total_time", 0),
                reverse=True
            )
        elif sort_by == "calls":
            # 按调用次数排序 - 使用 get_func_data_summary 的 calls 字段
            sorted_funcs = sorted(
                filtered_funcs,
                key=lambda f: f.get("calls", 0),
                reverse=True
            )
        elif sort_by == "impact_score":
            # 有效帧影响分数 = 平均总耗时 * log(调用次数 + 1)
            # 筛选出调用次数较多、对性能影响严重的数据
            # 使用 get_func_data_summary 的数据
            def get_impact_score(f):
                calls = f.get("calls", 0)
                avg_time = f.get("avg_total_time", f.get("total_time", 0) / max(calls, 1))
                # 使用对数权重，避免调用次数过多时影响分数过大
                import math
                weight = math.log(calls + 1) if calls > 0 else 0
                return avg_time * weight

            sorted_funcs = sorted(
                filtered_funcs,
                key=get_impact_score,
                reverse=True
            )
        elif sort_by == "avg_time":
            # 按平均单次耗时排序 - 使用 get_func_data_summary 的数据
            sorted_funcs = sorted(
                filtered_funcs,
                key=lambda f: f.get("avg_total_time",
                    f.get("total_time", 0) / max(f.get("calls", 1), 1)),
                reverse=True
            )
        elif sort_by == "high_cost_frames":
            # 按高耗时帧排序 - 使用 fun_top API 的 high_frame 字段
            # 找到序列较多的高耗时帧数据
            # v3.3 更新：简化排序规则，只按 high_frame 排序，不再按 totaltime_max 二次排序
            sorted_funcs = sorted(
                filtered_funcs,
                key=lambda f: f.get("high_frame", 0),
                reverse=True
            )
        elif sort_by == "valid_frame_avg":
            # 按有效帧平均耗时排序 - 使用 fun_top API 的 totaltime_avg
            # 先筛选有效帧数最多的前40%函数，然后在这些函数中按平均耗时排序
            # 这样代表了调用次数多且耗时较高的数据

            # 只分析有有效帧记录的函数
            funcs_with_valid_frames = [f for f in filtered_funcs if f.get("valid_frame_count", 0) > 0]

            if not funcs_with_valid_frames:
                # 如果没有有效帧数据，返回空列表
                return []

            # 第一步：按有效帧数降序排序
            sorted_by_valid_count = sorted(
                funcs_with_valid_frames,
                key=lambda f: f.get("valid_frame_count", 0),
                reverse=True
            )

            # 第二步：取前40%的函数（调用次数最多的）
            top_40_percent_count = max(1, int(len(sorted_by_valid_count) * 0.4))
            top_40_percent = sorted_by_valid_count[:top_40_percent_count]

            # 第三步：在前40%中按有效帧平均耗时降序排序
            sorted_funcs = sorted(
                top_40_percent,
                key=lambda f: f.get("totaltime_avg", 0),
                reverse=True
            )
        elif sort_by == "high_cost_frame_ratio":
            # 按高耗时帧占比排序 = gt_10ms / calls
            # 使用 get_func_data_summary 的 gt_10ms 和 calls
            def get_high_cost_ratio(f):
                calls = f.get("calls", 0)
                gt_10ms = f.get("gt_10ms", 0)
                return (gt_10ms / calls) if calls > 0 else 0

            # 只分析有高耗时帧记录的函数
            filtered_funcs = [f for f in filtered_funcs if f.get("gt_10ms", 0) > 0]
            sorted_funcs = sorted(
                filtered_funcs,
                key=get_high_cost_ratio,
                reverse=True
            )

        # Return top N
        return sorted_funcs[:top_n]

    def _get_fun_top(
        self,
        case_id: str,
        top_n: int,
        sort_by: str
    ) -> dict:
        """Get top functions (kept for backward compatibility)

        Args:
            case_id: Case ID
            top_n: Number of top functions
            sort_by: Sort metric

        Returns:
            Top functions data
        """
        # Use the new implementation
        func_list = self._get_func_list(case_id, use_cache=True)
        return self._sort_functions(func_list, sort_by, top_n)

    def group_by_module(
        self,
        func_list: List[dict],
        top_n: int = 10
    ) -> dict:
        """Group functions by module and get top N for each module

        Uses data from get_func_data_summary to categorize functions into modules
        based on function name patterns.

        Args:
            func_list: Function list from get_func_data_summary
            top_n: Number of top functions per module (default: 10)

        Returns:
            Dictionary with module names as keys and top functions as values
            Format: {module_name: [top_functions]}
        """
        # Step 1: Merge functions with the same name first
        merged_funcs = self._merge_functions_by_name(func_list)

        result = {}

        # Initialize all modules with empty lists
        for module_name in self.MODULE_PATTERNS.keys():
            result[module_name] = []

        # Categorize functions into modules
        for func in merged_funcs:
            func_name = func.get("name", "")
            module = self._classify_function_module(func_name)

            if module and module in result:
                result[module].append(func)

        # Sort each module by self_time and get top N
        for module_name in result:
            # Sort by self_time (descending)
            result[module_name] = sorted(
                result[module_name],
                key=lambda f: f.get("self_time", 0),
                reverse=True
            )[:top_n]

        return result

    def is_unity_entry(self, func: dict) -> bool:
        """Check if function is a Unity entry point using pattern matching

        Args:
            func: Function dict with 'name' field

        Returns:
            True if function is a Unity entry point, False otherwise
        """
        name = func.get("name", "")
        # Exact match
        if name in self.UNITY_ENTRY_PATTERNS:
            return True
        # Startswith match for handling "FunctionName: Variant" patterns
        for pattern in self.UNITY_ENTRY_PATTERNS:
            if name.startswith(pattern):
                return True
        return False

    def _classify_function_module(self, func_name: str) -> Optional[str]:
        """Classify a function into a module based on its name

        Args:
            func_name: Function name

        Returns:
            Module name or None if not classified
        """
        if not func_name:
            return None

        # Check each module's patterns
        for module_name, patterns in self.MODULE_PATTERNS.items():
            for pattern in patterns:
                if pattern in func_name:
                    return module_name

        # Default to None for uncategorized functions
        return None

    @property
    def data_type(self) -> str:
        """Data type identifier"""
        return "hotspots"
