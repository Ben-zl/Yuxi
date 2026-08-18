"""Unity Profiler Analyzer - CLI Entry Point

This CLI provides data collection and analysis capabilities.
For report generation, use AI agent with the prompts defined in SKILL.md.
"""

import sys
import os
import json
import click

# Add ubox-api to Python path
def add_ubox_api_to_path():
    """Find and add ubox-api directory to Python path"""
    current_dir = os.path.dirname(os.path.abspath(__file__))

    # Path candidates in order of preference
    path_candidates = [
        # When in Claude Code's skills directory (skills/utils/ubox-api)
        os.path.abspath(os.path.join(current_dir, "../../../utils/ubox-api")),
        # Relative to unity-profiler-analyzer directory (utils is sibling)
        os.path.abspath(os.path.join(current_dir, "../../utils/ubox-api")),
        # When Agent-skills is sibling to skills
        os.path.abspath(os.path.join(current_dir, "../../../Agent-skills/utils/ubox-api")),
        # When utils is in parent directory
        os.path.abspath(os.path.join(current_dir, "../../../../utils/ubox-api")),
    ]

    # Check each candidate path
    for ubox_api_path in path_candidates:
        ubox_profiler_path = os.path.join(ubox_api_path, "ubox_profiler")
        if os.path.exists(ubox_profiler_path) and os.path.exists(os.path.join(ubox_profiler_path, "__init__.py")):
            sys.path.insert(0, ubox_api_path)
            return ubox_api_path

    # Fallback: search parent directories
    parent = current_dir
    for _ in range(5):
        parent = os.path.dirname(parent)
        for ubox_name in ["utils/ubox-api", "ubox-api"]:
            ubox_api_path = os.path.join(parent, ubox_name.replace("/", os.sep))
            ubox_profiler_path = os.path.join(ubox_api_path, "ubox_profiler")
            if os.path.exists(ubox_profiler_path) and os.path.exists(os.path.join(ubox_profiler_path, "__init__.py")):
                sys.path.insert(0, ubox_api_path)
                return ubox_api_path

    raise ImportError(
        f"Could not find ubox_profiler module. "
        f"Please ensure ubox-api is available. "
        f"Current directory: {current_dir}"
    )

try:
    add_ubox_api_to_path()
except ImportError:
    pass  # Try importing anyway (might be installed globally)

from ubox_profiler import UboxProfilerClient

# Handle both relative and absolute imports
try:
    from .analyzer import ProfilerAnalyzer
except (ImportError, ValueError):
    # If relative import fails, try absolute import
    # This happens when running cli.py directly
    from analyzer import ProfilerAnalyzer


@click.group()
@click.version_option(version="0.1.0")
def cli():
    """Unity Profiler Performance Analyzer - Analyze Unity game performance"""
    pass


@cli.command()
@click.argument("case_id", type=str)
@click.option("--overview", is_flag=True, help="Performance overview analysis")
@click.option("--hotspots", is_flag=True, help="Hotspot function analysis")
@click.option("--jank", is_flag=True, help="Jank frame analysis")
@click.option("--top", default=20, type=int, help="Top N functions/frames for analysis (default: 20)")
@click.option("--sort-by", default="self_time", type=click.Choice([
    "self_time", "total_time", "valid_frame_avg", "high_cost_frames"
]), help="Sort metric for hotspot analysis")
@click.option("--min-calls", default=0, type=int, help="Minimum call count threshold for filtering valid frames")
@click.option("--include-unity-entry", is_flag=True, help="Include Unity common entry point functions (e.g., PlayerLoop, BehaviourUpdate). Default is to exclude.")
@click.option("--output", "-o", type=click.Path(), help="Output file path")
@click.option("--url", type=str, help="API base URL")
@click.option("--project", type=str, help="Project ID (APPKEY)")
@click.option("--no-cache", is_flag=True, help="Disable cache")
@click.option("--raw", is_flag=True, help="Output raw jank data without Python preprocessing (default: AI-Ready mode with preprocessing)")
@click.option("--with-screenshots", is_flag=True, help="Include screenshot URLs in jank frame analysis (requires jank mode)")
@click.option("--use-timestamp-screenshots", is_flag=True, help="Use timestamp-based screenshot matching (more accurate than frame number matching)")
@click.option("--max-screenshot-offset", type=float, default=10.0, help="Maximum time difference (seconds) for screenshot matching (default: 10.0)")
@click.option("--verbose", "-v", is_flag=True, help="Verbose output")
@click.option("--platform", type=click.Choice(["mobile", "pc"], case_sensitive=False), default="mobile", help="Target platform: 'mobile' (default, stricter thresholds) or 'pc' (looser thresholds for PC/console games)")
def analyze(case_id, overview, hotspots, jank, top, sort_by, min_calls, include_unity_entry, output, url, project, no_cache, raw, with_screenshots, use_timestamp_screenshots, max_screenshot_offset, verbose, platform):
    """Analyze Unity Profiler data for a specific case

    Examples:

        # Overview analysis (mobile platform - default)
        python scripts/cli.py analyze <case_id> --overview

        # Overview analysis (PC platform - looser thresholds)
        python scripts/cli.py analyze <case_id> --overview --platform pc

        # Hotspot analysis - by self time (default, Unity Entry excluded)
        python scripts/cli.py analyze <case_id> --hotspots --top 20

        # Hotspot analysis - by total time (Unity Entry excluded)
        python scripts/cli.py analyze <case_id> --hotspots --top 20 --sort-by total_time

        # Hotspot analysis - by valid frame average (frequent calls + high cost)
        python scripts/cli.py analyze <case_id> --hotspots --top 20 --sort-by valid_frame_avg

        # Hotspot analysis - by high cost frames (finding stutter causes)
        python scripts/cli.py analyze <case_id> --hotspots --top 20 --sort-by high_cost_frames

        # Jank frame analysis (default: AI-Ready mode with pattern recognition and recommendations)
        python scripts/cli.py analyze <case_id> --jank

        # Jank frame analysis (Top 30 jank frames)
        python scripts/cli.py analyze <case_id> --jank --top 30

        # Jank frame analysis with screenshots (includes screenshot URLs for each jank frame)
        python scripts/cli.py analyze <case_id> --jank --with-screenshots

        # Jank frame analysis with timestamp-based screenshot matching (more accurate)
        python scripts/cli.py analyze <case_id> --jank --with-screenshots --use-timestamp-screenshots

        # Jank frame analysis (RAW mode - no Python preprocessing)
        python scripts/cli.py analyze <case_id> --jank --raw

        # Hotspot analysis - include Unity entry points (for complete analysis)
        python scripts/cli.py analyze <case_id> --hotspots --include-unity-entry

        # Save to file
        python scripts/cli.py analyze <case_id> --overview -o report.md
    """
    # Get configuration
    base_url = url or os.getenv("UBOX_BASE_URL", "http://10.11.10.173:8080")
    project_id = project or os.getenv("UBOX_PROJECT_ID", "")

    if not project_id:
        click.echo("Error: Project ID is required. Use --project or set UBOX_PROJECT_ID environment variable.", err=True)
        sys.exit(1)

    # Initialize analyzer
    analyzer = ProfilerAnalyzer(
        base_url=base_url,
        project_id=project_id,
        enable_cache=not no_cache,
        platform=platform.lower()
    )

    if verbose:
        click.echo(f"Analyzing case: {case_id}")
        click.echo(f"Platform: {platform.upper()} (thresholds: {('PC game' if platform == 'pc' else 'Mobile game')})")

    try:
        if overview:
            # Overview analysis
            click.echo("Performing overview analysis...")
            result = analyzer.analyze_overview(case_id, use_cache=not no_cache)

        elif hotspots:
            # Hotspot analysis
            click.echo(f"Performing hotspot analysis (Top {top})...")
            exclude_unity_entry = not include_unity_entry  # Default is to exclude
            if exclude_unity_entry:
                click.echo("Excluding Unity entry point functions...")
            result = analyzer.analyze_hotspots(
                case_id,
                top_n=top,
                sort_by=sort_by,
                min_calls=min_calls,
                exclude_unity_entry=exclude_unity_entry,
                use_cache=not no_cache
            )

        elif jank:
            # Jank frame analysis (default: AI-Ready mode with Python preprocessing)
            if raw:
                # Raw mode: output original data without preprocessing
                click.echo(f"Performing jank frame analysis in RAW mode (Top {top})...")
                click.echo("Note: Using raw data mode. Pattern recognition and recommendations will NOT be generated.")
                result = analyzer.analyze_janks(
                    case_id,
                    top_n=top,
                    analyze_details=True,
                    use_cache=not no_cache
                )
            else:
                # AI-Ready mode (default): Python preprocessing + enhanced output
                click.echo(f"Performing jank frame analysis with AI-Ready preprocessing (Top {top})...")
                click.echo("Pattern recognition, severity classification, and recommendations enabled...")
                result = analyzer.analyze_janks_with_ai_ready(
                    case_id,
                    top_n=top,
                    analyze_details=True,
                    use_cache=not no_cache,
                    with_screenshots=with_screenshots,
                    project_id=project_id,
                    use_timestamp_screenshots=use_timestamp_screenshots,
                    max_screenshot_offset_seconds=max_screenshot_offset
                )

        else:
            click.echo("Error: Please specify an analysis type (--overview, --hotspots, or --jank)", err=True)
            sys.exit(1)

        # Output data as JSON (for AI agent consumption)
        json_output = json.dumps(result, indent=2, ensure_ascii=False)

        if output:
            with open(output, "w", encoding="utf-8") as f:
                f.write(json_output)
            click.echo(f"Data saved to: {output}")
            click.echo("Use this JSON data with AI agent to generate Markdown report.")
        else:
            click.echo("\n" + json_output)
            click.echo("\nTip: Use --output to save to file, then provide to AI agent for report generation.")

        # Show cache stats
        if verbose:
            stats = analyzer.get_cache_stats()
            click.echo(f"\nCache stats: {stats}")

    except Exception as e:
        click.echo(f"Error during analysis: {e}", err=True)
        if verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)


@cli.command()
@click.argument("case_id_a", type=str)
@click.argument("case_id_b", type=str)
@click.option("--output", "-o", type=click.Path(), help="Output file path")
@click.option("--url", type=str, help="API base URL")
@click.option("--project", type=str, help="Project ID (APPKEY)")
@click.option("--no-cache", is_flag=True, help="Disable cache")
@click.option("--hotspot-top", type=int, default=100, help="Number of top hotspot functions to compare (default: 100)")
@click.option("--verbose", "-v", is_flag=True, help="Verbose output")
def compare(case_id_a, case_id_b, output, url, project, no_cache, hotspot_top, verbose):
    """Compare two performance reports (JSON output with AI-ready preprocessing)

    Examples:

        # Basic comparison
        python scripts/cli.py compare <case_id_a> <case_id_b>

        # Save to file
        python scripts/cli.py compare <case_id_a> <case_id_b> -o comparison.json

        # With more hotspot functions
        python scripts/cli.py compare <case_id_a> <case_id_b> --hotspot-top 200 -o comparison.json

        # Complete example
        python scripts/cli.py compare \\
          bf909275-f505-11f0-9e9c-708bcdbcb7b1 \\
          9198642d-f8cd-11f0-ad85-f8b156e0c103 \\
          --project pcavpt6w \\
          -o comparison.json
    """
    # Get configuration
    base_url = url or os.getenv("UBOX_BASE_URL", "http://10.11.10.173:8080")
    project_id = project or os.getenv("UBOX_PROJECT_ID", "")

    if not project_id:
        click.echo("Error: Project ID is required. Use --project or set UBOX_PROJECT_ID environment variable.", err=True)
        sys.exit(1)

    # Initialize analyzer
    analyzer = ProfilerAnalyzer(
        base_url=base_url,
        project_id=project_id,
        enable_cache=not no_cache
    )

    if verbose:
        click.echo(f"Comparing reports: {case_id_a} vs {case_id_b}")

    try:
        # Perform comparison analysis with AI-ready preprocessing
        if verbose:
            click.echo("Performing comparison analysis with AI-ready preprocessing...")

        result = analyzer.compare_reports(
            case_id_a,
            case_id_b,
            use_cache=not no_cache,
            hotspot_top_n=hotspot_top
        )

        # Output data as JSON (for AI agent consumption)
        json_output = json.dumps(result, indent=2, ensure_ascii=False)

        if output:
            with open(output, "w", encoding="utf-8") as f:
                f.write(json_output)
            click.echo(f"Data saved to: {output}")
            click.echo("Use this JSON data with AI agent to generate comparison report.")
        else:
            click.echo("\n" + json_output)
            click.echo("\nTip: Use --output to save to file, then provide to AI agent for report generation.")

        # Show cache stats
        if verbose:
            stats = analyzer.get_cache_stats()
            click.echo(f"\nCache stats: {stats}")

    except Exception as e:
        click.echo(f"Error during comparison: {e}", err=True)
        if verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)


@cli.command()
@click.option("--case-id", type=str, help="Filter by case ID")
@click.option("--case-name", type=str, help="Filter by case name (supports substring matching)")
@click.option("--device-name", type=str, help="Filter by device name (supports substring matching)")
@click.option("--start-time", type=str, help="Start time (format: YYYY-MM-DD HH:mm:ss)")
@click.option("--end-time", type=str, help="End time (format: YYYY-MM-DD HH:mm:ss)")
@click.option("--page", type=int, default=1, help="Page number (default: 1)")
@click.option("--page-size", type=int, default=20, help="Page size (default: 20)")
@click.option("--order-by", type=str, help="Order by field (e.g., createTime, updateTime)")
@click.option("--order-type", type=click.Choice(["asc", "desc"]), help="Order type (asc or desc)")
@click.option("--url", type=str, help="API base URL")
@click.option("--project", type=str, help="Project ID (APPKEY)")
@click.option("--output", "-o", type=click.Path(), help="Output file path")
@click.option("--format", type=click.Choice(["table", "json", "csv"]), default="table", help="Output format")
@click.option("--regex", is_flag=True, help="Use regex matching for case-name and device-name")
@click.option("--verbose", "-v", is_flag=True, help="Verbose output")
def list_reports(case_id, case_name, device_name, start_time, end_time, page, page_size, order_by, order_type, url, project, output, format, regex, verbose):
    """List and search profiler reports with local filtering

    This command fetches ALL reports within the time range first, then applies
    local filtering for case_name and device_name. This supports fuzzy matching
    and more flexible search criteria.

    Examples:

        # List all reports (no time filter - may return ALL reports!)
        python scripts/cli.py list-reports --project pcavpt6w

        # Filter by time range (recommended)
        python scripts/cli.py list-reports --project pcavpt6w --start-time "2026-01-01 00:00:00" --end-time "2026-01-31 23:59:59"

        # Filter by case name (substring match)
        python scripts/cli.py list-reports --project pcavpt6w --start-time "2026-01-01 00:00:00" --case-name "登录"

        # Filter by device name (substring match)
        python scripts/cli.py list-reports --project pcavpt6w --start-time "2026-01-01 00:00:00" --device-name "Xiaomi"

        # Combined filters
        python scripts/cli.py list-reports --project pcavpt6w \\
          --start-time "2026-01-01 00:00:00" \\
          --end-time "2026-01-31 23:59:59" \\
          --case-name "登录" \\
          --device-name "iPhone" \\
          --page-size 50

        # Use regex matching
        python scripts/cli.py list-reports --project pcavpt6w \\
          --start-time "2026-01-01 00:00:00" \\
          --case-name ".*TDR.*雨天.*" \\
          --regex

        # Output as JSON
        python scripts/cli.py list-reports --project pcavpt6w --format json -o reports.json

        # Order by creation time (newest first)
        python scripts/cli.py list-reports --project pcavpt6w --start-time "2026-01-01 00:00:00" --order-by createTime --order-type desc

    NOTE: Always use --start-time and --end-time to limit the search range!
    Without time filters, this command will fetch ALL reports in the project.
    """
    # Get configuration
    base_url = url or os.getenv("UBOX_BASE_URL", "http://10.11.10.173:8080")
    project_id = project or os.getenv("UBOX_PROJECT_ID", "")

    if not project_id:
        click.echo("Error: Project ID is required. Use --project or set UBOX_PROJECT_ID environment variable.", err=True)
        sys.exit(1)

    # Initialize client
    from ubox_profiler import UboxProfilerClient
    client = UboxProfilerClient(base_url=base_url, project_id=project_id)

    if verbose:
        click.echo(f"[*] Fetching reports for project: {project_id}")
        click.echo(f"    Time range: {start_time or 'None'} to {end_time or 'None'}")
        if case_name:
            match_type = "regex" if regex else "substring"
            click.echo(f"    Case name filter ({match_type}): {case_name}")
        if device_name:
            match_type = "regex" if regex else "substring"
            click.echo(f"    Device name filter ({match_type}): {device_name}")

    try:
        # Step 1: Fetch ALL reports within time range (no case_name/device_name filter)
        # The API will return all reports in the time range
        if verbose:
            click.echo("[*] Fetching report list from API...")

        api_result = client.report.get_report_list(
            start_time=start_time,
            end_time=end_time,
        )

        if api_result.get("code") != 0:
            click.echo(f"Error: {api_result.get('msg', 'Unknown error')}", err=True)
            sys.exit(1)

        # Step 2: Get all reports from API response
        data = api_result.get("data", [])

        # Handle both array and paginated object responses
        if isinstance(data, list):
            all_reports = data
        else:
            # Paginated response
            all_reports = data.get("list", [])

        if verbose:
            click.echo(f"[*] Fetched {len(all_reports)} reports from API")

        # Step 3: Apply local filtering
        filtered_reports = []

        for report in all_reports:
            # Filter by case_id (exact match)
            if case_id:
                report_uuid = report.get("UUID", report.get("caseId", ""))
                if case_id.lower() not in report_uuid.lower():
                    continue

            # Filter by case_name (smart keyword matching or regex)
            if case_name:
                report_case_name = report.get("CaseName", report.get("caseName", ""))
                if not report_case_name or report_case_name == "None":
                    continue

                if regex:
                    import re
                    try:
                        if not re.search(case_name, report_case_name, re.IGNORECASE):
                            continue
                    except re.error as e:
                        click.echo(f"Error: Invalid regex pattern: {e}", err=True)
                        sys.exit(1)
                else:
                    # Smart keyword matching: Extract keywords from input and match ALL
                    # This ensures "载具跑图（TDR）（雨天）" matches "Vehicle Run (TDR) - Rain" not "Vehicle Run (TDR) - Clear"

                    # Strategy 1: Try exact substring match first
                    if case_name.lower() in report_case_name.lower():
                        # Direct substring match - use this report
                        pass
                    else:
                        # Strategy 2: Extract keywords and build Chinese-English mapping
                        import re as keyword_re

                        # Step 2a: Extract English keywords and abbreviations
                        keywords = keyword_re.findall(r'\b[A-Z][A-Za-z0-9]+\b', case_name)
                        keywords.extend(keyword_re.findall(r'\b[A-Z]{2,}\b', case_name))

                        # Step 2b: Extract Chinese keywords (characters in parentheses)
                        # Patterns like "（雨天）", "（晴天）", "（登录）" etc.
                        chinese_keywords = keyword_re.findall(r'[（(]([^）)]+)[）)]', case_name)

                        # Step 2c: Build Chinese-English keyword mapping
                        cn_en_mapping = {
                            '雨天': 'Rain',
                            '雨': 'Rain',
                            '晴天': 'Clear',
                            '晴': 'Clear',
                            '阴天': 'Cloudy',
                            '登录': 'Login',
                            '登入': 'Login',
                            '跑图': 'Run',
                            '载具': 'Vehicle',
                            '车辆': 'Vehicle',
                            '战斗': 'Combat',
                            '建造': 'Build',
                            '建造中': 'Building',
                        }

                        # Map Chinese keywords to English
                        for cn_kw in chinese_keywords:
                            if cn_kw in cn_en_mapping:
                                en_kw = cn_en_mapping[cn_kw]
                                if en_kw not in keywords:
                                    keywords.append(en_kw)

                        if keywords:
                            # Remove duplicates while preserving order
                            seen = set()
                            unique_keywords = []
                            for kw in keywords:
                                if kw not in seen and len(kw) > 1:  # Only keep keywords > 1 char
                                    seen.add(kw)
                                    unique_keywords.append(kw)

                            # Check if ALL keywords exist in the report case name
                            all_matched = True
                            for keyword in unique_keywords:
                                if keyword.lower() not in report_case_name.lower():
                                    all_matched = False
                                    break

                            if not all_matched:
                                continue
                        else:
                            # No keywords found, use original substring matching
                            if case_name.lower() not in report_case_name.lower():
                                continue

            # Filter by device_name (substring or regex match)
            if device_name:
                device = report.get("Device", {})

                # Extract device name from nested structure
                device_model = ""
                if isinstance(device, dict):
                    # Try to get device name from various possible fields
                    # New structure: Device.DeviceName.value
                    device_name_obj = device.get("DeviceName", {})
                    if isinstance(device_name_obj, dict) and "value" in device_name_obj:
                        device_model = device_name_obj.get("value", "")

                    # Legacy structure: Device.deviceModel or Device.deviceName
                    if not device_model:
                        device_model = device.get("deviceModel", device.get("deviceName", ""))
                else:
                    device_model = str(device) if device else ""

                if not device_model or device_model == "N/A" or device_model == "None":
                    continue

                if regex:
                    import re
                    try:
                        if not re.search(device_name, device_model, re.IGNORECASE):
                            continue
                    except re.error as e:
                        click.echo(f"Error: Invalid regex pattern: {e}", err=True)
                        sys.exit(1)
                else:
                    # Substring matching (case-insensitive)
                    if device_name.lower() not in device_model.lower():
                        continue

            # Report passed all filters
            filtered_reports.append(report)

        if verbose:
            click.echo(f"[*] Filtered to {len(filtered_reports)} matching reports")

        # Step 4: Apply sorting if requested
        if order_by:
            reverse = (order_type == "desc")
            filtered_reports.sort(
                key=lambda r: str(r.get(order_by, "")),
                reverse=reverse
            )

        # Step 5: Apply pagination
        total = len(filtered_reports)
        start_idx = (page - 1) * page_size
        end_idx = start_idx + page_size
        paginated_reports = filtered_reports[start_idx:end_idx]

        # Step 6: Format output
        if format == "json":
            output_data = {
                "total": total,
                "page": page,
                "page_size": page_size,
                "filters": {
                    "case_id": case_id,
                    "case_name": case_name,
                    "device_name": device_name,
                    "start_time": start_time,
                    "end_time": end_time,
                    "regex": regex
                },
                "reports": paginated_reports
            }
            json_output = json.dumps(output_data, indent=2, ensure_ascii=False)

            if output:
                with open(output, "w", encoding="utf-8") as f:
                    f.write(json_output)
                click.echo(f"Report list saved to: {output}")
            else:
                click.echo(json_output)

        elif format == "csv":
            import csv
            import io

            fieldnames = []

            if paginated_reports:
                # Get fieldnames from first report
                fieldnames = list(paginated_reports[0].keys())

            if output:
                with open(output, "w", encoding="utf-8", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=fieldnames)
                    writer.writeheader()
                    writer.writerows(paginated_reports)
                click.echo(f"CSV report saved to: {output}")
            else:
                # Output to stdout
                output_str = io.StringIO()
                writer = csv.DictWriter(output_str, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(paginated_reports)
                click.echo(output_str.getvalue())

        else:  # table format (default)
            click.echo(f"\n[*] Report List (Page {page}, Total: {total} matched)")
            if case_name or device_name or start_time or end_time:
                click.echo("    Filters applied:")
                if start_time:
                    click.echo(f"      - From: {start_time}")
                if end_time:
                    click.echo(f"      - To: {end_time}")
                if case_name:
                    click.echo(f"      - Case name: {case_name}")
                if device_name:
                    click.echo(f"      - Device name: {device_name}")
            click.echo("=" * 110)

            if paginated_reports:
                # Display table header
                headers = ["No.", "Case ID (UUID)", "Case Name", "Device", "Create Time", "Status"]
                click.echo(f"{headers[0]:<6} {headers[1]:<38} {headers[2]:<18} {headers[3]:<18} {headers[4]:<20} {headers[5]}")
                click.echo("-" * 110)

                # Display rows
                for i, report in enumerate(paginated_reports, 1):
                    case_id_str = str(report.get("UUID", report.get("caseId", "N/A")))[:36]
                    case_name_str = str(report.get("CaseName", report.get("caseName", "N/A")))[:16]

                    # Get device info
                    device = report.get("Device", {})
                    if isinstance(device, dict):
                        # New structure: Device.DeviceName.value
                        device_name_obj = device.get("DeviceName", {})
                        if isinstance(device_name_obj, dict) and "value" in device_name_obj:
                            device_info = device_name_obj.get("value", "N/A")
                        else:
                            # Legacy structure: Device.deviceModel or Device.deviceName
                            device_info = device.get("deviceModel", device.get("deviceName", "N/A"))
                    else:
                        device_info = "N/A"
                    device_info = str(device_info)[:16]

                    # Get time
                    create_time = str(report.get("Time", report.get("createTime", "N/A")))[:18]

                    # Get status
                    status = report.get("Status", "N/A")
                    status_str = "[Done]" if status == 2 else f"[{status}]"

                    click.echo(f"{i:<6} {case_id_str:<38} {case_name_str:<18} {device_info:<18} {create_time:<20} {status_str}")

                # Show Case ID copy hints
                click.echo("\n[*] Tip: Copy Case ID (UUID) for analysis:")
                for i, report in enumerate(paginated_reports[:5], 1):
                    click.echo(f"   {i}. {report.get('UUID', report.get('caseId', 'N/A'))}")

            else:
                click.echo("No reports found matching the criteria.")

            # Show pagination info
            if page_size > 0:
                total_pages = (total + page_size - 1) // page_size
                click.echo(f"\nPage {page} of {total_pages} (showing {len(paginated_reports)} of {total} matched reports)")

            # Show next/prev page hints
            if page < total_pages:
                click.echo(f"\n[*] Tip: Use --page {page + 1} to see next page")
            if page > 1:
                click.echo(f"[*] Tip: Use --page {page - 1} to see previous page")

    except Exception as e:
        click.echo(f"Error fetching report list: {e}", err=True)
        if verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)
    finally:
        client.close()


@cli.command()
def cache():
    """Cache management commands"""
    click.echo("Cache management is for internal use only.")


def main():
    """Main entry point"""
    cli()


if __name__ == "__main__":
    main()
