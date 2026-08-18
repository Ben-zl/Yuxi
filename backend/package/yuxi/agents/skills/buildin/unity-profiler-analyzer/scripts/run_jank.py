#!/usr/bin/env python
import sys
import os
import json

# Add paths
scripts_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(scripts_dir)
sys.path.insert(0, scripts_dir)

# Add utils directory for ubox_profiler
utils_candidates = [
    os.path.join(parent_dir, '../../utils'),
    os.path.join(parent_dir, '../utils'),
    os.path.join(parent_dir, '../../../Agent-skills/utils'),
]
for utils_path in utils_candidates:
    ubox_api = os.path.join(utils_path, 'ubox-api')
    if os.path.exists(ubox_api):
        sys.path.insert(0, ubox_api)
        break

# Now import using absolute imports
from analyzer import ProfilerAnalyzer

# Initialize analyzer
analyzer = ProfilerAnalyzer(
    base_url="http://10.11.10.173:8080",
    project_id="pcavpt6w",
    enable_cache=True
)

# Run jank analysis
case_id = "bf909275-f505-11f0-9e9c-708bcdbcb7b1"
print(f"Performing jank frame analysis (Top 30)...")

result = analyzer.analyze_janks(
    case_id,
    top_n=30,
    analyze_details=True,
    use_cache=True
)

# Output data as JSON
json_output = json.dumps(result, indent=2, ensure_ascii=False)
output_file = '/tmp/jank_data.json'
with open(output_file, "w", encoding="utf-8") as f:
    f.write(json_output)

print(f"Data saved to: {output_file}")
