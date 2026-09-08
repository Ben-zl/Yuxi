"""Agent 删除与长期记忆补偿清理测试。"""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from yuxi.agentscope.client import AgentScopeServiceError
from yuxi.services import agent_cleanup_service


class FakeTaskRepository:
    references = 0
    events: list[str] = []

    def __init__(self, _db):
        pass

    async def count_active_references(self, _agent_id):
        return self.references

    async def detach_agent_references(self, _agent_id):
        self.events.append("references_detached")


class FakeAgentRepository:
    pending = None

    def __init__(self, _db):
        pass

    async def mark_deletion_pending(self, _agent_id):
        return self.pending

    async def claim_pending_deletions(self, *, limit):
        del limit
        return [SimpleNamespace(slug="agent-a"), SimpleNamespace(slug="agent-b")]


class FakeDb:
    def __init__(self):
        self.commit_count = 0

    async def commit(self):
        self.commit_count += 1


class FakeMemoryClient:
    pass


@pytest.mark.asyncio
async def test_delete_commits_tombstone_before_external_cleanup(monkeypatch):
    """删除用例必须先提交本地 tombstone，再尝试外部长期记忆清理。"""
    events: list[str] = []
    pending = SimpleNamespace(id=7, slug="custom-agent")

    class TaskRepo(FakeTaskRepository):
        async def detach_agent_references(self, _agent_id):
            events.append("references_detached")

    class AgentRepo(FakeAgentRepository):
        async def mark_deletion_pending(self, _agent_id):
            events.append("tombstone_marked")
            return pending

    class Db(FakeDb):
        async def commit(self):
            events.append("tombstone_committed")

    async def finalize(slug, client):
        del slug, client
        events.append("memory_cleared")
        return True

    monkeypatch.setattr(agent_cleanup_service, "AgentTaskRepository", TaskRepo)
    monkeypatch.setattr(agent_cleanup_service, "AgentRepository", AgentRepo)
    monkeypatch.setattr(agent_cleanup_service, "_try_finalize_pending_agent", finalize)

    result = await agent_cleanup_service.delete_agent_with_memory_cleanup(
        db=Db(), agent=SimpleNamespace(id=7), caller_uid="user-1", client=FakeMemoryClient()
    )

    assert events == ["tombstone_marked", "references_detached", "tombstone_committed", "memory_cleared"]
    assert result.cleanup_pending is False


@pytest.mark.asyncio
async def test_delete_keeps_tombstone_when_external_cleanup_fails(monkeypatch):
    """AgentScope 失败必须保留已提交 tombstone 并标记待补偿。"""
    pending = SimpleNamespace(id=7, slug="custom-agent")
    FakeAgentRepository.pending = pending
    monkeypatch.setattr(agent_cleanup_service, "AgentTaskRepository", FakeTaskRepository)
    monkeypatch.setattr(agent_cleanup_service, "AgentRepository", FakeAgentRepository)

    async def finalize(_slug, _client):
        raise AgentScopeServiceError("down", status_code=503)

    monkeypatch.setattr(agent_cleanup_service, "_try_finalize_pending_agent", finalize)
    db = FakeDb()
    result = await agent_cleanup_service.delete_agent_with_memory_cleanup(
        db=db, agent=SimpleNamespace(id=7), caller_uid="user-1", client=FakeMemoryClient()
    )

    assert db.commit_count == 1
    assert result.cleanup_pending is True


@pytest.mark.asyncio
async def test_reconciler_continues_after_one_slug_fails(monkeypatch):
    """单个 pending Agent 清理失败不得阻断其他 slug。"""

    @asynccontextmanager
    async def session_context():
        yield object()

    monkeypatch.setattr(agent_cleanup_service, "AgentRepository", FakeAgentRepository)
    monkeypatch.setattr(agent_cleanup_service.pg_manager, "get_async_session_context", session_context)
    calls = []

    async def finalize(slug, _client):
        calls.append(slug)
        if slug == "agent-a":
            raise AgentScopeServiceError("busy", status_code=409)
        return True

    monkeypatch.setattr(agent_cleanup_service, "_try_finalize_pending_agent", finalize)
    cleared = await agent_cleanup_service.reconcile_deleted_agent_memories(client=FakeMemoryClient())

    assert calls == ["agent-a", "agent-b"]
    assert cleared == ["agent-b"]


@pytest.mark.asyncio
async def test_pending_agent_is_not_finalized_while_active_writer_exists(monkeypatch):
    """活动 Run 或请求存在时不得清理 memory 或释放 slug tombstone。"""
    pending = SimpleNamespace(slug="agent-a", deletion_pending_at=object())

    class Db:
        async def scalar(self, _statement):
            return pending

    @asynccontextmanager
    async def session_context():
        yield Db()

    async def has_active(_db, _slug):
        return True

    class Client:
        async def clear_memory_agent(self, _uid, _slug):
            raise AssertionError("active writer must prevent cleanup")

    monkeypatch.setattr(agent_cleanup_service.pg_manager, "get_async_session_context", session_context)
    monkeypatch.setattr(agent_cleanup_service, "_has_active_writers", has_active)

    assert await agent_cleanup_service._try_finalize_pending_agent("agent-a", Client()) is False
