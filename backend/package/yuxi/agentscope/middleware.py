"""Yuxi 注入 AgentScope 的运行生命周期中间件。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from agentscope.event import CustomEvent
from agentscope.middleware import MiddlewareBase
from yuxi.utils.logging_config import logger

from yuxi.agentscope.team_lifecycle import TeamLifecycleModule


class SteerMiddleware(MiddlewareBase):
    """只在模型前或无工具模型结束后让当前 Run 为 Steer 让路。"""

    def __init__(self, should_stop: Callable[[], Awaitable[bool]]):
        self._should_stop = should_stop

    async def on_reasoning(self, agent, input_kwargs, next_handler):
        """工具批次完整结束后才会再次进入该边界。"""
        if await self._should_stop():
            raise asyncio.CancelledError

        has_tool_call = False
        async for item in next_handler(**input_kwargs):
            has_tool_call = has_tool_call or getattr(item, "type", None) == "tool_call"
            yield item

        if not has_tool_call and await self._should_stop():
            raise asyncio.CancelledError


class ContextObservabilityMiddleware(MiddlewareBase):
    """发布 Token 上下文快照与压缩生命周期，供 Yuxi 状态面板消费。"""

    def __init__(self, message_bus, session_id: str):
        self._message_bus = message_bus
        self._session_id = session_id

    async def _publish(self, name: str, value: dict[str, Any]) -> None:
        event = CustomEvent(name=name, value=value)
        await self._message_bus.session_publish_event(
            self._session_id,
            event.model_dump(mode="json"),
        )

    @staticmethod
    def _estimate_tokens(value: Any) -> int:
        """用稳定字符近似拆分上下文构成；总输入仍使用模型计数器。"""
        if value in (None, [], {}, ""):
            return 0
        if hasattr(value, "model_dump"):
            value = value.model_dump(mode="json")
        serialized = json.dumps(value, ensure_ascii=False, default=str)
        return max((len(serialized) + 3) // 4, 1)

    async def on_model_call(self, agent, input_kwargs, next_handler):
        """在每次模型调用前发布当前实际输入与上下文上限。"""
        model = input_kwargs["current_model"]
        messages = list(input_kwargs.get("messages") or [])
        tools = list(input_kwargs.get("tools") or [])
        state_messages = list(agent.state.context or [])
        summary = str(agent.state.summary or "")

        try:
            llm_input_tokens = int(await model.count_tokens(messages=messages, tools=tools))
        except Exception:  # noqa: BLE001 - 观测失败不能阻断模型主调用
            logger.exception("AgentScope 上下文 Token 计数失败，改用字符近似")
            llm_input_tokens = self._estimate_tokens([messages, tools])

        message_payloads = [
            message.model_dump(mode="json") if hasattr(message, "model_dump") else message
            for message in messages
        ]
        system_messages = [item for item in message_payloads if isinstance(item, dict) and item.get("role") == "system"]
        non_system_messages = [
            item for item in message_payloads if not (isinstance(item, dict) and item.get("role") == "system")
        ]
        tool_messages = [
            item
            for item in non_system_messages
            if "tool_result" in json.dumps(item, ensure_ascii=False, default=str)
        ]
        content_messages = [item for item in non_system_messages if item not in tool_messages]
        context_window = max(int(getattr(model, "context_size", 0) or 0), 0) or None
        trigger_ratio = float(agent.context_config.trigger_ratio)

        await self._publish(
            "token_context",
            {
                "state_message_count": len(state_messages),
                "state_message_count_before_call": len(state_messages),
                "state_messages_tokens": self._estimate_tokens(state_messages),
                "state_messages_tokens_before_call": self._estimate_tokens(state_messages),
                "llm_message_count": len(non_system_messages),
                "llm_messages_tokens": self._estimate_tokens(non_system_messages),
                "llm_content_message_count": len(content_messages),
                "llm_content_message_tokens": self._estimate_tokens(content_messages),
                "llm_tool_message_count": len(tool_messages),
                "llm_tool_message_tokens": self._estimate_tokens(tool_messages),
                "llm_input_tokens": llm_input_tokens,
                "system_tokens": self._estimate_tokens(system_messages),
                "tools_tokens": self._estimate_tokens(tools),
                "tool_count": len(tools),
                "context_window": context_window,
                "context_usage_ratio": (
                    min(round(llm_input_tokens / context_window, 4), 1.0) if context_window else None
                ),
                "remaining_context_tokens": (
                    max(context_window - llm_input_tokens, 0) if context_window else None
                ),
                "summary_active": bool(summary),
                "summary_message_tokens": self._estimate_tokens(summary),
                "summary_trigger_tokens": int(context_window * trigger_ratio) if context_window else None,
                "counter": "agentscope.model.count_tokens+chars/4",
                "estimate": True,
                "measured_at": datetime.now(UTC).isoformat(),
            },
        )
        return await next_handler(**input_kwargs)

    async def on_compress_context(self, agent, input_kwargs, next_handler):
        """把 AgentScope 压缩阶段映射为 main 分支已有的页面协议。"""
        summary_before = str(getattr(agent.state, "summary", "") or "")
        context_before = self._estimate_tokens(list(getattr(agent.state, "context", None) or []))
        try:
            await next_handler(**input_kwargs)
        except BaseException:
            raise
        summary_after = str(getattr(agent.state, "summary", "") or "")
        context_after = self._estimate_tokens(list(getattr(agent.state, "context", None) or []))
        if summary_after == summary_before and context_after == context_before:
            return
        await self._publish("context_compression", {"status": "started"})
        await self._publish(
            "context_compression",
            {"status": "completed", "summary_active": bool(summary_after)},
        )

class TeamLifecycleMiddleware(MiddlewareBase):
    """保留 AgentScope Team runtime，并投影为 Yuxi 长期 child Thread。"""

    def __init__(self, lifecycle: TeamLifecycleModule, *, is_worker: bool):
        self._lifecycle = lifecycle
        self._is_worker = is_worker

    async def on_acting(self, agent, input_kwargs, next_handler):
        """观察 AgentCreate roster 差分，并把 TeamDelete 收口到父线程删除。"""
        tool_call = input_kwargs["tool_call"]
        if tool_call.name == "TeamDelete":
            from agentscope.message import TextBlock
            from agentscope.tool import ToolChunk

            yield ToolChunk(
                content=[TextBlock(text="Team runtime retained until the parent thread is deleted.")],
            )
            return
        if tool_call.name != "AgentCreate":
            async for item in next_handler(**input_kwargs):
                yield item
            return

        before = await self._lifecycle.snapshot()
        emitted = [item async for item in next_handler(**input_kwargs)]
        after = await self._lifecycle.snapshot()
        try:
            tool_input = json.loads(tool_call.input or "{}")
            if not isinstance(tool_input, dict):
                tool_input = {}
            await self._lifecycle.project_created_member(
                before=before,
                after=after,
                tool_call_id=tool_call.id,
                tool_input=tool_input,
            )
        except Exception:
            # AgentCreate 已经成功时不能把投影故障反馈为工具失败，否则模型会
            # 重试并创建重复 worker；故障保留在服务日志供恢复扫描处理。
            logger.exception("AgentScope Team worker 生命周期投影失败")
        for item in emitted:
            yield item

    async def on_reply(self, agent, input_kwargs, next_handler):
        """worker Reply 进入 child Run；leader Reply 保持原链路。"""
        if not self._is_worker:
            async for item in next_handler(**input_kwargs):
                yield item
            return
        async for item in self._lifecycle.project_worker_reply(input_kwargs, next_handler):
            yield item


async def build_steer_middleware(user_id: str, agent_id: str, session_id: str) -> SteerMiddleware | None:
    """为顶层 Yuxi Session 解析当前 Run；Team worker 不注入。"""
    from yuxi.repositories.agent_run_repository import AgentRunRepository
    from yuxi.repositories.agentscope_thread_sessions import get_thread_session_by_agentscope_context
    from yuxi.services.agent_request_queue_service import should_end_run_for_steer
    from yuxi.storage.postgres.manager import pg_manager

    async with pg_manager.get_async_session_context() as db:
        mapping = await get_thread_session_by_agentscope_context(
            db,
            uid=user_id,
            agentscope_agent_id=agent_id,
            agentscope_session_id=session_id,
        )
        if mapping is None:
            return None
        run = await AgentRunRepository(db).get_active_run_by_thread_for_user(
            uid=user_id,
            agent_slug=mapping.agent_slug,
            conversation_thread_id=mapping.thread_id,
        )
    if run is None:
        return None

    async def should_stop() -> bool:
        return await should_end_run_for_steer(run.id)

    return SteerMiddleware(should_stop)


async def build_team_lifecycle_middleware(storage, user_id: str, agent_id: str, session_id: str):
    """按 Session 角色创建 Team 生命周期 Middleware。"""
    session = await storage.get_session(user_id, agent_id, session_id)
    if session is None:
        return None
    is_worker = False
    if session.team_id is not None:
        team = await storage.get_team(user_id, session.team_id)
        is_worker = team is not None and team.session_id != session_id
    return TeamLifecycleMiddleware(
        TeamLifecycleModule(
            storage=storage,
            uid=user_id,
            agent_id=agent_id,
            session_id=session_id,
        ),
        is_worker=is_worker,
    )


def build_context_observability_middleware(message_bus, session_id: str) -> ContextObservabilityMiddleware:
    """为本轮会话创建上下文观测 Middleware。"""
    return ContextObservabilityMiddleware(message_bus, session_id)
