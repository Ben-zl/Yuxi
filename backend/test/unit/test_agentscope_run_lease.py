"""AgentScope Run heartbeat 生命周期测试。"""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

from yuxi.agentscope import run_lease


async def test_renew_run_lease_commits_before_stopping_on_lost_ownership(monkeypatch):
    """每次成功续租必须提交；失去 owner 后 heartbeat 立即退出。"""

    db = SimpleNamespace(commit=AsyncMock())
    repository = SimpleNamespace(renew_lease=AsyncMock(side_effect=[True, False]))

    @asynccontextmanager
    async def _db_context():
        yield db

    monkeypatch.setattr(run_lease.pg_manager, "get_async_session_context", _db_context)
    monkeypatch.setattr(run_lease, "AgentRunRepository", lambda _db: repository)
    monkeypatch.setattr(run_lease.asyncio, "sleep", AsyncMock())

    await run_lease.renew_run_lease(
        "run-long",
        worker_id="team-worker",
        lease_seconds=120,
        heartbeat_seconds=30,
    )

    assert repository.renew_lease.await_count == 2
    repository.renew_lease.assert_any_await(
        "run-long",
        worker_id="team-worker",
        lease_seconds=120,
    )
    db.commit.assert_awaited_once()


async def test_start_run_lease_heartbeat_is_idempotent_per_run(monkeypatch):
    """同一进程内重复装配 Team middleware 不得创建第二个 heartbeat。"""

    blocker = AsyncMock()

    async def _renew(*_args, **_kwargs):
        await blocker()

    monkeypatch.setattr(run_lease, "renew_run_lease", _renew)
    first = run_lease.start_run_lease_heartbeat("run-one", worker_id="worker")
    second = run_lease.start_run_lease_heartbeat("run-one", worker_id="worker")
    assert first is second

    await run_lease.stop_run_lease_heartbeat("run-one")
    assert "run-one" not in run_lease._heartbeat_tasks
