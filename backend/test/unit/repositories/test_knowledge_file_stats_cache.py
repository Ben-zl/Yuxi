from __future__ import annotations

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.repositories import knowledge_file_repository as repository_module
from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository
from yuxi.storage.postgres.models_knowledge import KnowledgeBase, KnowledgeFile

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


class _AsyncSessionContext:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, exc_type, *_args):
        if exc_type is None:
            await self.db.commit()
        else:
            await self.db.rollback()
        return False


class _FakeRedis:
    def __init__(self):
        self.values: dict[str, str] = {}
        self.deleted_keys: list[str] = []

    async def get(self, key: str):
        return self.values.get(key)

    async def set(self, key: str, value: str, *, ex: int):
        del ex
        self.values[key] = value

    async def delete(self, key: str):
        self.deleted_keys.append(key)
        self.values.pop(key, None)


@pytest_asyncio.fixture
async def repository_context(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(KnowledgeBase.__table__.create)
        await conn.run_sync(KnowledgeFile.__table__.create)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        monkeypatch.setattr(
            repository_module.pg_manager,
            "get_async_session_context",
            lambda: _AsyncSessionContext(session),
        )
        redis = _FakeRedis()

        async def get_fake_redis():
            return redis

        monkeypatch.setattr("yuxi.storage.redis.get_async_redis_client", get_fake_redis)
        session.add(KnowledgeBase(kb_id="kb_1", name="KB 1", description="", kb_type="milvus"))
        await session.commit()
        yield KnowledgeFileRepository(), session, redis

    await engine.dispose()


def _stale_stats(*, file_count: int, pending_parse_count: int = 0) -> dict[str, int]:
    return {
        "row_count": file_count,
        "file_count": file_count,
        "folder_count": 0,
        "total_size": 0,
        "chunk_count": 0,
        "token_count": 0,
        "pending_parse_count": pending_parse_count,
        "pending_index_count": 0,
        "processing_count": 0,
    }


async def test_upsert_invalidates_cached_stats_before_next_read(repository_context):
    repo, _session, redis = repository_context
    cache_key = "yuxi:kb_file_stats:kb_1"
    redis.values[cache_key] = json.dumps(_stale_stats(file_count=0))

    await repo.upsert(
        "file_1",
        {
            "kb_id": "kb_1",
            "filename": "report.pdf",
            "file_type": "pdf",
            "status": "uploaded",
            "is_folder": False,
            "file_size": 12,
        },
    )

    stats = await repo.get_kb_file_stats("kb_1")

    assert stats["file_count"] == 1
    assert stats["pending_parse_count"] == 1
    assert redis.deleted_keys == [cache_key]


async def test_delete_invalidates_cached_stats_before_next_read(repository_context):
    repo, session, redis = repository_context
    session.add(
        KnowledgeFile(
            file_id="file_1",
            kb_id="kb_1",
            filename="report.pdf",
            file_type="pdf",
            status="uploaded",
            is_folder=False,
        )
    )
    await session.commit()
    cache_key = "yuxi:kb_file_stats:kb_1"
    redis.values[cache_key] = json.dumps(_stale_stats(file_count=1, pending_parse_count=1))

    await repo.delete("file_1")
    stats = await repo.get_kb_file_stats("kb_1")

    assert stats["file_count"] == 0
    assert stats["pending_parse_count"] == 0
    assert redis.deleted_keys == [cache_key]


async def test_status_update_invalidates_pending_stats_before_next_read(repository_context):
    repo, session, redis = repository_context
    session.add(
        KnowledgeFile(
            file_id="file_1",
            kb_id="kb_1",
            filename="report.pdf",
            file_type="pdf",
            status="uploaded",
            is_folder=False,
        )
    )
    await session.commit()
    cache_key = "yuxi:kb_file_stats:kb_1"
    redis.values[cache_key] = json.dumps(_stale_stats(file_count=1, pending_parse_count=1))

    updated = await repo.update_fields_if_status(
        kb_id="kb_1",
        file_id="file_1",
        allowed_statuses={"uploaded"},
        data={"status": "indexed"},
    )
    stats = await repo.get_kb_file_stats("kb_1")

    assert updated is not None
    assert stats["file_count"] == 1
    assert stats["pending_parse_count"] == 0
    assert redis.deleted_keys == [cache_key]


async def test_failed_database_commit_only_drops_derived_stats_cache(monkeypatch):
    events: list[str] = []
    record = SimpleNamespace(file_id="file_1", kb_id="kb_1", status="uploaded")

    class _Result:
        def scalar_one_or_none(self):
            return record

    class _Session:
        def get_bind(self):
            return SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))

        async def execute(self, _statement):
            return _Result()

    @asynccontextmanager
    async def failing_session_context():
        yield _Session()
        events.append("commit_failed")
        raise RuntimeError("commit failed")

    async def record_invalidation(_kb_ids):
        events.append("cache_invalidated")

    monkeypatch.setattr(repository_module.pg_manager, "get_async_session_context", failing_session_context)
    repo = KnowledgeFileRepository()
    monkeypatch.setattr(repo, "_invalidate_kb_file_stats", record_invalidation)

    with pytest.raises(RuntimeError, match="commit failed"):
        await repo.update_fields(file_id="file_1", kb_id="kb_1", data={"status": "indexed"})

    assert events == ["cache_invalidated", "commit_failed"]


async def test_update_invalidates_cache_before_transaction_releases_stats_lock(monkeypatch):
    events: list[str] = []
    record = SimpleNamespace(file_id="file_1", kb_id="kb_1", status="uploaded")

    class _Result:
        def scalar_one_or_none(self):
            return record

    class _Session:
        def get_bind(self):
            return SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))

        async def execute(self, _statement):
            events.append("database_update")
            return _Result()

    @asynccontextmanager
    async def session_context():
        events.append("transaction_open")
        yield _Session()
        events.append("transaction_commit_and_lock_release")

    async def record_lock(_session, _kb_ids):
        events.append("stats_lock_acquired")

    async def record_invalidation(_kb_ids):
        events.append("cache_invalidated")

    monkeypatch.setattr(repository_module.pg_manager, "get_async_session_context", session_context)
    repo = KnowledgeFileRepository()
    monkeypatch.setattr(repo, "_lock_kb_file_stats", record_lock)
    monkeypatch.setattr(repo, "_invalidate_kb_file_stats", record_invalidation)

    await repo.update_fields(file_id="file_1", kb_id="kb_1", data={"status": "indexed"})

    assert events == [
        "transaction_open",
        "stats_lock_acquired",
        "database_update",
        "cache_invalidated",
        "transaction_commit_and_lock_release",
    ]


async def test_postgres_stats_lock_uses_stable_per_kb_advisory_keys():
    statements = []

    class _Session:
        def get_bind(self):
            return SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

        async def execute(self, statement):
            statements.append(str(statement.compile(compile_kwargs={"literal_binds": True})))

    await KnowledgeFileRepository()._lock_kb_file_stats(_Session(), {"kb_2", None, "kb_1"})

    assert len(statements) == 2
    assert "pg_advisory_xact_lock" in statements[0]
    assert "yuxi:kb_file_stats:kb_1" in statements[0]
    assert "yuxi:kb_file_stats:kb_2" in statements[1]
