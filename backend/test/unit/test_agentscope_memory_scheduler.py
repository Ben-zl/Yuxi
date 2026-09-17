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


async def test_scheduler_dream_passes_cross_session_deduplication_hint(tmp_path):
    """Dream 必须显式合并跨 Session 证据并排除临时 pending 状态。"""
    scheduler = MemoryDreamScheduler(registry=SimpleNamespace(), base_dir=tmp_path)
    scheduler._record_result = AsyncMock()
    maintenance = SimpleNamespace(run_job=AsyncMock(return_value=SimpleNamespace(success=True)))

    await scheduler._run_date("u", "a", date(2026, 9, 1), maintenance)

    dream_call, reindex_call = maintenance.run_job.await_args_list
    assert dream_call.args == ("auto_dream",)
    assert dream_call.kwargs["date"] == "2026-09-01"
    assert "跨 Session" in dream_call.kwargs["hint"]
    assert "pending" in dream_call.kwargs["hint"]
    assert "合并" in dream_call.kwargs["hint"]
    assert reindex_call.args == ("reindex",)


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


async def test_scheduler_skips_disabled_scope_but_processes_enabled_scope(tmp_path, monkeypatch):
    """同次扫描中关闭 Memory 的 scope 不调度，其他启用 scope 仍正常执行。"""
    scheduler = MemoryDreamScheduler(registry=SimpleNamespace(), base_dir=tmp_path)
    scheduler._process_scope = AsyncMock()
    records = [
        SimpleNamespace(
            uid="disabled",
            agent_slug="a",
            last_dream_date=date(2026, 8, 30),
            last_memory_at=None,
            dream_status=None,
            dream_attempted_at=None,
            maintenance_department_id=None,
        ),
        SimpleNamespace(
            uid="enabled",
            agent_slug="a",
            last_dream_date=date(2026, 8, 30),
            last_memory_at=None,
            dream_status=None,
            dream_attempted_at=None,
            maintenance_department_id=11,
        ),
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

    monkeypatch.setattr(scheduler_module.pg_manager, "get_async_session_context", fake_session_context)
    monkeypatch.setattr(scheduler_module, "AgentMemoryScopeRepository", FakeRepository)
    monkeypatch.setattr(scheduler_module.UserConfig, "load", load_config)

    await scheduler.run_due_once(datetime(2026, 8, 31, 23, 30, tzinfo=ZoneInfo("Asia/Shanghai")))

    scheduler._process_scope.assert_awaited_once_with("enabled", "a", [date(2026, 8, 31)], 11)


async def test_scheduler_skips_scope_with_active_reply(tmp_path):
    """活动 reply 存在时 Dream 本轮跳过且不尝试取得维护租约。"""
    registry = SimpleNamespace(
        is_busy=AsyncMock(return_value=True),
        acquire_exclusive=AsyncMock(),
    )
    scheduler = MemoryDreamScheduler(registry=registry, base_dir=tmp_path)

    await scheduler._process_scope("u", "a", [date(2026, 8, 31)], 11)

    registry.acquire_exclusive.assert_not_called()


async def test_scheduler_missing_workspace_records_failure(tmp_path, monkeypatch):
    """catalog 指向的 Workspace 不存在时应记录失败，不能创建空目录后误报 Dream 成功。"""

    class FakeRegistry:
        async def is_busy(self, _uid, _agent_slug):
            return False

        @asynccontextmanager
        async def acquire_exclusive(self, _uid, _agent_slug):
            yield None

    @asynccontextmanager
    async def fake_session_context():
        yield object()

    scheduler = MemoryDreamScheduler(registry=FakeRegistry(), base_dir=tmp_path)
    scheduler._record_result = AsyncMock()
    monkeypatch.setattr(scheduler_module.pg_manager, "get_async_session_context", fake_session_context)
    monkeypatch.setattr(scheduler_module, "project_runtime", AsyncMock(return_value=object()))
    monkeypatch.setattr(scheduler_module, "build_memory_models", lambda _projection: (object(), object(), "v1"))

    await scheduler._process_scope("u", "a", [date(2026, 8, 31)], 11)

    scheduler._record_result.assert_awaited_once()
    args, kwargs = scheduler._record_result.await_args
    assert args == ("u", "a", "failed")
    assert "Workspace 不存在" in kwargs["error"]


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


async def test_scheduler_partial_catch_up_keeps_last_success_before_failure(tmp_path, monkeypatch):
    """多日补跑中途失败时，已成功日期应推进，后续日期留待下一轮重试。"""

    class FakeRegistry:
        async def is_busy(self, _uid, _agent_slug):
            return False

        @asynccontextmanager
        async def acquire_exclusive(self, _uid, _agent_slug):
            yield None

    class Response:
        success = True

    maintenance = SimpleNamespace(
        run_job=AsyncMock(side_effect=[Response(), Response(), RuntimeError("second day failed")]),
    )

    @asynccontextmanager
    async def fake_session_context():
        yield object()

    @asynccontextmanager
    async def fake_maintenance(**_kwargs):
        yield maintenance

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    scheduler = MemoryDreamScheduler(registry=FakeRegistry(), base_dir=tmp_path)
    scheduler._record_result = AsyncMock()
    monkeypatch.setattr(scheduler_module.pg_manager, "get_async_session_context", fake_session_context)
    monkeypatch.setattr(scheduler_module, "project_runtime", AsyncMock(return_value=object()))
    monkeypatch.setattr(scheduler_module, "build_memory_models", lambda _projection: (object(), object(), "v1"))
    monkeypatch.setattr(scheduler_module, "validate_memory_workspace", lambda *_args, **_kwargs: workspace)
    monkeypatch.setattr(scheduler_module, "reme_maintenance_app", fake_maintenance)

    await scheduler._process_scope(
        "u",
        "a",
        [date(2026, 8, 30), date(2026, 8, 31), date(2026, 9, 1)],
        11,
    )

    assert scheduler._record_result.await_args_list[0].args == (
        "u",
        "a",
        "completed",
    )
    assert scheduler._record_result.await_args_list[0].kwargs["dream_date"] == date(2026, 8, 30)
    assert scheduler._record_result.await_args_list[1].args == ("u", "a", "failed")
    assert "second day failed" in scheduler._record_result.await_args_list[1].kwargs["error"]
    assert maintenance.run_job.await_count == 3


async def _no_op_commit() -> None:
    return None


async def test_scheduler_unbound_scope_skips_dream_without_model(tmp_path, monkeypatch):
    """无维护部门绑定的旧 scope 跳过自动 Dream：写明确失败原因，不调用模型。"""
    scheduler = MemoryDreamScheduler(registry=SimpleNamespace(), base_dir=tmp_path)
    scheduler._process_scope = AsyncMock()
    failures = []
    record = SimpleNamespace(
        uid="unbound",
        agent_slug="a",
        last_dream_date=date(2026, 8, 30),
        last_memory_at=None,
        dream_status=None,
        dream_attempted_at=None,
        maintenance_department_id=None,
    )

    @asynccontextmanager
    async def fake_session_context():
        yield SimpleNamespace(commit=_no_op_commit)

    class FakeRepository:
        def __init__(self, _db):
            pass

        async def list_all(self):
            return [record]

        async def set_dream_result(self, _record, **kwargs):
            failures.append(kwargs)

    monkeypatch.setattr(scheduler_module.pg_manager, "get_async_session_context", fake_session_context)
    monkeypatch.setattr(scheduler_module, "AgentMemoryScopeRepository", FakeRepository)
    monkeypatch.setattr(
        scheduler_module.UserConfig,
        "load",
        AsyncMock(return_value=SimpleNamespace(schema=SimpleNamespace(enable_memory=True))),
    )

    await scheduler.run_due_once(datetime(2026, 8, 31, 23, 30, tzinfo=ZoneInfo("Asia/Shanghai")))

    scheduler._process_scope.assert_not_awaited()
    assert len(failures) == 1
    assert failures[0]["status"] == "failed"
    assert "未绑定维护部门" in failures[0]["error"]


async def test_scheduler_revoked_membership_records_failure_without_model(tmp_path, monkeypatch):
    """撤权后固定部门解析失败：Dream 记录失败原因，不调用模型。"""

    class FakeRegistry:
        async def is_busy(self, _uid, _agent_slug):
            return False

        @asynccontextmanager
        async def acquire_exclusive(self, _uid, _agent_slug):
            yield None

    @asynccontextmanager
    async def fake_session_context():
        yield object()

    scheduler = MemoryDreamScheduler(registry=FakeRegistry(), base_dir=tmp_path)
    scheduler._record_result = AsyncMock()
    model_calls = []
    monkeypatch.setattr(scheduler_module.pg_manager, "get_async_session_context", fake_session_context)
    monkeypatch.setattr(
        scheduler_module,
        "project_runtime",
        AsyncMock(side_effect=ValueError("运行部门上下文无效: 非目标部门成员")),
    )
    monkeypatch.setattr(
        scheduler_module,
        "build_memory_models",
        lambda _projection: model_calls.append("model") or (object(), object(), "v1"),
    )

    await scheduler._process_scope("u", "a", [date(2026, 8, 31)], 11)

    scheduler._record_result.assert_awaited_once()
    args, kwargs = scheduler._record_result.await_args
    assert args == ("u", "a", "failed")
    assert "非目标部门成员" in kwargs["error"]
    assert model_calls == []
