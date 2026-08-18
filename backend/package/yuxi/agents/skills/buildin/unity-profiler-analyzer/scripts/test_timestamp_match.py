#!/usr/bin/env python
"""Test timestamp-based screenshot matching logic"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.analyzer import ProfilerAnalyzer
import re
import json

def parse_raw_filename(filename):
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

def calculate_frame_timestamps(case_id, analyzer):
    """Calculate timestamps for all frames using calibration

    Args:
        case_id: Case ID
        analyzer: ProfilerAnalyzer instance

    Returns:
        List of timestamps (same length as frameTime.frames)
    """
    # Get raw files for calibration points
    original_files = analyzer.client.report.get_original_files(case_id)
    files_list = original_files.get('data', [])

    # Parse calibration points from raw files
    calibration_points = {}  # {frame_number: timestamp}
    for file_info in files_list:
        filename = file_info.get('fileName', '')
        timestamp, frame = parse_raw_filename(filename)
        if timestamp is not None and frame is not None:
            calibration_points[frame] = timestamp

    print(f'Found {len(calibration_points)} calibration points from raw files')
    for frame, ts in sorted(calibration_points.items())[:5]:
        print(f'  Frame {frame}: timestamp={ts}')

    # Get frame time data
    case_info = analyzer.client.report.get_case_info(case_id)
    frame_time_data = case_info.get('data', {}).get('frameTime', {})
    frame_times = frame_time_data.get('frames', [])

    if not frame_times:
        print('No frame time data found')
        return []

    print(f'Total frames: {len(frame_times)}')

    # Calculate timestamps for all frames
    # Start with the first calibration point as baseline
    sorted_calibration = sorted(calibration_points.items())
    if not sorted_calibration:
        print('No calibration points found, using accumulation only')
        # No calibration points, just accumulate from frame 0
        timestamps = [0] * len(frame_times)
        current_ts = 0
        for i, ft in enumerate(frame_times):
            current_ts += ft / 1000.0  # Convert ms to seconds
            timestamps[i] = current_ts
        return timestamps

    # Get the first calibration point
    baseline_frame, baseline_timestamp = sorted_calibration[0]

    # Calculate timestamps for all frames
    timestamps = [0] * len(frame_times)

    # Find calibration points and use them for accuracy
    calib_index = 0
    current_calib_frame, current_calib_ts = sorted_calibration[calib_index]

    # Current timestamp accumulator
    current_ts = current_calib_ts

    # Process frames in segments
    # Frames before first calibration point: assume they start at baseline_timestamp - accumulated time
    if baseline_frame > 0:
        # Calculate time before baseline
        pre_baseline_time = sum(frame_times[:baseline_frame]) / 1000.0
        start_ts = baseline_timestamp - pre_baseline_time

        for i in range(baseline_frame):
            if i == 0:
                timestamps[i] = start_ts
            else:
                timestamps[i] = timestamps[i-1] + frame_times[i] / 1000.0

        current_ts = timestamps[baseline_frame - 1] + frame_times[baseline_frame] / 1000.0
    else:
        current_ts = current_calib_ts

    # Process from baseline frame onwards
    for i in range(baseline_frame, len(frame_times)):
        # Accumulate time for this frame
        if i == 0:
            timestamps[i] = current_ts
        else:
            timestamps[i] = timestamps[i-1] + frame_times[i] / 1000.0
            current_ts = timestamps[i]

        # Check if we need to calibrate at this frame
        while calib_index < len(sorted_calibration) and i == sorted_calibration[calib_index][0]:
            calib_frame, calib_ts = sorted_calibration[calib_index]
            # Adjust current timestamp to calibration point
            timestamps[i] = calib_ts
            current_ts = calib_ts
            calib_index += 1

    return timestamps

def find_screenshot_by_timestamp(screen_data, target_timestamp):
    """Find screenshot URL by closest timestamp

    Args:
        screen_data: List of screenshot URLs or dict with data
        target_timestamp: Target timestamp in seconds

    Returns:
        Tuple of (screenshot_url, actual_timestamp, offset_seconds)
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
    url_pattern = re.compile(r'/screenshot/(\d+)_(\d+)\.jpg')

    closest_url = None
    closest_ts = None
    min_diff = float('inf')

    for screen in screens:
        if isinstance(screen, str):
            url = screen
            match = url_pattern.search(url)
            if match:
                # Timestamp is in SECONDS (10 digits)
                screenshot_ts = float(match.group(1))
                diff = abs(screenshot_ts - target_timestamp)
                if diff < min_diff:
                    min_diff = diff
                    closest_url = url
                    closest_ts = screenshot_ts

    # Match if within 10 seconds
    if closest_url and min_diff <= 10:
        offset = min_diff
        return closest_url, closest_ts, offset

    return None, None, None

# Test the logic
analyzer = ProfilerAnalyzer(
    project_id='pcavpt6w',
    base_url='https://ubox.testplus.cn'
)

case_id = 'e7841d94-fb73-11f0-b722-342eb790fa55'

print('=== Calculating Frame Timestamps ===')
timestamps = calculate_frame_timestamps(case_id, analyzer)

if timestamps:
    print(f'\nCalculated {len(timestamps)} frame timestamps')
    print('Sample timestamps:')
    for i in [0, 100, 1000, 5000, 10000, 20000, 30000, 36378, 36678, 36978]:
        if i < len(timestamps):
            print(f'  Frame {i}: timestamp={timestamps[i]:.2f} s')

    print('\n=== Testing Screenshot Matching by Timestamp ===')
    screens = analyzer.client.report.get_screen_list(case_id)
    screen_list = screens.get('data', []) if isinstance(screens, dict) else screens

    # Test for specific frames
    test_frames = [80, 242, 298, 469, 596]
    for frame in test_frames:
        if frame < len(timestamps):
            target_ts = timestamps[frame]
            url, actual_ts, offset = find_screenshot_by_timestamp(screen_list, target_ts)
            print(f'Frame {frame} (ts={target_ts:.2f}):')
            if actual_ts is not None:
                print(f'  Matched screenshot ts: {actual_ts}, offset: {offset:.2f}s')
            else:
                print(f'  No matching screenshot found')
            if url:
                print(f'  URL: {url[:80]}...')
            print()
