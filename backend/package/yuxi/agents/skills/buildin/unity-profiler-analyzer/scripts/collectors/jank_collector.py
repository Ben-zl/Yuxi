"""Unity Profiler Analyzer - Jank Frame Collector"""

from typing import List, Optional, Dict, Tuple
from datetime import datetime
import re

# Handle both relative and absolute imports
try:
    from .base_collector import BaseCollector
except (ImportError, ValueError):
    # Absolute imports for direct execution
    from collectors.base_collector import BaseCollector


class JankCollector(BaseCollector):
    """Collector for jank frame data

    Jank frames are frames that exceed the target frame time threshold
    (typically > 16.67ms for 60 FPS or > 33.33ms for 30 FPS).

    API Response Format:
    - jank_frames: List of frame numbers (integers)
    - big_jank_frames: List of severe jank frame numbers
    """

    # Jank threshold definitions (in milliseconds)
    JANK_THRESHOLDS = {
        60: 16.67,   # 60 FPS target
        30: 33.33,   # 30 FPS target
    }

    # Default target FPS
    DEFAULT_TARGET_FPS = 60

    # Top N jank frames to analyze in detail
    DEFAULT_TOP_N = 10

    def __init__(self, client, cache_manager, config=None):
        """Initialize jank collector

        Args:
            client: UboxProfilerClient instance
            cache_manager: CacheManager instance
            config: Optional Config object
        """
        super().__init__(client, cache_manager, config)

    def collect(
        self,
        case_id: str,
        use_cache: bool = True,
        top_n: int = DEFAULT_TOP_N,
        analyze_details: bool = True
    ) -> dict:
        """Collect jank frame data

        Args:
            case_id: Case ID
            use_cache: Whether to use cache
            top_n: Number of top jank frames to analyze in detail
            analyze_details: Whether to analyze frame performance details

        Returns:
            Jank data containing:
            - jank_frames: Complete list of jank frame numbers
            - top_jank_frames: Top N jank frames with function details (sorted by frame_time)
            - summary: Statistical summary
            - total_frames: Total frame count (if available)
            - jank_count: Number of jank frames
        """
        # Try to load from cache
        if use_cache:
            cached = self._load_from_cache(case_id)
            if cached is not None:
                return cached

        # Fetch from API
        jank_data = self._fetch_jank_frames(case_id)

        if not jank_data:
            return {
                "jank_frames": [],
                "top_jank_frames": [],
                "summary": {},
                "total_frames": 0,
                "jank_count": 0,
            }

        # Extract and merge jank_frames and big_jank_frames
        # Both arrays may be present, merge them and deduplicate
        jank_frames_list = jank_data.get("jank_frames", [])
        big_jank_frames_list = jank_data.get("big_jank_frames", [])

        # Merge both lists and remove duplicates while preserving order
        seen_frames = set()
        merged_frame_numbers = []
        for frame_list in [jank_frames_list, big_jank_frames_list]:
            for frame in frame_list:
                if frame not in seen_frames:
                    seen_frames.add(frame)
                    merged_frame_numbers.append(frame)

        jank_frame_numbers = merged_frame_numbers
        total_frames = jank_data.get("total_frames", 0)
        jank_count = len(jank_frame_numbers)

        # Get detailed frame data for all jank frames to get frame_time
        jank_frames_with_details = []

        # Pre-load frameTime.frames for accurate frame time lookup
        frame_times_dict = self._load_frame_times_from_case_info(case_id)

        for frame_id in jank_frame_numbers:
            frame_details = self._get_frame_performance(case_id, frame_id)
            if frame_details:
                # Extract frame time - prioritize frameTime.frames data
                # This is critical because cpu.get_frame_performance().TotalTime is unreliable
                frame_time = self._extract_frame_time(frame_details, case_id, frame_times_dict)
                jank_frames_with_details.append({
                    "frame": frame_id,
                    "frame_time": frame_time,
                    "target_fps": self.DEFAULT_TARGET_FPS,
                    "details": frame_details
                })

        # Sort by frame_time (descending) to get the worst frames
        sorted_frames = sorted(
            jank_frames_with_details,
            key=lambda f: f.get("frame_time", 0),
            reverse=True
        )

        # Get top N jank frames with function analysis
        top_jank_frames = []
        if analyze_details and top_n > 0:
            top_jank_frames = self._analyze_top_jank_frames(
                sorted_frames[:top_n]
            )

        # Generate summary statistics
        frame_times = [f.get("frame_time", 0) for f in sorted_frames]
        summary = self._generate_summary(frame_times, total_frames, jank_count)

        # Prepare final data structure
        # jank_frames is the list of frame numbers (for backward compatibility)
        data = {
            "jank_frames": jank_frame_numbers,  # Original frame numbers
            "jank_frames_with_time": sorted_frames,  # Full details sorted by time
            "top_jank_frames": top_jank_frames,
            "summary": summary,
            "total_frames": total_frames,
            "jank_count": jank_count,
        }

        # Save to cache
        self._save_to_cache(case_id, data)

        return data

    def _fetch_from_api(self, case_id: str) -> dict:
        """Fetch jank frame data from API"""
        return self.collect(case_id, use_cache=False)

    def _fetch_jank_frames(self, case_id: str) -> Optional[dict]:
        """Fetch jank frames from API

        Args:
            case_id: Case ID

        Returns:
            Jank frame data dict or None if error
        """
        try:
            response = self.client.cpu.get_jank_frame(case_id)

            if response.get("code") != 0:
                if self.config and self.config.debug:
                    print(f"Error fetching jank frames: {response.get('message', 'Unknown error')}")
                return None

            return response.get("data")

        except Exception as e:
            if self.config and self.config.debug:
                print(f"Exception fetching jank frames: {e}")
            return None

    def _load_frame_times_from_case_info(self, case_id: str) -> dict:
        """Load frameTime.frames data from case_info for accurate frame time lookup

        Args:
            case_id: Case ID

        Returns:
            Dictionary mapping frame_id to frame_time in milliseconds
        """
        try:
            case_info = self.client.report.get_case_info(case_id)
            if case_info.get('code') == 0:
                frame_time_data = case_info['data'].get('frameTime', {})
                frames = frame_time_data.get('frames', [])

                # Build mapping: real_frame_id = index + 1 -> frame_time
                frame_times_dict = {}
                for idx, frame_time in enumerate(frames):
                    real_frame_id = idx + 1  # 索引 + 1 = 真实帧号
                    frame_times_dict[real_frame_id] = float(frame_time)

                return frame_times_dict
        except Exception as e:
            if self.config and self.config.debug:
                print(f"WARNING: Failed to load frameTime.frames: {e}")

        return {}

    def _extract_frame_time(self, frame_details: dict, case_id: str = None,
                           frame_times_dict: dict = None) -> float:
        """Extract frame time from frame performance details

        IMPORTANT: cpu.get_frame_performance().TotalTime is unreliable!
        We prioritize frameTime.frames data which is the only accurate source.

        Args:
            frame_details: Frame performance data
            case_id: Case ID (needed to access frameTime.frames)
            frame_times_dict: Pre-loaded frame times mapping

        Returns:
            Frame time in milliseconds
        """
        # Priority 1: Use pre-loaded frameTime.frames data
        if frame_times_dict:
            frame_id = int(frame_details.get("frame", 0))
            if frame_id in frame_times_dict:
                if self.config and self.config.debug:
                    print(f"DEBUG: Using frameTime.frames for frame {frame_id}: {frame_times_dict[frame_id]} ms")
                return frame_times_dict[frame_id]

        # Priority 2: Load frameTime.frames data on-the-fly
        if case_id:
            try:
                case_info = self.client.report.get_case_info(case_id)
                if case_info.get('code') == 0:
                    frames = case_info['data'].get('frameTime', {}).get('frames', [])
                    frame_id = int(frame_details.get("frame", 0))
                    idx = frame_id - 1  # Convert frame_id to array index

                    if 0 <= idx < len(frames):
                        return float(frames[idx])  # Unit is already in milliseconds
            except Exception as e:
                if self.config and self.config.debug:
                    print(f"WARNING: Failed to get frameTime for frame {frame_details.get('frame')}: {e}")

        # Priority 3: Fallback to frame_performance.TotalTime (UNRELIABLE!)
        # This should only be used as last resort as the data is often incorrect
        frame_data = frame_details.get("frame_data", [])
        if frame_data and len(frame_data) > 0:
            root_func = frame_data[0]
            total_time = float(root_func.get("TotalTime", 0))

            # Try to detect and fix unit issues
            # If total_time > 1000, it might be in seconds (unlikely for frame time)
            # If total_time < 1000 but very large, it might also be in seconds
            # This is a heuristic fix for unreliable API data
            if total_time > 100:
                # Assume it's in seconds, convert to milliseconds
                return total_time * 1000
            else:
                # Assume it's already in milliseconds
                return total_time

        # Last resort: try other fields
        if "frame_time" in frame_details:
            return float(frame_details.get("frame_time", 0))
        elif "total_time" in frame_details:
            return float(frame_details.get("total_time", 0))
        elif "time" in frame_details:
            return float(frame_details.get("time", 0))
        else:
            return 0.0

    def _analyze_top_jank_frames(
        self,
        top_frames: List[dict]
    ) -> List[dict]:
        """Analyze top jank frames to get function details and classification

        Args:
            top_frames: List of top jank frames with frame_time

        Returns:
            Enhanced jank frames with function details and classification
        """
        result = []

        for frame_data in top_frames:
            frame_id = frame_data.get("frame")
            if frame_id is None:
                continue

            # Extract and flatten function list from hierarchical frame_data
            frame_details = frame_data.get("details", {})
            functions = self._flatten_function_tree(frame_details.get("frame_data", []))

            # Build enhanced frame data
            enhanced_frame = {
                "frame": frame_id,
                "frame_time": frame_data.get("frame_time", 0),
                "target_fps": frame_data.get("target_fps", self.DEFAULT_TARGET_FPS),
                "functions": functions,
                "top_function": self._get_top_function(functions),
            }

            # Classify jank cause
            enhanced_frame["jank_cause"] = self.classify_jank_cause(enhanced_frame)

            result.append(enhanced_frame)

        return result

    def _flatten_function_tree(
        self,
        frame_data: List[dict],
        level: int = 0
    ) -> List[dict]:
        """Flatten hierarchical function tree into a list

        Args:
            frame_data: Function tree (root level is list with one item)
            level: Depth level in tree

        Returns:
            Flattened list of functions
        """
        result = []

        if not frame_data or len(frame_data) == 0:
            return result

        for node in frame_data:
            func_info = {
                "name": node.get("name", "N/A"),
                "SelfTime": node.get("SelfTime", 0),
                "TotalTime": node.get("TotalTime", 0),
                "Calls": node.get("Calls", 0),
                "TotalGC": node.get("TotalGC", 0),
                "level": level
            }
            result.append(func_info)

            # Process children recursively
            children = node.get("children", [])
            if children:
                result.extend(self._flatten_function_tree(children, level + 1))

        return result

    def _get_frame_performance(self, case_id: str, frame_id: int) -> dict:
        """Get frame performance details

        Args:
            case_id: Case ID
            frame_id: Frame ID

        Returns:
            Frame performance data
        """
        try:
            response = self.client.cpu.get_frame_performance(case_id, frame_id)

            if response.get("code") != 0:
                return {}

            data = response.get("data", {})
            # IMPORTANT: Add frame field for later frame_time lookup
            data["frame"] = frame_id
            return data

        except Exception as e:
            if self.config and self.config.debug:
                print(f"Error getting frame performance for frame {frame_id}: {e}")
            return {}

    def _get_top_function(self, functions: List[dict]) -> Optional[dict]:
        """Get the top耗时 function from function list

        Args:
            functions: List of function dicts

        Returns:
            Top function dict or None
        """
        if not functions:
            return None

        # Sort by SelfTime (descending) - use SelfTime as it represents the actual work done by this function
        sorted_funcs = sorted(
            functions,
            key=lambda f: f.get("SelfTime", 0),
            reverse=True
        )

        return sorted_funcs[0] if sorted_funcs else None

    def _generate_summary(
        self,
        frame_times: List[float],
        total_frames: int,
        jank_count: int
    ) -> dict:
        """Generate summary statistics for jank frames

        Args:
            frame_times: List of frame times
            total_frames: Total frame count
            jank_count: Jank frame count

        Returns:
            Summary statistics dict
        """
        if not frame_times:
            return {
                "jank_rate": 0,
                "avg_frame_time": 0,
                "max_frame_time": 0,
                "min_frame_time": 0,
            }

        summary = {
            "jank_rate": (jank_count / total_frames * 100) if total_frames > 0 else 0,
            "avg_frame_time": sum(frame_times) / len(frame_times),
            "max_frame_time": max(frame_times),
            "min_frame_time": min(frame_times),
        }

        return summary

    def classify_jank_cause(self, frame_data: dict) -> str:
        """Classify the cause of jank based on top function name

        Args:
            frame_data: Frame data with functions

        Returns:
            Jank cause category
        """
        top_function = frame_data.get("top_function")
        if not top_function:
            # Try to get from functions list
            functions = frame_data.get("functions", [])
            if functions:
                top_function = functions[0]
            else:
                return "Unknown"

        func_name = top_function.get("name", "")

        # Rendering related
        if any(pattern in func_name for pattern in [
            "Draw", "Render", "Camera", "Shader", "Mesh", "Material",
            "Texture", "Graphics", "GPU", "ScriptableRenderer",
        ]):
            return "Rendering"

        # GC related
        if any(pattern in func_name for pattern in [
            "GC.Collect", "GC.Alloc", "GC.Realloc",
        ]):
            return "GC"

        # Physics related
        if any(pattern in func_name for pattern in [
            "Physics", "Rigidbody", "Collider", "FixedUpdate",
        ]):
            return "Physics"

        # Animation related
        if any(pattern in func_name for pattern in [
            "Animator", "Animation", "State", "BlendTree",
        ]):
            return "Animation"

        # Script/Logic related
        if any(pattern in func_name for pattern in [
            "Mono", "Script", "Behaviour", "Coroutine",
            "Invoke", "Assembly",
        ]):
            return "Script"

        # Resource loading
        if any(pattern in func_name for pattern in [
            "Resources.Load", "AssetBundle.Load", "ResourceManager",
        ]):
            return "Resource"

        return "Other"

    @property
    def data_type(self) -> str:
        """Data type identifier for caching"""
        return "jank"

    # ==================== Screenshot Methods ====================

    # Screenshot base URL format
    SCREENSHOT_BASE_URL = "https://minio-cluster.testplus.cn"

    def _get_screen_list(self, case_id: str) -> Dict:
        """Fetch screen list data from API

        Args:
            case_id: Case ID

        Returns:
            Screen list data dict containing URL list or empty dict if error

        API Response Format:
        {
            "code": 0,
            "msg": "success",
            "data": ["url1", "url2", ...]  # List of screenshot URLs
        }
        """
        try:
            # Use report.get_screen_list API (correct endpoint)
            response = self.client.report.get_screen_list(case_id)

            if response.get("code") != 0:
                if self.config and self.config.debug:
                    print(f"Error fetching screen list: {response.get('message', 'Unknown error')}")
                return {}

            data = response.get("data", [])

            # The API returns a list of URL strings directly
            # Wrap it in a dict for easier handling, or return as-is if already a list
            # For consistency, return the data as-is (list)
            # DEBUG: Print structure
            if self.config and self.config.debug:
                print(f"DEBUG: Screen list data type: {type(data)}")
                if isinstance(data, list):
                    print(f"DEBUG: Screen count: {len(data)}")
                    if len(data) > 0:
                        print(f"DEBUG: First screen: {data[0]}")

            # Return data directly (it's already a list)
            return data

        except Exception as e:
            if self.config and self.config.debug:
                print(f"Exception fetching screen list: {e}")
            return {}

    def _build_screenshot_url(
        self,
        project_id: str,
        case_id: str,
        timestamp: int,
        frame: int
    ) -> str:
        """Build screenshot URL from components

        Args:
            project_id: Project ID (APPKEY)
            case_id: Case ID
            timestamp: Screenshot timestamp (milliseconds)
            frame: Frame number

        Returns:
            Screenshot URL
        """
        # URL format: https://minio-cluster.testplus.cn/{project_id}/{case_id}/screenshot/{timestamp}_{frame}.jpg
        return f"{self.SCREENSHOT_BASE_URL}/{project_id}/{case_id}/screenshot/{timestamp}_{frame}.jpg"

    def _find_screenshot_for_frame(
        self,
        screen_data,  # Can be list or dict
        frame: int
    ) -> Optional[str]:
        """Find screenshot URL for a specific frame (finds closest match)

        Args:
            screen_data: Screen list data from API (list of URLs or dict with screens key)
            frame: Frame number

        Returns:
            Screenshot URL or None if not found

        API Response Format:
        {
            "code": 0,
            "msg": "success",
            "data": [
                "https://minio-cluster.testplus.cn/{project_id}/{case_id}/screenshot/{timestamp}_{frame}.jpg",
                ...
            ]
        }
        Note: timestamp is in SECONDS (10 digits), not milliseconds
        """
        if not screen_data:
            return None

        # Handle both list (direct URL list) and dict (with screens key) formats
        if isinstance(screen_data, list):
            screens = screen_data
        elif isinstance(screen_data, dict):
            # Try common structures for backward compatibility
            screens = screen_data.get("screens", [])
            if not screens:
                screens = screen_data.get("screen_list", [])
            if not screens and isinstance(screen_data.get("data"), list):
                screens = screen_data.get("data", [])
        else:
            return None

        if not screens:
            return None

        # Find closest screenshot by parsing frame number from URL
        closest_url = None
        min_distance = float('inf')

        import re
        # Pattern to extract frame number from URL: .../screenshot/{timestamp}_{frame}.jpg
        url_pattern = re.compile(r'/screenshot/\d+_(\d+)\.jpg')

        for screen in screens:
            # Handle both string URLs and dict objects
            if isinstance(screen, str):
                url = screen
                # Extract frame number from URL
                match = url_pattern.search(url)
                if match:
                    screen_frame = int(match.group(1))
                    distance = abs(screen_frame - frame)
                    if distance < min_distance:
                        min_distance = distance
                        closest_url = url
            elif isinstance(screen, dict):
                # Handle dict format (backward compatibility)
                url = screen.get("url") or screen.get("screenshot_url")
                if url:
                    match = url_pattern.search(url)
                    if match:
                        screen_frame = int(match.group(1))
                        distance = abs(screen_frame - frame)
                        if distance < min_distance:
                            min_distance = distance
                            closest_url = url
                else:
                    # Try to get frame directly from dict
                    screen_frame = screen.get("frame") or screen.get("frameNumber")
                    if screen_frame is not None:
                        distance = abs(screen_frame - frame)
                        if distance < min_distance:
                            min_distance = distance
                            # Build URL if components available
                            timestamp = screen.get("timestamp") or screen.get("time") or screen.get("ts")
                            project_id = screen.get("project_id") or screen.get("projectId")
                            case_id = screen.get("case_id") or screen.get("caseId")
                            actual_frame = screen.get("frame") or screen.get("frameNumber")

                            if timestamp and project_id and case_id:
                                closest_url = self._build_screenshot_url(project_id, case_id, timestamp, actual_frame)

        # If we found a close enough screenshot (within 200 frames)
        if closest_url and min_distance <= 200:
            # Add frame offset info as URL fragment if not exact match
            # Extract frame from the URL to check if it's exact
            match = url_pattern.search(closest_url)
            if match:
                actual_frame = int(match.group(1))
                if actual_frame != frame:
                    closest_url += f"#frame_offset={min_distance}"
            return closest_url

        return None

    def collect_with_screenshots(
        self,
        case_id: str,
        use_cache: bool = True,
        top_n: int = DEFAULT_TOP_N,
        analyze_details: bool = True,
        project_id: str = None
    ) -> dict:
        """Collect jank frame data with screenshot URLs

        Args:
            case_id: Case ID
            use_cache: Whether to use cache
            top_n: Number of top jank frames to analyze in detail
            analyze_details: Whether to analyze frame performance details
            project_id: Project ID (APPKEY) - required for building screenshot URLs

        Returns:
            Jank data with screenshot URLs included:
            - jank_frames: Complete list of jank frame numbers
            - top_jank_frames: Top N jank frames with function details and screenshots
            - summary: Statistical summary
            - total_frames: Total frame count (if available)
            - jank_count: Number of jank frames
        """
        # Get base jank data
        data = self.collect(case_id, use_cache, top_n, analyze_details)

        # Fetch screen list
        screen_data = self._get_screen_list(case_id)

        # Get project_id from client config if not provided
        if not project_id:
            project_id = self.client.config.project_id

        # Add screenshot URLs to top jank frames
        if analyze_details and data.get("top_jank_frames"):
            for frame_data in data["top_jank_frames"]:
                frame = frame_data.get("frame")
                if frame:
                    # Try to find screenshot from API response
                    screenshot_url = self._find_screenshot_for_frame(screen_data, frame)

                    # If not found in API response, build URL from case info
                    if not screenshot_url and project_id:
                        # Use case_id from parameter and timestamp from frame_data if available
                        # For now, we'll store the URL pattern
                        timestamp = int(datetime.now().timestamp() * 1000)  # Fallback timestamp
                        screenshot_url = self._build_screenshot_url(project_id, case_id, timestamp, frame)

                    frame_data["screenshot_url"] = screenshot_url

        return data

    # ==================== Timestamp-based Screenshot Methods ====================

    def _parse_raw_filename(self, filename: str) -> Tuple[Optional[float], Optional[int]]:
        """Parse {timestamp}_{frame}.raw.zip filename

        Args:
            filename: File name like "1769513805.0_36378.raw.zip"

        Returns:
            Tuple of (timestamp, frame) or (None, None) if invalid format
        """
        pattern = re.compile(r'(\d+\.?\d*)_(\d+)\.raw\.zip')
        match = pattern.search(filename)
        if match:
            timestamp = float(match.group(1))
            frame = int(match.group(2))
            return timestamp, frame
        return None, None

    def _calculate_frame_timestamps(self, case_id: str) -> List[float]:
        """Calculate timestamps for all frames using calibration from raw files

        This method:
        1. Gets raw files data to extract calibration points (every ~300 frames)
        2. Gets frameTime.frames data for frame durations
        3. Calculates timestamps by accumulating frame times
        4. Calibrates every 300 frames using raw file data

        Args:
            case_id: Case ID

        Returns:
            List of timestamps in seconds (same length as frameTime.frames)
        """
        # Get raw files for calibration points
        try:
            original_files = self.client.report.get_original_files(case_id)
            files_list = original_files.get('data', [])
        except Exception as e:
            if self.config and self.config.debug:
                print(f"Error fetching original files: {e}")
            return []

        # Parse calibration points from raw files
        calibration_points = {}  # {frame_number: timestamp}
        for file_info in files_list:
            filename = file_info.get('fileName', '')
            timestamp, frame = self._parse_raw_filename(filename)
            if timestamp is not None and frame is not None:
                calibration_points[frame] = timestamp

        if self.config and self.config.debug:
            print(f"DEBUG: Found {len(calibration_points)} calibration points from raw files")

        # Get frame time data
        try:
            case_info = self.client.report.get_case_info(case_id)
            frame_time_data = case_info.get('data', {}).get('frameTime', {})
            frame_times = frame_time_data.get('frames', [])
        except Exception as e:
            if self.config and self.config.debug:
                print(f"Error fetching case info: {e}")
            return []

        if not frame_times:
            if self.config and self.config.debug:
                print("DEBUG: No frame time data found")
            return []

        if self.config and self.config.debug:
            print(f"DEBUG: Total frames: {len(frame_times)}")

        # Calculate timestamps for all frames
        sorted_calibration = sorted(calibration_points.items())

        if not sorted_calibration:
            # No calibration points, just accumulate from 0
            timestamps = [0.0] * len(frame_times)
            current_ts = 0.0
            for i, ft in enumerate(frame_times):
                current_ts += ft / 1000.0  # Convert ms to seconds
                timestamps[i] = current_ts
            return timestamps

        # Get the first calibration point as the Profiler recording start time
        baseline_frame, baseline_timestamp = sorted_calibration[0]

        # The first raw file timestamp represents the Profiler recording start time
        # No need to calculate pre-baseline time
        start_ts = baseline_timestamp

        # Initialize timestamps array
        timestamps = [0.0] * len(frame_times)

        # Calculate timestamps for frames BEFORE the first raw file
        # Start from start_ts and accumulate frame times
        current_ts = start_ts
        for i in range(baseline_frame):
            timestamps[i] = current_ts
            # Move to next frame by adding this frame's duration
            current_ts += frame_times[i] / 1000.0

        # Set the first calibration point frame timestamp
        timestamps[baseline_frame] = baseline_timestamp
        current_ts = baseline_timestamp

        # Process frames AFTER the first calibration point
        calib_index = 1  # Start from second calibration point (first one already used)
        for i in range(baseline_frame + 1, len(frame_times)):
            # Accumulate time for this frame
            current_ts += frame_times[i] / 1000.0
            timestamps[i] = current_ts

            # Check if we need to calibrate at this frame
            while calib_index < len(sorted_calibration) and i == sorted_calibration[calib_index][0]:
                calib_frame, calib_ts = sorted_calibration[calib_index]
                # Adjust current timestamp to calibration point
                timestamps[i] = calib_ts
                current_ts = calib_ts
                calib_index += 1

        return timestamps

    def _find_screenshot_by_timestamp(
        self,
        screen_data,
        target_timestamp: float,
        target_frame: int,
        max_offset_seconds: float = 10.0
    ) -> Tuple[Optional[str], Optional[float], Optional[float]]:
        """Find screenshot URL by closest timestamp AND frame number

        This is a two-step matching algorithm:
        1. Filter screenshots by timestamp proximity (within max_offset_seconds)
        2. Among candidates with similar timestamps, pick the one with closest frame number

        Args:
            screen_data: Screen list data from API (list of URLs or dict with data)
            target_timestamp: Target timestamp in seconds (Unix timestamp)
            target_frame: Target frame number
            max_offset_seconds: Maximum allowed time difference in seconds

        Returns:
            Tuple of (screenshot_url, actual_timestamp, offset_seconds)
            or (None, None, None) if no match found
        """
        # Extract screenshot list
        if isinstance(screen_data, list):
            screens = screen_data
        elif isinstance(screen_data, dict):
            screens = screen_data.get('data', [])
        else:
            return None, None, None

        if not screens:
            return None, None, None

        # Parse timestamps from screenshot URLs
        # Format: .../screenshot/{timestamp}_{frame}.jpg
        url_pattern = re.compile(r'/screenshot/(\d+)_(\d+)\.jpg')

        # Step 1: Find all screenshots within timestamp tolerance
        candidates = []
        for screen in screens:
            if isinstance(screen, str):
                url = screen
                match = url_pattern.search(url)
                if match:
                    # Timestamp is in SECONDS (10 digits)
                    screenshot_ts = float(match.group(1))
                    screenshot_frame = int(match.group(2))
                    time_diff = abs(screenshot_ts - target_timestamp)

                    # Only consider screenshots within time tolerance
                    if time_diff <= max_offset_seconds:
                        frame_diff = abs(screenshot_frame - target_frame)
                        candidates.append({
                            'url': url,
                            'timestamp': screenshot_ts,
                            'frame': screenshot_frame,
                            'time_diff': time_diff,
                            'frame_diff': frame_diff
                        })

        if not candidates:
            return None, None, None

        # Step 2: Among timestamp candidates, pick the one with closest frame number
        # Sort by time_diff first, then by frame_diff
        candidates.sort(key=lambda x: (x['time_diff'], x['frame_diff']))
        best = candidates[0]

        return best['url'], best['timestamp'], best['time_diff']

    def collect_with_timestamp_screenshots(
        self,
        case_id: str,
        use_cache: bool = True,
        top_n: int = DEFAULT_TOP_N,
        analyze_details: bool = True,
        project_id: str = None,
        max_screenshot_offset_seconds: float = 10.0
    ) -> dict:
        """Collect jank frame data with timestamp-based screenshot URLs

        This method uses a new approach to match screenshots:
        1. Calculates frame timestamps from frameTime.frames data
        2. Calibrates every 300 frames using raw profiler data
        3. Matches screenshots by timestamp (not frame number)

        Args:
            case_id: Case ID
            use_cache: Whether to use cache
            top_n: Number of top jank frames to analyze in detail
            analyze_details: Whether to analyze frame performance details
            project_id: Project ID (APPKEY) - required for building screenshot URLs
            max_screenshot_offset_seconds: Maximum time difference for screenshot matching

        Returns:
            Jank data with screenshot URLs included
        """
        # Get base jank data
        data = self.collect(case_id, use_cache, top_n, analyze_details)

        # Calculate frame timestamps
        frame_timestamps = self._calculate_frame_timestamps(case_id)

        if not frame_timestamps:
            if self.config and self.config.debug:
                print("DEBUG: Failed to calculate frame timestamps, falling back to frame-based matching")
            # Fall back to frame-based matching
            return self.collect_with_screenshots(case_id, use_cache, top_n, analyze_details, project_id)

        # Fetch screen list
        screen_data = self._get_screen_list(case_id)

        # Add screenshot URLs to top jank frames using timestamp matching
        if analyze_details and data.get("top_jank_frames"):
            for frame_data in data["top_jank_frames"]:
                frame = frame_data.get("frame")
                if frame is not None and frame < len(frame_timestamps):
                    frame_timestamp = frame_timestamps[frame]
                    screenshot_url, actual_ts, offset = self._find_screenshot_by_timestamp(
                        screen_data, frame_timestamp, frame, max_screenshot_offset_seconds
                    )

                    if screenshot_url:
                        frame_data["screenshot_url"] = screenshot_url
                        frame_data["screenshot_timestamp"] = actual_ts
                        frame_data["screenshot_offset"] = offset
                    else:
                        frame_data["screenshot_url"] = None

        return data
