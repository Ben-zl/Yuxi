#!/usr/bin/env python
"""Unity Profiler Analyzer - Test runner"""
import sys
import os

# Get the scripts directory
scripts_dir = os.path.dirname(os.path.abspath(__file__))

# Add to path
if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)

# Now import cli
import cli

if __name__ == '__main__':
    cli.cli()
