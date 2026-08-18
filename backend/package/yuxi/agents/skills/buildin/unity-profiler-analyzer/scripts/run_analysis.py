#!/usr/bin/env python
import sys
import os

scripts_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, scripts_dir)

from cli import main

sys.argv = ['cli.py', 'analyze', 'bf909275-f505-11f0-9e9c-708bcdbcb7b1', '--jank', '--top', '30', '--project', 'pcavpt6w', '-o', '/tmp/jank_data.json']

main()
