from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.repositories.agent_run_repository import AgentRunRepository
from yuxi.storage.postgres.models_business import (
    AgentRun,
    Base,
    Conversation,
    Project,
    SubagentThread,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


@pytest_asyncio.fixture()
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        yield db
    await engine.dispose()


async def _seed_subagent_runs(db, *, relation_child_thread_id: str = "child-thread") -> AgentRun:
    child_run = AgentRun(
        id="child-run",
        conversation_thread_id="child-thread",
        runtime_scope_id="parent-thread",
        agent_slug="worker",
        uid="user-1",
        status="completed",
        request_id="child-req",
        conversation_id=20,
        created_by_run_id="parent-run",
        subagent_thread_relation_id=77,
        run_type="subagent",
        input_payload={},
    )
    db.add_all(
        [
            Project(
                id="project-1",
                uid="user-1",
                selection_status="implicit",
                workdir_path="projects/project-1",
                directory_mode="managed",
            ),
            Conversation(
                id=10,
                thread_id="parent-thread",
                project_id="project-1",
                uid="user-1",
                agent_id="main",
                status="active",
            ),
            Conversation(
                id=20,
                thread_id="child-thread",
                project_id="project-1",
                uid="user-1",
                agent_id="worker",
                status="subagent",
            ),
            SubagentThread(
                id=77,
                uid="user-1",
                parent_conversation_id=10,
                child_conversation_id=20,
                child_thread_id=relation_child_thread_id,
                subagent_slug="worker",
                created_by_run_id="parent-run",
            ),
            AgentRun(
                id="parent-run",
                conversation_thread_id="parent-thread",
                runtime_scope_id="parent-thread",
                agent_slug="main",
                uid="user-1",
                status="completed",
                request_id="parent-req",
                conversation_id=10,
                run_type="chat",
                input_payload={},
            ),
            child_run,
        ]
    )
    await db.commit()
    return child_run


async def test_get_subagent_run_for_creator_returns_child_run(session):
    child_run = await _seed_subagent_runs(session)

    result = await AgentRunRepository(session).get_subagent_run_for_creator(
        uid="user-1",
        created_by_run_id="parent-run",
        run_id="child-run",
    )

    assert result is child_run


async def test_get_subagent_run_for_creator_returns_none_for_relation_mismatch(session):
    await _seed_subagent_runs(session, relation_child_thread_id="other-child-thread")

    result = await AgentRunRepository(session).get_subagent_run_for_creator(
        uid="user-1",
        created_by_run_id="parent-run",
        run_id="child-run",
    )

    assert result is None


async def test_create_run_persists_origin_snapshot(session):
    run = await AgentRunRepository(session).create_run(
        run_id="origin-run",
        conversation_thread_id="thread-1",
        agent_slug="main",
        uid="user-1",
        request_id="origin-request",
        input_payload={"model_spec": "provider:model"},
        source="agent_call",
        channel="api",
        external_id="external-1",
        origin_metadata={"agent_invocation_meta": {"trace_id": "trace-1"}},
    )
    await session.commit()

    assert run.source == "agent_call"
    assert run.channel == "api"
    assert run.external_id == "external-1"
    assert run.origin_metadata == {"agent_invocation_meta": {"trace_id": "trace-1"}}


async def _seed_thread_run(db, *, thread_id: str, run_id: str, status: str, run_type: str = "chat"):
    run = AgentRun(
        id=run_id,
        conversation_thread_id=thread_id,
        runtime_scope_id=thread_id,
        agent_slug="main",
        uid="user-1",
        status=status,
        request_id=f"req-{run_id}",
        run_type=run_type,
        input_payload={},
    )
    db.add(run)
    await db.flush()
    return run


async def test_get_latest_top_level_runs_for_threads_picks_latest_chat_resume(session):
    await _seed_thread_run(session, thread_id="t1", run_id="t1-old", status="completed")
    await _seed_thread_run(session, thread_id="t1", run_id="t1-running", status="running")
    await _seed_thread_run(session, thread_id="t2", run_id="t2-sub", status="completed", run_type="subagent")
    await _seed_thread_run(session, thread_id="t2", run_id="t2-done", status="completed")
    await session.commit()

    result = await AgentRunRepository(session).get_latest_top_level_runs_for_threads("user-1", ["t1", "t2"])

    assert result["t1"] == ("t1-running", "running")
    assert result["t2"] == ("t2-done", "completed")


async def test_get_latest_top_level_runs_for_threads_scopes_by_user(session):
    await _seed_thread_run(session, thread_id="t1", run_id="t1-done", status="completed")
    session.add(
        AgentRun(
            id="t1-other",
            conversation_thread_id="t1",
            runtime_scope_id="t1",
            agent_slug="main",
            uid="user-2",
            status="running",
            request_id="req-other",
            run_type="chat",
            input_payload={},
        )
    )
    await session.commit()

    result = await AgentRunRepository(session).get_latest_top_level_runs_for_threads("user-1", ["t1"])

    assert result["t1"] == ("t1-done", "completed")


async def test_get_latest_top_level_runs_for_threads_empty_input(session):
    result = await AgentRunRepository(session).get_latest_top_level_runs_for_threads("user-1", [])
    assert result == {}


async def test_list_child_runs_by_thread_returns_children_from_all_top_level_runs(session):
    first_parent = await _seed_thread_run(
        session,
        thread_id="thread-1",
        run_id="parent-1",
        status="completed",
    )
    second_parent = await _seed_thread_run(
        session,
        thread_id="thread-1",
        run_id="parent-2",
        status="completed",
        run_type="resume",
    )
    session.add_all(
        [
            AgentRun(
                id="child-1",
                conversation_thread_id="child-thread-1",
                runtime_scope_id="thread-1",
                agent_slug="worker-1",
                uid="user-1",
                status="completed",
                request_id="child-request-1",
                created_by_run_id=first_parent.id,
                run_type="subagent",
                input_payload={},
            ),
            AgentRun(
                id="child-2",
                conversation_thread_id="child-thread-2",
                runtime_scope_id="thread-1",
                agent_slug="worker-2",
                uid="user-1",
                status="completed",
                request_id="child-request-2",
                created_by_run_id=second_parent.id,
                run_type="subagent",
                input_payload={},
            ),
        ]
    )
    await session.commit()

    result = await AgentRunRepository(session).list_child_runs_by_thread_for_user(
        "thread-1",
        "user-1",
    )

    assert [run.id for run in result] == ["child-1", "child-2"]


async def test_set_terminal_status_persists_token_usage_only_for_winner(session):
    repo = AgentRunRepository(session)
    run = await repo.create_run(
        run_id="usage-run",
        conversation_thread_id="thread-1",
        agent_slug="main",
        uid="user-1",
        request_id="usage-request",
        input_payload={},
    )
    usage = {
        "schema_version": 2,
        "models": {"provider:model": {}},
        "total": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
    }

    persisted, changed = await repo.set_terminal_status(
        run.id,
        status="failed",
        token_usage=usage,
    )
    loser, loser_changed = await repo.set_terminal_status(
        run.id,
        status="completed",
        token_usage={"total": {"input_tokens": 999}},
    )

    assert changed is True
    assert persisted.token_usage == usage
    assert loser_changed is False
    assert loser.token_usage == usage


async def test_set_terminal_status_normalizes_cancel_request_under_lock(session):
    """Team 收尾竞争终态时必须在同一行锁内保留取消语义。"""
    repo = AgentRunRepository(session)
    run = await repo.create_run(
        run_id="cancelled-team-run",
        conversation_thread_id="thread-1",
        agent_slug="worker",
        uid="user-1",
        request_id="cancelled-team-request",
        input_payload={},
    )
    _, claimed = await repo.mark_running(run.id, worker_id="worker-1")
    assert claimed is True
    await repo.request_cancel(run.id)

    persisted, changed = await repo.set_terminal_status(
        run.id,
        status="completed",
        error_type="unexpected",
        error_message="should be cleared",
        worker_id="worker-1",
        cancel_requested_as_cancelled=True,
    )

    assert changed is True
    assert persisted.status == "cancelled"
    assert persisted.error_type is None
    assert persisted.error_message is None


async def test_request_cancel_converts_interrupted_run_to_cancelled(session):
    """人工审批挂起的 Run 仍可被取消，并且不能停留在 interrupted。"""
    repo = AgentRunRepository(session)
    run = await repo.create_run(
        run_id="interrupted-cancel-run",
        conversation_thread_id="thread-1",
        agent_slug="main",
        uid="user-1",
        request_id="interrupted-cancel-request",
        input_payload={},
    )
    run.status = "interrupted"
    await session.flush()

    persisted = await repo.request_cancel(run.id)

    assert persisted is not None
    assert persisted.status == "cancelled"
    assert persisted.error_type == "cancelled"
    assert persisted.error_message == "对话已取消"
