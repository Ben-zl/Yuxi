@echo off
REM Unity Profiler Analyzer - 便捷调用脚本
REM 使用方法: upa <command> [options]

cd /d "%~dp0"
python scripts\cli.py %*
