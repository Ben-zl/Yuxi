from __future__ import annotations

from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from yuxi.services.conversation_service import get_thread_history_view
from yuxi.storage.postgres.models_business import AgentRun, Base, Conversation, Message, ToolCall

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest_asyncio.fixture()
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        yield db
    await engine.dispose()


async def test_queue_history_keeps_each_request_with_its_reply(session):
    started_at = datetime(2026, 7, 12, 9, 0, 0)
    session.add(Conversation(id=1, thread_id="thread-1", uid="user-1", agent_id="main", status="active"))
    session.add(
        AgentRun(
            id="run-a",
            conversation_thread_id="thread-1",
            agent_slug="main",
            uid="user-1",
            request_id="request-a",
            conversation_id=1,
            input_payload={},
            status="completed",
            created_at=started_at,
        )
    )
    session.add_all(
        [
            Message(
                id=1,
                conversation_id=1,
                role="user",
                content="A",
                request_id="request-a",
                run_id="run-a",
                delivery_status="complete",
                created_at=started_at,
            ),
            Message(
                id=2,
                conversation_id=1,
                role="user",
                content="B",
                request_id="request-b",
                delivery_status="queued",
                created_at=started_at + timedelta(seconds=1),
            ),
            Message(
                id=3,
                conversation_id=1,
                role="assistant",
                content="A reply",
                run_id="run-a",
                delivery_status="complete",
                created_at=started_at + timedelta(seconds=2),
            ),
        ]
    )
    await session.commit()

    queued_history = await get_thread_history_view(
        thread_id="thread-1",
        current_uid="user-1",
        db=session,
    )
    assert [message["content"] for message in queued_history["history"]] == ["A", "A reply"]

    request_b = await session.get(Message, 2)
    request_b.run_id = "run-b"
    request_b.delivery_status = "complete"
    session.add(
        AgentRun(
            id="run-b",
            conversation_thread_id="thread-1",
            agent_slug="main",
            uid="user-1",
            request_id="request-b",
            conversation_id=1,
            input_payload={},
            status="completed",
            created_at=started_at + timedelta(seconds=3),
        )
    )
    session.add(
        Message(
            id=4,
            conversation_id=1,
            role="assistant",
            content="B reply",
            run_id="run-b",
            delivery_status="complete",
            created_at=started_at + timedelta(seconds=4),
        )
    )
    await session.commit()

    completed_history = await get_thread_history_view(
        thread_id="thread-1",
        current_uid="user-1",
        db=session,
    )
    assert [message["content"] for message in completed_history["history"]] == [
        "A",
        "A reply",
        "B",
        "B reply",
    ]


async def test_history_keeps_cancelled_request_when_run_has_assistant_output(session):
    """执行后取消的 Run 已有回复时，历史必须保留完整问答。"""
    started_at = datetime(2026, 8, 21, 13, 0, 0)
    session.add(Conversation(id=1, thread_id="thread-1", uid="user-1", agent_id="main", status="active"))
    session.add(
        AgentRun(
            id="run-cancelled",
            conversation_thread_id="thread-1",
            agent_slug="main",
            uid="user-1",
            request_id="request-cancelled",
            conversation_id=1,
            input_payload={},
            status="cancelled",
            created_at=started_at,
        )
    )
    session.add_all(
        [
            Message(
                id=1,
                conversation_id=1,
                role="user",
                content="查询性能",
                request_id="request-cancelled",
                run_id="run-cancelled",
                delivery_status="cancelled",
                created_at=started_at,
            ),
            Message(
                id=2,
                conversation_id=1,
                role="assistant",
                content="已生成部分结果",
                request_id="request-cancelled",
                run_id="run-cancelled",
                delivery_status="complete",
                created_at=started_at + timedelta(seconds=1),
            ),
        ]
    )
    await session.commit()

    response = await get_thread_history_view(
        thread_id="thread-1",
        current_uid="user-1",
        db=session,
    )

    assert [message["content"] for message in response["history"]] == [
        "查询性能",
        "已生成部分结果",
    ]


async def test_history_exposes_failed_run_without_assistant_output(session):
    """失败 Run 没有回复消息时，历史仍应展示明确的错误回复。"""
    started_at = datetime(2026, 8, 28, 8, 5, 41)
    session.add(Conversation(id=1, thread_id="thread-1", uid="user-1", agent_id="main", status="active"))
    session.add(
        AgentRun(
            id="run-failed",
            conversation_thread_id="thread-1",
            agent_slug="main",
            uid="user-1",
            request_id="request-failed",
            conversation_id=1,
            input_payload={},
            status="failed",
            error_type="configuration_error",
            error_message="执行失败: 模型供应商未配置 API Key",
            created_at=started_at,
            finished_at=started_at + timedelta(seconds=1),
        )
    )
    session.add(
        Message(
            id=1,
            conversation_id=1,
            role="user",
            content="进一步分析",
            request_id="request-failed",
            run_id="run-failed",
            delivery_status="failed",
            created_at=started_at,
        )
    )
    await session.commit()

    response = await get_thread_history_view(
        thread_id="thread-1",
        current_uid="user-1",
        db=session,
    )

    assert [message["type"] for message in response["history"]] == ["human", "ai"]
    error_reply = response["history"][1]
    assert error_reply["id"] == "run-error:run-failed"
    assert error_reply["run_id"] == "run-failed"
    assert error_reply["request_id"] == "request-failed"
    assert error_reply["delivery_status"] == "failed"
    assert error_reply["error_type"] == "configuration_error"
    assert error_reply["error_message"] == "执行失败: 模型供应商未配置 API Key"
    assert error_reply["extra_metadata"]["synthetic_run_error"] is True


async def test_history_restores_reasoning_and_tool_calls(session):
    """历史接口应恢复实时阶段可见的推理内容与工具执行详情。"""
    session.add(Conversation(id=1, thread_id="thread-1", uid="user-1", agent_id="main", status="active"))
    session.add(
        Message(
            id=1,
            conversation_id=1,
            role="assistant",
            content="最终答案",
            extra_metadata={
                "additional_kwargs": {"reasoning_content": "先查询知识库"},
            },
        )
    )
    session.add(
        ToolCall(
            message_id=1,
            langgraph_tool_call_id="tc1",
            tool_name="query_kb",
            tool_input={"query": "退款"},
            tool_output="知识库结果",
            status="success",
        )
    )
    await session.commit()

    response = await get_thread_history_view(
        thread_id="thread-1",
        current_uid="user-1",
        db=session,
    )

    assistant = response["history"][0]
    assert assistant["additional_kwargs"] == {"reasoning_content": "先查询知识库"}
    assert assistant["tool_calls"] == [
        {
            "id": "tc1",
            "name": "query_kb",
            "function": {"name": "query_kb"},
            "args": {"query": "退款"},
            "tool_call_result": {"content": "知识库结果"},
            "status": "success",
            "error_message": None,
        }
    ]
