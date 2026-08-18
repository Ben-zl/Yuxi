"""Unity Profiler Analyzer - Comparison Analysis Module

This module provides functionality for comparing two performance reports.
It follows the 4-step workflow:
1. Data Export via cli.py
2. Pre-analysis via scripts (ComparisonCollector, ComparisonPreprocessor)
3. AI Analysis by AI agent
4. Report Generation by AI agent based on template

The key classes are:
- ComparisonCollector: Collects data for two reports using existing collectors
- ComparisonPreprocessor: Performs statistical analysis and computes deltas
- ComparisonReporter: Formats data as AI-ready JSON (NO Markdown generation)
"""

# Handle both relative and absolute imports
try:
    from .comparison_collector import ComparisonCollector
    from .comparison_preprocessor import ComparisonPreprocessor
    from .comparison_reporter import ComparisonReporter
except (ImportError, ValueError):
    # Absolute imports for direct execution
    from comparers.comparison_collector import ComparisonCollector
    from comparers.comparison_preprocessor import ComparisonPreprocessor
    from comparers.comparison_reporter import ComparisonReporter


__all__ = [
    "ComparisonCollector",
    "ComparisonPreprocessor",
    "ComparisonReporter",
]
