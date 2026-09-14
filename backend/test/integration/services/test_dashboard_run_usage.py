"""Dashboard Token 统计必须消费 AgentRun 终态事实。"""

from __future__ import annotations

import os
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.repositories.dashboard_repository import DashboardRepository
from yuxi.storage.postgres.models_business import Agent, AgentRun, Base, Conversation, Message, Project, User
from yuxi.utils.datetime_utils import utc_now, utc_now_naive

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_token_timeseries_uses_terminal_agent_run_usage_not_message_metadata() -> None:
    """消息缺失或 metadata 错误时，终态 Run 的真实用量仍必须准确计入。"""
    schema = f"pytest_dashboard_usage_{uuid4().hex[:16]}"
    admin_engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    scoped_engine = None
    try:
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        scoped_engine = create_async_engine(
            os.environ["POSTGRES_URL"],
            pool_pre_ping=True,
            connect_args={"server_settings": {"search_path": schema}},
        )
        async with scoped_engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        now = utc_now()
        finished_at = utc_now_naive() - timedelta(minutes=1)
        share_config = {
            "version": 2,
            "read_scope": {
                "access_level": "global",
                "department_ids": [],
                "user_uids": [],
            },
            "manage_scope": None,
        }
        async with async_sessionmaker(scoped_engine, expire_on_commit=False)() as db:
            user = User(
                uid="dashboard-user",
                username="dashboard-user",
                password_hash="unused",
                role="user",
                login_failed_count=0,
                is_deleted=0,
            )
            project = Project(
                id="dashboard-project",
                uid=user.uid,
                name="Dashboard",
                selection_status="selectable",
                workdir_path="projects/11111111-1111-4111-8111-111111111111",
                directory_mode="managed",
                status="active",
            )
            agent = Agent(
                slug="dashboard-agent",
                backend_id="ChatbotAgent",
                name="Dashboard",
                pics=[],
                config_json={},
                share_config=share_config,
                is_default=False,
                is_subagent=False,
            )
            conversation = Conversation(
                thread_id="dashboard-thread",
                uid=user.uid,
                agent_id=agent.slug,
                title="Dashboard",
                status="active",
                is_pinned=False,
                project_id=project.id,
                extra_metadata={},
            )
            db.add(user)
            await db.flush()
            db.add_all([project, agent])
            await db.flush()
            db.add(conversation)
            await db.flush()

            completed = AgentRun(
                id="dashboard-completed",
                conversation_thread_id=conversation.thread_id,
                runtime_scope_id=conversation.thread_id,
                runtime_cleanup_pending=False,
                agent_slug=agent.slug,
                uid=user.uid,
                status="completed",
                request_id="dashboard-completed-request",
                source="chat",
                channel="web",
                origin_metadata={},
                conversation_id=conversation.id,
                run_type="chat",
                input_payload={},
                token_usage={
                    "run": {
                        "total": {
                            "input_tokens": 17,
                            "output_tokens": 5,
                            "total_tokens": 22,
                        }
                    }
                },
                finished_at=finished_at,
            )
            failed = AgentRun(
                id="dashboard-failed",
                conversation_thread_id=conversation.thread_id,
                runtime_scope_id=conversation.thread_id,
                runtime_cleanup_pending=False,
                agent_slug=agent.slug,
                uid=user.uid,
                status="failed",
                request_id="dashboard-failed-request",
                source="chat",
                channel="web",
                origin_metadata={},
                conversation_id=conversation.id,
                run_type="resume",
                input_payload={},
                token_usage={
                    "run": {
                        "total": {
                            "input_tokens": 3,
                            "output_tokens": 2,
                            "total_tokens": 5,
                        }
                    }
                },
                finished_at=finished_at,
            )
            message = Message(
                conversation_id=conversation.id,
                role="assistant",
                content="错误的消息侧统计值",
                delivery_status="complete",
                extra_metadata={
                    "token_usage": {
                        "input_tokens": 999,
                        "output_tokens": 999,
                    }
                },
            )
            db.add_all([completed, failed, message])
            await db.flush()

            result = await DashboardRepository(db).get_call_timeseries(
                metric_type="tokens",
                time_range="14hours",
                now=now,
            )

        assert result["total_count"] == 27
        assert sum(item["data"]["input_tokens"] for item in result["data"]) == 20
        assert sum(item["data"]["output_tokens"] for item in result["data"]) == 7
    finally:
        if scoped_engine is not None:
            await scoped_engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin_engine.dispose()
