"""基于 Yuxi 事实源构建 AgentScope 线程状态视图。"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from yuxi.agents.backends.sandbox.paths import (
    sandbox_outputs_dir,
    virtual_path_for_thread_file,
)
from yuxi.agentscope.protocol import event_to_chunks
from yuxi.agentscope.thread_guard import load_pending_confirm
from yuxi.repositories.agent_run_repository import AgentRunRepository
from yuxi.repositories.conversation_repository import ConversationRepository
from yuxi.repositories.subagent_thread_repository import SubagentThreadRepository
from yuxi.services.subagent_run_service import serialize_subagent_run_state
from yuxi.storage.postgres.models_business import User
from yuxi.utils.logging_config import logger


def _list_artifacts(thread_id: str, uid: str) -> list[str]:
    """列出线程输出目录中的产物虚拟路径。"""
    root = sandbox_outputs_dir(thread_id)
    if not root.exists():
        return []
    return [
        virtual_path_for_thread_file(thread_id, path, uid=uid) for path in sorted(root.rglob("*")) if path.is_file()
    ]


def _serialize_message(message: Any) -> dict[str, Any]:
    """将业务消息转换为前端兼容的消息结构。"""
    role = str(message.role)
    result = {
        "id": str(message.id),
        "role": role,
        "type": {"user": "human", "assistant": "ai"}.get(role, role),
        "content": message.content,
        "message_type": message.message_type,
        "image_content": message.image_content,
        "extra_metadata": dict(message.extra_metadata or {}),
        "run_id": message.run_id,
        "request_id": message.request_id,
        "status": message.delivery_status,
    }
    if message.tool_calls:
        result["tool_calls"] = [tool_call.to_dict() for tool_call in message.tool_calls]
    return result


def _pending_interrupt(event: dict, *, thread_id: str, run_id: str) -> dict:
    """把 AgentScope 挂起事件还原为状态接口的 interrupt 载荷。"""
    chunks = event_to_chunks(event, request_id=run_id)
    if not chunks:
        raise ValueError(f"不支持的挂起事件类型: {event.get('type') or 'unknown'}")
    chunk = chunks[0]
    return {
        **{key: value for key, value in chunk.items() if key not in {"response", "request_id"}},
        "thread_id": thread_id,
        "run_id": run_id,
    }


async def get_thread_state_view(
    *,
    thread_id: str,
    current_user: User,
    db,
    include_messages: bool = False,
    include_relations: bool = True,
) -> dict:
    """返回线程状态，不读取旧 LangGraph checkpoint。"""
    uid = str(current_user.uid)
    conversation_repo = ConversationRepository(db)
    conversation = await conversation_repo.get_conversation_by_thread_id(thread_id)
    if conversation is None or conversation.uid != uid or conversation.status == "deleted":
        raise HTTPException(status_code=404, detail="对话线程不存在")

    run_repo = AgentRunRepository(db)
    latest_run = await run_repo.get_latest_run_by_thread_for_user(thread_id, uid)
    subagent_runs = []
    if latest_run is not None:
        for child_run in await run_repo.list_child_runs_for_user(latest_run.id, uid):
            try:
                subagent_runs.append(serialize_subagent_run_state(child_run))
            except ValueError as exc:
                logger.error(
                    "子智能体运行记录格式异常: parent_run_id=%s, run_id=%s, %s",
                    latest_run.id,
                    child_run.id,
                    exc,
                )
                raise HTTPException(
                    status_code=500,
                    detail="子智能体运行记录格式异常",
                ) from exc

    response: dict[str, Any] = {
        "agent_state": {
            "todos": [],
            "files": {},
            "artifacts": _list_artifacts(thread_id, uid),
            "subagent_runs": subagent_runs,
            "token_usage": dict(latest_run.token_usage or {}) if latest_run else None,
        }
    }

    pending = await load_pending_confirm(thread_id)
    if latest_run and latest_run.status == "interrupted" and pending:
        response["interrupt"] = _pending_interrupt(
            pending,
            thread_id=thread_id,
            run_id=latest_run.id,
        )

    if include_relations:
        relation = await SubagentThreadRepository(db).get_by_child_conversation_for_user(
            conversation.id,
            uid,
        )
        if relation:
            parent = await conversation_repo.get_conversation_by_id(relation.parent_conversation_id)
            if parent is None or parent.uid != uid or parent.status == "deleted":
                raise HTTPException(status_code=404, detail="父对话线程不存在")
            response["parent_thread_id"] = parent.thread_id
            response["subagent_thread"] = relation.to_dict()
            child_run = await run_repo.get_latest_subagent_run_by_thread_for_user(
                thread_id,
                uid,
            )
            if child_run:
                try:
                    response["subagent_run"] = serialize_subagent_run_state(child_run)
                except ValueError as exc:
                    logger.error(
                        "子智能体运行记录格式异常: thread_id=%s, run_id=%s, %s",
                        thread_id,
                        child_run.id,
                        exc,
                    )
                    raise HTTPException(
                        status_code=500,
                        detail="子智能体运行记录格式异常",
                    ) from exc

    if include_messages:
        messages = await conversation_repo.get_messages_by_thread_id(thread_id)
        response["messages"] = [_serialize_message(message) for message in messages]
    return response
