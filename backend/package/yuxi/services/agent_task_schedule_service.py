"""AgentTask 定时规则：编译、校验与预览（任务中心工单 07）。

简单模式（每日/每周）编译为与高级模式相同的 5 段 POSIX Cron；
解析、预览与下次运行计算复用 APScheduler CronTrigger，
apscheduler 是 Yuxi 的直接依赖（父规格 #958）。
"""

from __future__ import annotations

from datetime import datetime, UTC
from typing import Any

from fastapi import HTTPException


MIN_SCHEDULE_INTERVAL_SECONDS = 300  # 最短间隔 5 分钟
PREVIEW_COUNT = 5
SCHEDULE_MODES = ("daily", "weekly", "cron")

WEEKDAY_NUMBERS = {
    "mon": 1,
    "tue": 2,
    "wed": 3,
    "thu": 4,
    "fri": 5,
    "sat": 6,
    "sun": 0,
}


def _parse_simple_to_cron(schedule: dict[str, Any]) -> str:
    """每日 HH:MM 或每周 [星期] HH:MM → 5 段 Cron（分 时 * * 星期）。"""
    mode = schedule.get("mode")
    time_value = str(schedule.get("time") or "")
    parts = time_value.split(":")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        raise HTTPException(status_code=422, detail="定时时间格式应为 HH:MM")
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise HTTPException(status_code=422, detail="定时时间超出有效范围")
    if mode == "daily":
        return f"{minute} {hour} * * *"
    weekdays = schedule.get("weekdays") or []
    if not weekdays:
        raise HTTPException(status_code=422, detail="每周定时至少选择一个星期")
    numbers: set[int] = set()
    for w in weekdays:
        w_str = str(w)
        if w_str.isdigit():
            num = int(w_str)
            if 0 <= num <= 6:
                numbers.add(num)
            else:
                numbers.add(-1)
        else:
            numbers.add(WEEKDAY_NUMBERS.get(w_str[:3].lower(), -1))
    if -1 in numbers:
        raise HTTPException(status_code=422, detail="无效的星期配置")
    dow = ",".join(str(n) for n in sorted(numbers))
    return f"{minute} {hour} * * {dow}"


def compile_schedule_cron(schedule: dict[str, Any]) -> str:
    """统一编译为 5 段 Cron；非法输入显式拒绝。"""
    mode = schedule.get("mode")
    if mode in ("daily", "weekly"):
        return _parse_simple_to_cron(schedule)
    if mode == "cron":
        cron = str(schedule.get("cron") or "").strip()
        if len(cron.split()) != 5:
            raise HTTPException(status_code=422, detail="Cron 表达式必须为 5 段（分 时 日 月 星期）")
        _build_trigger(cron, "UTC")
        return cron
    raise HTTPException(status_code=422, detail="定时模式只支持 daily/weekly/cron")


def _convert_dow_to_apscheduler(dow_expr: str) -> str:
    """将 POSIX Cron day-of-week 转为 APScheduler 约定。

    POSIX: 0=Sunday, 1=Monday, ..., 6=Saturday
    APScheduler from_crontab: 0=Monday, 1=Tuesday, ..., 6=Sunday
    转换: aps = (posix + 6) % 7
    """
    if dow_expr == "*":
        return "*"
    parts = dow_expr.split(",")
    converted = []
    for part in parts:
        if "-" in part and part.split("-", 1)[0].isdigit():
            start, end = part.split("-", 1)
            converted.append(f"{(int(start) + 6) % 7}-{(int(end) + 6) % 7}")
        elif part.lstrip("*/").isdigit():
            base = part.lstrip("*/")
            if part.startswith("*/"):
                converted.append(f"*/{(int(base) + 6) % 7}")
            else:
                converted.append(str((int(base) + 6) % 7))
        else:
            converted.append(part)
    return ",".join(converted)


def _build_trigger(cron: str, timezone_name: str):
    """构造 APScheduler CronTrigger；表达式或时区非法在此抛出。

    APScheduler 的 from_crontab 使用 0=Monday 约定，与 POSIX Cron（0=Sunday）
    不同；统一在构造前将 day-of-week 字段从 POSIX 转为 APScheduler 约定。
    """
    from apscheduler.triggers.cron import CronTrigger

    parts = cron.split()
    if len(parts) == 5:
        parts[4] = _convert_dow_to_apscheduler(parts[4])
        cron = " ".join(parts)
    try:
        return CronTrigger.from_crontab(cron, timezone=timezone_name)
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=f"无效的定时配置: {exc}") from exc
    except Exception as exc:
        from zoneinfo import ZoneInfoNotFoundError

        if isinstance(exc, ZoneInfoNotFoundError):
            raise HTTPException(status_code=422, detail=f"无效的时区: {timezone_name}") from exc
        raise


def _next_fire_times(cron: str, timezone_name: str, count: int, after: datetime | None = None) -> list[datetime]:
    """从 after（UTC，naive 视为 UTC，默认当前）起计算后续 count 次触发时间。

    持久化的 next_run_at 是 naive UTC：按本地时区解释会造成 8 小时级偏移，
    使停机补发把大量历史周期误记 missed。
    """
    trigger = _build_trigger(cron, timezone_name)
    if after is None:
        current = datetime.now(UTC)
    elif after.tzinfo is None:
        current = after.replace(tzinfo=UTC)
    else:
        current = after.astimezone(UTC)
    times: list[datetime] = []
    for _ in range(count * 48):  # 防御极端稀疏规则
        fire = trigger.get_next_fire_time(current, current)
        if fire is None:
            break
        times.append(fire)
        current = fire
        if len(times) >= count:
            break
    if len(times) < count:
        raise HTTPException(status_code=422, detail="无法计算足够的未来触发时间")
    return times


def validate_min_interval(cron: str) -> None:
    """拒绝快于每 5 分钟一次的周期。"""
    times = _next_fire_times(cron, "UTC", 2)
    if (times[1] - times[0]).total_seconds() < MIN_SCHEDULE_INTERVAL_SECONDS:
        raise HTTPException(status_code=422, detail="定时间隔不能小于 5 分钟")


def preview_schedule(schedule: dict[str, Any], timezone_name: str, count: int = PREVIEW_COUNT) -> list[str]:
    """预览未来 count 次本地时间（ISO 格式），供创建/编辑校验。"""
    cron = compile_schedule_cron(schedule)
    times = _next_fire_times(cron, timezone_name, count)
    return [t.isoformat() for t in times]


def apply_schedule_fields(payload: dict[str, Any]) -> dict[str, Any]:
    """把请求中的 schedule 配置转换为任务表字段；未启用返回空。"""
    schedule = payload.get("schedule")
    if not schedule or not schedule.get("enabled"):
        return {}
    timezone_name = str(schedule.get("timezone") or "UTC")
    _build_trigger("0 0 * * *", timezone_name)  # 校验 IANA 时区合法性
    cron = compile_schedule_cron(schedule)
    validate_min_interval(cron)
    next_fire = _next_fire_times(cron, timezone_name, 1)[0]
    mode = schedule.get("mode") if schedule.get("mode") in SCHEDULE_MODES else None
    return {
        "schedule_mode": mode,
        "schedule_cron": cron,
        "schedule_timezone": timezone_name,
        "next_run_at": next_fire.replace(tzinfo=None),
    }


def compute_next_run(cron: str, timezone_name: str, after: datetime | None = None) -> datetime:
    """计算下次运行时间（UTC naive，供扫描回写）。"""
    fire = _next_fire_times(cron, timezone_name, 1, after=after)[0]
    return fire.astimezone(UTC).replace(tzinfo=None)


def schedule_idempotency_key(task_id: str, scheduled_at_utc, timezone_name: str) -> str:
    """构造定时触发的幂等键。

    使用任务时区下的本地时间（date + time）而非 UTC 时间戳，确保秋季 DST
    重复的本地时刻（如 1:30 AM 出现两次）映射到同一幂等键，只执行一次。
    """
    from zoneinfo import ZoneInfo

    if scheduled_at_utc is None:
        return f"schedule:{task_id}:none"
    aware = scheduled_at_utc.replace(tzinfo=UTC) if scheduled_at_utc.tzinfo is None else scheduled_at_utc
    local = aware.astimezone(ZoneInfo(timezone_name))
    return f"schedule:{task_id}:{local.strftime('%Y-%m-%d %H:%M')}"
