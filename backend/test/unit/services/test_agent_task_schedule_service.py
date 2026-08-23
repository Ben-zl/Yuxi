"""AgentTask 定时规则纯时间计算单元测试（工单 07/08）。

覆盖 Cron 编译、最短间隔校验、下次运行计算和 DST 边界。
不依赖数据库或 Redis；仅校验时间计算行为。
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from yuxi.services.agent_task_schedule_service import (
    compile_schedule_cron,
    compute_next_run,
    preview_schedule,
    validate_min_interval,
)


class TestCompileScheduleCron:
    def test_daily(self):
        cron = compile_schedule_cron({"mode": "daily", "time": "09:30"})
        assert cron == "30 9 * * *"

    def test_weekly_integers(self):
        """前端发送整数星期值（POSIX: 1=Monday, 0=Sunday）。"""
        cron = compile_schedule_cron({"mode": "weekly", "time": "14:00", "weekdays": [1, 3]})
        assert cron == "0 14 * * 1,3"  # POSIX: 1=Mon, 3=Wed

    def test_weekly_sunday(self):
        cron = compile_schedule_cron({"mode": "weekly", "time": "00:00", "weekdays": [0]})
        assert cron == "0 0 * * 0"  # POSIX: 0=Sunday

    def test_weekly_abbreviations(self):
        """也接受字符串缩写。"""
        cron = compile_schedule_cron({"mode": "weekly", "time": "09:00", "weekdays": ["mon", "wed"]})
        assert cron == "0 9 * * 1,3"

    def test_cron_mode(self):
        cron = compile_schedule_cron({"mode": "cron", "cron": "*/5 * * * *"})
        assert cron == "*/5 * * * *"

    def test_invalid_time_format(self):
        with pytest.raises(HTTPException) as exc:
            compile_schedule_cron({"mode": "daily", "time": "25:00"})
        assert exc.value.status_code == 422

    def test_weekly_without_weekdays(self):
        with pytest.raises(HTTPException):
            compile_schedule_cron({"mode": "weekly", "time": "09:00", "weekdays": []})

    def test_cron_wrong_segments(self):
        with pytest.raises(HTTPException):
            compile_schedule_cron({"mode": "cron", "cron": "* * * *"})  # 4 段

    def test_invalid_mode(self):
        with pytest.raises(HTTPException):
            compile_schedule_cron({"mode": "hourly", "time": "09:00"})


class TestMinInterval:
    def test_reject_too_frequent(self):
        with pytest.raises(HTTPException) as exc:
            validate_min_interval("* * * * *")  # 每分钟
        assert "5 分钟" in exc.value.detail

    def test_accept_5_minutes(self):
        validate_min_interval("*/5 * * * *")  # 每 5 分钟，不抛异常


class TestNextRun:
    def test_daily_next_run(self):
        cron = "0 9 * * *"
        after = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)
        next_run = compute_next_run(cron, "UTC", after=after)
        assert next_run.hour == 9
        assert next_run.day == 20  # 第二天 09:00 UTC

    def test_weekly_next_run_monday(self):
        """POSIX cron 1=Monday；周三之后下一个周一是 Aug 24。"""
        cron = "0 14 * * 1"  # POSIX: 1=Monday
        after = datetime(2026, 8, 19, 0, 0, tzinfo=timezone.utc)  # 周三
        next_run = compute_next_run(cron, "UTC", after=after)
        assert next_run.weekday() == 0  # Monday
        assert next_run.hour == 14

    def test_weekly_next_run_sunday(self):
        """POSIX cron 0=Sunday；周三之后下一个周日是 Aug 23。"""
        cron = "0 10 * * 0"  # POSIX: 0=Sunday
        after = datetime(2026, 8, 19, 0, 0, tzinfo=timezone.utc)  # 周三
        next_run = compute_next_run(cron, "UTC", after=after)
        assert next_run.weekday() == 6  # Sunday
        assert next_run.hour == 10


class TestPreview:
    def test_preview_daily(self):
        preview = preview_schedule({"mode": "daily", "time": "09:00"}, "UTC")
        assert len(preview) == 5
        for t in preview:
            assert "T" in t

    def test_preview_weekly(self):
        preview = preview_schedule(
            {"mode": "weekly", "time": "10:00", "weekdays": [1]}, "UTC"
        )
        assert len(preview) == 5

    def test_preview_cron(self):
        preview = preview_schedule({"mode": "cron", "cron": "0 */2 * * *"}, "UTC")
        assert len(preview) == 5


class TestDST:
    """DST 边界：春跳过的不存在时刻跳到下一有效周期，秋重复只执行第一次。

    APScheduler 的 CronTrigger 按 IANA 时区计算，自动处理 DST。
    秋季重复时刻的去重在触发层由幂等键保证，不在纯时间计算层。
    """

    def test_spring_forward_skips_nonexistent(self):
        """美国 DST 春跳：2026-03-08 02:30 EST 不存在，不抛异常。"""
        cron = "30 2 * * *"
        tz = "America/New_York"
        after = datetime(2026, 3, 7, 12, 0, tzinfo=timezone.utc)
        from yuxi.services.agent_task_schedule_service import _next_fire_times
        times = _next_fire_times(cron, tz, 3, after=after)
        assert len(times) == 3  # 不抛异常，产生了 3 个有效时间

    def test_fall_back_produces_fire_times(self):
        """美国 DST 秋回：CronTrigger 仍产生 fire times；去重在触发层处理。"""
        cron = "30 1 * * *"
        tz = "America/New_York"
        after = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)
        from yuxi.services.agent_task_schedule_service import _next_fire_times
        times = _next_fire_times(cron, tz, 3, after=after)
        assert len(times) >= 1  # 至少产生了有效时间


class TestApplyScheduleFields:
    def test_disabled_returns_empty(self):
        from yuxi.services.agent_task_schedule_service import apply_schedule_fields
        result = apply_schedule_fields({"schedule": {"enabled": False}})
        assert result == {}

    def test_no_schedule_returns_empty(self):
        from yuxi.services.agent_task_schedule_service import apply_schedule_fields
        result = apply_schedule_fields({})
        assert result == {}

    def test_enabled_returns_fields(self):
        from yuxi.services.agent_task_schedule_service import apply_schedule_fields
        result = apply_schedule_fields({
            "schedule": {
                "enabled": True,
                "mode": "daily",
                "time": "09:00",
                "timezone": "UTC",
            }
        })
        assert result["schedule_mode"] == "daily"
        assert result["schedule_cron"] == "0 9 * * *"
        assert result["schedule_timezone"] == "UTC"
        assert result["next_run_at"] is not None

    def test_invalid_timezone_rejected(self):
        from yuxi.services.agent_task_schedule_service import apply_schedule_fields
        with pytest.raises(HTTPException) as exc:
            apply_schedule_fields({
                "schedule": {
                    "enabled": True,
                    "mode": "daily",
                    "time": "09:00",
                    "timezone": "Invalid/Zone",
                }
            })
        assert exc.value.status_code == 422
