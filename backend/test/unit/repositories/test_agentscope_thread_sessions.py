"""AgentScope 线程映射 repository 测试。"""

from types import SimpleNamespace

import pytest

from yuxi.repositories import agentscope_thread_sessions as repository


@pytest.mark.asyncio
async def test_list_thread_sessions_skips_database_for_empty_thread_ids() -> None:
    """空线程集合不构造无意义或意外放宽的查询。"""

    class DB:
        async def execute(self, _statement):
            raise AssertionError("empty thread_ids must not query the database")

    assert await repository.list_thread_sessions(DB(), uid="u1", thread_ids=[]) == []


@pytest.mark.asyncio
async def test_list_thread_sessions_filters_by_uid_and_requested_threads() -> None:
    """批量映射查询同时限制用户和调用方已授权的线程集合。"""
    records = [SimpleNamespace(uid="u1", thread_id="t1")]
    captured_sql = ""

    class Scalars:
        def all(self):
            return records

    class Result:
        def scalars(self):
            return Scalars()

    class DB:
        async def execute(self, statement):
            nonlocal captured_sql
            captured_sql = str(statement.compile(compile_kwargs={"literal_binds": True}))
            return Result()

    result = await repository.list_thread_sessions(DB(), uid="u1", thread_ids=["t1", "t2"])

    assert result == records
    assert "agentscope_thread_sessions.uid = 'u1'" in captured_sql
    assert "agentscope_thread_sessions.thread_id IN ('t1', 't2')" in captured_sql
