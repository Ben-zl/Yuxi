"""在隔离 PostgreSQL schema 验证运行时事实表的旧版本升级。"""

from __future__ import annotations

import json
import os
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from yuxi.storage.postgres.manager import PostgresManager
from yuxi.storage.postgres.models_business import Base

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


def _scoped_manager(engine) -> PostgresManager:
    """创建不触碰全局单例的隔离数据库管理器。"""
    manager = object.__new__(PostgresManager)
    PostgresManager.__init__(manager)
    manager.async_engine = engine
    manager._initialized = True
    return manager


async def _create_schema(admin_engine, schema: str) -> None:
    async with admin_engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))


async def _drop_schema(admin_engine, schema: str) -> None:
    async with admin_engine.begin() as connection:
        await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))


async def _create_current_tables(engine) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


async def _make_runtime_tables_legacy(connection) -> None:
    await connection.execute(text("ALTER TABLE agent_run_attempts RENAME COLUMN owner_id TO worker_id"))
    await connection.execute(text("ALTER TABLE agent_run_attempts RENAME COLUMN last_heartbeat_at TO heartbeat_at"))
    await connection.execute(text("CREATE SEQUENCE agent_run_attempts_id_seq"))
    await connection.execute(
        text(
            "ALTER TABLE agent_run_attempts "
            "ALTER COLUMN id TYPE INTEGER USING id::INTEGER, "
            "ALTER COLUMN id SET DEFAULT nextval('agent_run_attempts_id_seq')"
        )
    )
    for column_name in ("started_at", "heartbeat_at", "lease_expires_at", "finished_at"):
        await connection.execute(
            text(
                f"ALTER TABLE agent_run_attempts "
                f"ALTER COLUMN {column_name} TYPE TIMESTAMP WITH TIME ZONE "
                f"USING {column_name} AT TIME ZONE 'UTC'"
            )
        )

    await connection.execute(text("ALTER TABLE message_feedbacks DROP CONSTRAINT message_feedbacks_message_id_fkey"))
    await connection.execute(
        text("ALTER TABLE message_feedbacks ALTER COLUMN message_id TYPE VARCHAR(64) USING message_id::VARCHAR")
    )


async def test_runtime_schema_upgrade_converts_legacy_rows_and_is_idempotent() -> None:
    """旧事实行升级后应保留 UTC 语义并接受新的字符串 Attempt ID。"""
    schema = f"pytest_runtime_upgrade_{uuid4().hex[:16]}"
    admin_engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    scoped_engine = None

    try:
        await _create_schema(admin_engine, schema)
        scoped_engine = create_async_engine(
            os.environ["POSTGRES_URL"],
            pool_pre_ping=True,
            connect_args={"server_settings": {"search_path": schema}},
        )
        manager = _scoped_manager(scoped_engine)
        await _create_current_tables(scoped_engine)

        async with scoped_engine.begin() as connection:
            await _make_runtime_tables_legacy(connection)
            await connection.execute(
                text(
                    """
                    INSERT INTO agents (
                        slug,
                        backend_id,
                        name,
                        pics,
                        config_json,
                        share_config,
                        is_default,
                        is_subagent
                    ) VALUES (
                        'legacy-agent',
                        'ChatbotAgent',
                        'Legacy agent',
                        '[]'::json,
                        CAST(:config_json AS JSON),
                        CAST(:share_config AS JSONB),
                        FALSE,
                        FALSE
                    )
                    """
                ),
                {
                    "config_json": json.dumps({"context": {"tools": ["read", "write", "web_search"]}}),
                    "share_config": json.dumps(
                        {
                            "version": 2,
                            "read_scope": {
                                "access_level": "global",
                                "department_ids": [],
                                "user_uids": [],
                            },
                            "manage_scope": None,
                        }
                    ),
                },
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO agent_runs (
                        id,
                        conversation_thread_id,
                        runtime_scope_id,
                        runtime_cleanup_pending,
                        agent_slug,
                        uid,
                        status,
                        request_id,
                        source,
                        channel,
                        origin_metadata,
                        run_type,
                        input_payload,
                        token_usage
                    ) VALUES (
                        'legacy-run',
                        'legacy-thread',
                        'legacy-thread',
                        FALSE,
                        'legacy-agent',
                        'legacy-user',
                        'completed',
                        'legacy-request',
                        'chat',
                        'web',
                        '{}'::json,
                        'chat',
                        '{}'::json,
                        '{}'::jsonb
                    )
                    """
                )
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO agent_runs (
                        id,
                        conversation_thread_id,
                        runtime_scope_id,
                        runtime_cleanup_pending,
                        agent_slug,
                        uid,
                        status,
                        request_id,
                        source,
                        channel,
                        origin_metadata,
                        run_type,
                        input_payload,
                        token_usage,
                        worker_id,
                        heartbeat_at,
                        lease_expires_at
                    ) VALUES (
                        'legacy-expired-run',
                        'legacy-expired-thread',
                        'legacy-expired-thread',
                        TRUE,
                        'legacy-agent',
                        'legacy-user',
                        'expired',
                        'legacy-expired-request',
                        'chat',
                        'web',
                        '{}'::json,
                        'chat',
                        '{}'::json,
                        '{}'::jsonb,
                        'legacy-worker',
                        '2026-09-12 00:01:00',
                        '2026-09-12 00:02:00'
                    )
                    """
                )
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO agent_run_attempts (
                        id,
                        run_id,
                        attempt_no,
                        worker_id,
                        started_at,
                        heartbeat_at,
                        lease_expires_at,
                        finished_at
                    ) VALUES (
                        1,
                        'legacy-run',
                        1,
                        'legacy-worker',
                        '2026-09-12 08:00:00+08',
                        '2026-09-12 08:01:00+08',
                        '2026-09-12 08:02:00+08',
                        '2026-09-12 08:03:00+08'
                    )
                    """
                )
            )
            for statement in (
                "INSERT INTO users (username, uid, password_hash, role, login_failed_count, is_deleted) "
                "VALUES ('legacy-user', 'legacy-user', 'unused', 'user', 0, 0)",
                """
                INSERT INTO projects (
                    id,
                    uid,
                    name,
                    selection_status,
                    workdir_path,
                    directory_mode,
                    status
                ) VALUES (
                    'legacy-project',
                    'legacy-user',
                    'Legacy project',
                    'selectable',
                    'projects/11111111-1111-4111-8111-111111111111',
                    'managed',
                    'active'
                )
                """,
                """
                INSERT INTO conversations (
                    id,
                    thread_id,
                    uid,
                    agent_id,
                    title,
                    project_id,
                    is_pinned
                ) VALUES (
                    1,
                    'legacy-feedback-thread',
                    'legacy-user',
                    'legacy-agent',
                    'Legacy feedback',
                    'legacy-project',
                    FALSE
                )
                """,
                """
                ALTER TABLE message_feedbacks
                    ADD COLUMN conversation_id INTEGER,
                    ADD CONSTRAINT fk_message_feedbacks_conversation_id
                        FOREIGN KEY (conversation_id) REFERENCES conversations(id)
                """,
                "ALTER TABLE message_feedbacks ALTER COLUMN conversation_id SET NOT NULL",
                "INSERT INTO messages (id, conversation_id, role, content, delivery_status) "
                "VALUES (42, 1, 'assistant', 'legacy answer', 'complete')",
                (
                    "INSERT INTO message_feedbacks (message_id, conversation_id, uid, rating) "
                    "VALUES ('42', 1, 'legacy-user', 'like')"
                ),
            ):
                await connection.execute(text(statement))

        await manager.ensure_business_schema()

        async with scoped_engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO agent_run_attempts (
                        id,
                        run_id,
                        attempt_no,
                        owner_id,
                        started_at
                    ) VALUES (
                        'new-attempt-id',
                        'legacy-run',
                        2,
                        'new-worker',
                        '2026-09-12 01:00:00'
                    )
                    """
                )
            )

        await manager.ensure_business_schema()

        async with scoped_engine.connect() as connection:
            attempt_types = dict(
                (
                    await connection.execute(
                        text(
                            """
                            SELECT column_name, data_type
                            FROM information_schema.columns
                            WHERE table_schema = current_schema()
                              AND table_name = 'agent_run_attempts'
                              AND column_name IN (
                                  'id',
                                  'started_at',
                                  'last_heartbeat_at',
                                  'lease_expires_at',
                                  'finished_at'
                              )
                            """
                        )
                    )
                ).all()
            )
            feedback_type = await connection.scalar(
                text(
                    """
                    SELECT data_type
                    FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND table_name = 'message_feedbacks'
                      AND column_name = 'message_id'
                    """
                )
            )
            legacy_attempt = (
                (
                    await connection.execute(
                        text(
                            """
                        SELECT
                            id,
                            owner_id,
                            to_char(started_at, 'YYYY-MM-DD HH24:MI:SS') AS started_at,
                            to_char(last_heartbeat_at, 'YYYY-MM-DD HH24:MI:SS') AS heartbeat_at,
                            to_char(lease_expires_at, 'YYYY-MM-DD HH24:MI:SS') AS lease_expires_at,
                            to_char(finished_at, 'YYYY-MM-DD HH24:MI:SS') AS finished_at
                        FROM agent_run_attempts
                        WHERE attempt_no = 1
                        """
                        )
                    )
                )
                .mappings()
                .one()
            )
            attempt_ids = list(
                (await connection.execute(text("SELECT id FROM agent_run_attempts ORDER BY attempt_no"))).scalars()
            )
            feedback_message_id = await connection.scalar(
                text("SELECT message_id FROM message_feedbacks WHERE uid = 'legacy-user'")
            )
            feedback_foreign_key = await connection.scalar(
                text(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM pg_constraint
                        WHERE conrelid = 'message_feedbacks'::regclass
                          AND contype = 'f'
                          AND pg_get_constraintdef(oid)
                              = 'FOREIGN KEY (message_id) REFERENCES messages(id)'
                    )
                    """
                )
            )
            legacy_conversation_column = await connection.scalar(
                text(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM information_schema.columns
                        WHERE table_schema = current_schema()
                          AND table_name = 'message_feedbacks'
                          AND column_name = 'conversation_id'
                    )
                    """
                )
            )
            legacy_conversation_foreign_key = await connection.scalar(
                text(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM pg_constraint
                        WHERE conrelid = 'message_feedbacks'::regclass
                          AND conname = 'fk_message_feedbacks_conversation_id'
                    )
                    """
                )
            )
            tools = await connection.scalar(
                text("SELECT config_json::jsonb #> '{context,tools}' FROM agents WHERE slug = 'legacy-agent'")
            )
            legacy_expired_run = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT
                                status,
                                error_type,
                                finished_at IS NOT NULL AS has_finished_at,
                                worker_id,
                                heartbeat_at,
                                lease_expires_at,
                                runtime_cleanup_pending
                            FROM agent_runs
                            WHERE id = 'legacy-expired-run'
                            """
                        )
                    )
                )
                .mappings()
                .one()
            )

        assert attempt_types == {
            "id": "character varying",
            "started_at": "timestamp without time zone",
            "last_heartbeat_at": "timestamp without time zone",
            "lease_expires_at": "timestamp without time zone",
            "finished_at": "timestamp without time zone",
        }
        assert feedback_type == "integer"
        assert dict(legacy_attempt) == {
            "id": "1",
            "owner_id": "legacy-worker",
            "started_at": "2026-09-12 00:00:00",
            "heartbeat_at": "2026-09-12 00:01:00",
            "lease_expires_at": "2026-09-12 00:02:00",
            "finished_at": "2026-09-12 00:03:00",
        }
        assert attempt_ids == ["1", "new-attempt-id"]
        assert feedback_message_id == 42
        assert feedback_foreign_key is True
        assert legacy_conversation_column is False
        assert legacy_conversation_foreign_key is False
        assert tools == ["web_search"]
        assert dict(legacy_expired_run) == {
            "status": "interrupted",
            "error_type": "legacy_expired_status",
            "has_finished_at": True,
            "worker_id": None,
            "heartbeat_at": None,
            "lease_expires_at": None,
            "runtime_cleanup_pending": False,
        }
    finally:
        if scoped_engine is not None:
            await scoped_engine.dispose()
        await _drop_schema(admin_engine, schema)
        await admin_engine.dispose()


async def test_runtime_schema_upgrade_rejects_non_numeric_feedback_ids() -> None:
    """非数字旧 Message ID 必须阻止迁移且保留原始行。"""
    schema = f"pytest_runtime_feedback_{uuid4().hex[:16]}"
    admin_engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    scoped_engine = None

    try:
        await _create_schema(admin_engine, schema)
        scoped_engine = create_async_engine(
            os.environ["POSTGRES_URL"],
            pool_pre_ping=True,
            connect_args={"server_settings": {"search_path": schema}},
        )
        manager = _scoped_manager(scoped_engine)
        await _create_current_tables(scoped_engine)

        async with scoped_engine.begin() as connection:
            await connection.execute(
                text("ALTER TABLE message_feedbacks DROP CONSTRAINT message_feedbacks_message_id_fkey")
            )
            await connection.execute(
                text("ALTER TABLE message_feedbacks ALTER COLUMN message_id TYPE VARCHAR(64) USING message_id::VARCHAR")
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO message_feedbacks (message_id, uid, rating)
                    VALUES ('not-a-message-id', 'legacy-user', 'dislike')
                    """
                )
            )

        with pytest.raises(DBAPIError, match="contains non-numeric values"):
            await manager.ensure_business_schema()

        async with scoped_engine.connect() as connection:
            feedback_type = await connection.scalar(
                text(
                    """
                    SELECT data_type
                    FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND table_name = 'message_feedbacks'
                      AND column_name = 'message_id'
                    """
                )
            )
            feedback_message_id = await connection.scalar(
                text("SELECT message_id FROM message_feedbacks WHERE uid = 'legacy-user'")
            )

        assert feedback_type == "character varying"
        assert feedback_message_id == "not-a-message-id"
    finally:
        if scoped_engine is not None:
            await scoped_engine.dispose()
        await _drop_schema(admin_engine, schema)
        await admin_engine.dispose()


async def test_runtime_schema_upgrade_rejects_numeric_orphan_feedback_ids() -> None:
    """数字格式正确但不存在的 Message ID 也必须阻止迁移并保留旧表。"""
    schema = f"pytest_runtime_orphan_{uuid4().hex[:16]}"
    admin_engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    scoped_engine = None

    try:
        await _create_schema(admin_engine, schema)
        scoped_engine = create_async_engine(
            os.environ["POSTGRES_URL"],
            pool_pre_ping=True,
            connect_args={"server_settings": {"search_path": schema}},
        )
        manager = _scoped_manager(scoped_engine)
        await _create_current_tables(scoped_engine)

        async with scoped_engine.begin() as connection:
            await connection.execute(
                text("ALTER TABLE message_feedbacks DROP CONSTRAINT message_feedbacks_message_id_fkey")
            )
            await connection.execute(
                text("ALTER TABLE message_feedbacks ALTER COLUMN message_id TYPE VARCHAR(64) USING message_id::VARCHAR")
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO message_feedbacks (message_id, uid, rating)
                    VALUES ('42', 'legacy-user', 'dislike')
                    """
                )
            )

        with pytest.raises(DBAPIError, match="does not reference an existing message"):
            await manager.ensure_business_schema()

        async with scoped_engine.connect() as connection:
            feedback_type = await connection.scalar(
                text(
                    """
                    SELECT data_type
                    FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND table_name = 'message_feedbacks'
                      AND column_name = 'message_id'
                    """
                )
            )
            feedback_message_id = await connection.scalar(
                text("SELECT message_id FROM message_feedbacks WHERE uid = 'legacy-user'")
            )

        assert feedback_type == "character varying"
        assert feedback_message_id == "42"
    finally:
        if scoped_engine is not None:
            await scoped_engine.dispose()
        await _drop_schema(admin_engine, schema)
        await admin_engine.dispose()
