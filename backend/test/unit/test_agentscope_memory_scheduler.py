"""ReMe 每日 Dream 调度日期与失败状态测试。"""

from datetime import date, datetime
from types import SimpleNamespace
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from yuxi.agentscope.memory_scheduler import MemoryDreamScheduler
from yuxi.agentscope import memory_scheduler as scheduler_module

pytestmark = pytest.mark.unit


def test_scheduler_target_date_changes_at_2315(tmp_path):
    """23:15 前只处理昨天，之后允许处理当天。"""
    scheduler = MemoryDreamScheduler(registry=SimpleNamespace(), base_dir=tmp_path)
    timezone = ZoneInfo("Asia/Shanghai")

    assert scheduler._target_date(datetime(2026, 9, 1, 23, 14, tzinfo=timezone)) == date(2026, 8, 31)
    assert scheduler._target_date(datetime(2026, 9, 1, 23, 15, tzinfo=timezone)) == date(2026, 9, 1)


def test_scheduler_catch_up_is_limited_to_seven_days(tmp_path):
    """停机补跑从上次成功日后开始，单轮最多七天。"""
    scheduler = MemoryDreamScheduler(registry=SimpleNamespace(), base_dir=tmp_path)
    record = SimpleNamespace(last_dream_date=date(2026, 8, 20), last_memory_at=None)

    assert scheduler._due_dates(record, date(2026, 8, 31)) == [
        date(2026, 8, 21),
        date(2026, 8, 22),
        date(2026, 8, 23),
        date(2026, 8, 24),
        date(2026, 8, 25),
        date(2026, 8, 26),
        date(2026, 8, 27),
    ]


def test_scheduler_first_date_uses_memory_timestamp_in_shanghai(tmp_path):
    """首次 Dream 从实际记忆时间对应的上海日期开始。"""
    scheduler = MemoryDreamScheduler(registry=SimpleNamespace(), base_dir=tmp_path)
    record = SimpleNamespace(
        last_dream_date=None,
        last_memory_at=datetime(2026, 8, 31, 16, 30),
    )

    assert scheduler._due_dates(record, date(2026, 9, 2))[0] == date(2026, 9, 1)


async def test_scheduler_failed_job_records_failure_once(tmp_path):
    """单个 Dream 失败由 scope 边界统一记录，避免重复持久化。"""
    scheduler = MemoryDreamScheduler(registry=SimpleNamespace(), base_dir=tmp_path)
    scheduler._record_result = AsyncMock()
    maintenance = SimpleNamespace(run_job=AsyncMock(side_effect=RuntimeError("dream failed")))

    with pytest.raises(RuntimeError, match="dream failed"):
        await scheduler._run_date("u", "a", date(2026, 8, 31), maintenance)

    scheduler._record_result.assert_not_awaited()


async def test_scheduler_retry_window_accepts_aware_database_timestamp(tmp_path, monkeypatch):
    """TIMESTAMPTZ 返回 aware datetime 时，一小时失败退避不能发生时区类型错误。"""
    scheduler = MemoryDreamScheduler(registry=SimpleNamespace(), base_dir=tmp_path)
    scheduler._process_scope = AsyncMock()
    attempted_at = datetime(2026, 8, 31, 14, 30, tzinfo=ZoneInfo("UTC"))
    record = SimpleNamespace(
        uid="u",
        agent_slug="a",
        last_dream_date=date(2026, 8, 30),
        last_memory_at=attempted_at,
        dream_status="failed",
        dream_attempted_at=attempted_at,
    )

    @asynccontextmanager
    async def fake_session_context():
        yield object()

    class FakeRepository:
        def __init__(self, _db):
            pass

        async def list_all(self):
            return [record]

    monkeypatch.setattr(scheduler_module.pg_manager, "get_async_session_context", fake_session_context)
    monkeypatch.setattr(scheduler_module, "AgentMemoryScopeRepository", FakeRepository)
    monkeypatch.setattr(
        scheduler_module.UserConfig,
        "load",
        AsyncMock(return_value=SimpleNamespace(schema=SimpleNamespace(enable_memory=True))),
    )
    monkeypatch.setattr(
        scheduler_module,
        "utc_now",
        lambda: datetime(2026, 8, 31, 15, 0, tzinfo=ZoneInfo("UTC")),
        raising=False,
    )

    await scheduler.run_due_once(datetime(2026, 8, 31, 23, 30, tzinfo=ZoneInfo("Asia/Shanghai")))

    scheduler._process_scope.assert_not_awaited()


async def test_scheduler_skips_scope_with_active_reply(tmp_path):
    """活动 reply 存在时 Dream 本轮跳过且不尝试取得维护租约。"""
    registry = SimpleNamespace(
        is_busy=AsyncMock(return_value=True),
        acquire_exclusive=AsyncMock(),
    )
    scheduler = MemoryDreamScheduler(registry=registry, base_dir=tmp_path)

    await scheduler._process_scope("u", "a", [date(2026, 8, 31)])

    registry.acquire_exclusive.assert_not_called()


async def test_scheduler_dream_job_times_out(tmp_path):
    """单个 Dream 超时必须暴露给 scope 边界记录失败。"""
    scheduler = MemoryDreamScheduler(
        registry=SimpleNamespace(),
        base_dir=tmp_path,
        job_timeout_seconds=0.01,
    )

    async def never_finishes(*_args, **_kwargs):
        await __import__("asyncio").sleep(1)

    maintenance = SimpleNamespace(run_job=never_finishes)

    with pytest.raises(TimeoutError):
        await scheduler._run_date("u", "a", date(2026, 8, 31), maintenance)
