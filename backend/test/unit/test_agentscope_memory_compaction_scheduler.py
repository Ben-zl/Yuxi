"""ReMe Daily 近重复归并调度测试。"""

from contextlib import asynccontextmanager
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from yuxi.agentscope import memory_compaction_scheduler as scheduler_module
from yuxi.agentscope.memory_compaction_scheduler import MemoryCompactionScheduler

pytestmark = pytest.mark.unit


async def test_compaction_scheduler_scans_enabled_scope_recent_seven_days(tmp_path, monkeypatch):
    """调度器只处理启用 Memory 的 scope，并限定最近七个日期目录。"""
    workspace = tmp_path / "workspace"
    for value in ("2026-08-24", "2026-08-26", "2026-09-01"):
        (workspace / "daily" / value).mkdir(parents=True)
    records = [
        SimpleNamespace(uid="enabled", agent_slug="agent"),
        SimpleNamespace(uid="disabled", agent_slug="agent"),
    ]

    @asynccontextmanager
    async def fake_session_context():
        yield object()

    class FakeRepository:
        def __init__(self, _db):
            pass

        async def list_all(self):
            return records

    async def load_config(_db, uid):
        return SimpleNamespace(schema=SimpleNamespace(enable_memory=uid == "enabled"))

    scheduler = MemoryCompactionScheduler(
        registry=SimpleNamespace(),
        memory_service=SimpleNamespace(),
        base_dir=tmp_path,
    )
    scheduler._process_scope = AsyncMock()
    monkeypatch.setattr(scheduler_module.pg_manager, "get_async_session_context", fake_session_context)
    monkeypatch.setattr(scheduler_module, "AgentMemoryScopeRepository", FakeRepository)
    monkeypatch.setattr(scheduler_module.UserConfig, "load", load_config)
    monkeypatch.setattr(scheduler_module, "validate_memory_workspace", lambda *_args: workspace)

    await scheduler.run_once(today=date(2026, 9, 1))

    scheduler._process_scope.assert_awaited_once_with(
        "enabled",
        "agent",
        ["2026-08-26", "2026-09-01"],
    )


async def test_compaction_scheduler_skips_active_scope(tmp_path):
    """存在活动 reply 时本轮跳过，不取得独占租约。"""
    registry = SimpleNamespace(is_busy=AsyncMock(return_value=True), acquire_exclusive=AsyncMock())
    scheduler = MemoryCompactionScheduler(
        registry=registry,
        memory_service=SimpleNamespace(),
        base_dir=tmp_path,
    )

    await scheduler._process_scope("u", "a", ["2026-09-01"])

    registry.acquire_exclusive.assert_not_called()


async def test_compaction_scheduler_uses_exclusive_lease_and_reindexes(tmp_path, monkeypatch):
    """归并写文件和 reindex 必须处于同一独占 scope 租约内。"""
    events = []

    class FakeRegistry:
        async def is_busy(self, _uid, _slug):
            return False

        @asynccontextmanager
        async def acquire_exclusive(self, _uid, _slug):
            events.append("lease-start")
            yield None
            events.append("lease-end")

    async def compact(_workspace, *, date, maintain_index):
        events.append(f"compact:{date}")
        await maintain_index(date)

    memory_service = SimpleNamespace(
        reindex_scope=AsyncMock(side_effect=lambda *_args, **_kwargs: events.append("reindex")),
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    scheduler = MemoryCompactionScheduler(
        registry=FakeRegistry(),
        memory_service=memory_service,
        base_dir=tmp_path,
    )
    monkeypatch.setattr(scheduler_module, "validate_memory_workspace", lambda *_args: workspace)
    monkeypatch.setattr(scheduler_module, "compact_daily_memories", compact)

    await scheduler._process_scope("u", "a", ["2026-09-01"])

    assert events == ["lease-start", "compact:2026-09-01", "reindex", "lease-end"]
