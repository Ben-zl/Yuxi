"""把 AgentScope Team worker 投影为 Yuxi 的长期子线程生命周期。"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from yuxi.agentscope.protocol import ToolEventConverter, event_to_chunks, make_chunk, reply_end_to_terminal
from yuxi.agentscope.run_lease import (
    RUN_LEASE_SECONDS,
    start_run_lease_heartbeat,
    stop_run_lease_heartbeat,
)
from yuxi.agentscope.team_protocol import resolve_worker_team_leader
from yuxi.agentscope.usage import CacheInputMode, UsageAccumulator
from yuxi.repositories.agent_repository import AgentRepository
from yuxi.repositories.agent_run_repository import AgentRunRepository, TERMINAL_RUN_STATUSES
from yuxi.repositories.agentscope_team_workers import AgentScopeTeamWorkerRepository
from yuxi.repositories.agentscope_thread_sessions import get_thread_session_by_agentscope_context
from yuxi.repositories.conversation_repository import ConversationRepository
from yuxi.repositories.subagent_thread_repository import SubagentThreadRepository
from yuxi.services.agent_run_manifest_service import build_run_manifest_result, compute_manifest_fingerprint
from yuxi.services.run_queue_service import append_run_stream_event
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import Conversation, Message, ToolCall, User
from yuxi.utils.hash_utils import hash_id, subagent_child_thread_id
from yuxi.utils.logging_config import logger


TEAM_MEMBER_MAX_NUDGES = 3


async def _record_team_run_manifest(db, run, *, worker_id: str) -> None:
    """在 Team child Run 进入 AgentScope worker 前固化运行资产。"""
    user = await db.scalar(select(User).where(User.uid == run.uid))
    if user is None:
        raise RuntimeError("Run 所属用户不存在")
    projected = await build_run_manifest_result(run=run, user=user, db=db)
    fingerprint = compute_manifest_fingerprint(projected.manifest)
    persisted, _recorded = await AgentRunRepository(db).record_run_manifest(
        run.id,
        manifest=projected.manifest,
        fingerprint=fingerprint,
        worker_id=worker_id,
    )
    if persisted is None:
        raise RuntimeError("Team child Run 不存在")
    persisted_fingerprint = getattr(persisted, "manifest_fingerprint", fingerprint)
    if persisted_fingerprint != fingerprint:
        raise RuntimeError("Team child Run manifest 已变化，拒绝继续执行")
    run.manifest_fingerprint = persisted_fingerprint


@dataclass(frozen=True)
class TeamRosterSnapshot:
    """一次 Team roster 快照，只保留生命周期投影需要的字段。"""

    team_id: str | None
    members: dict[str, tuple[str, str]]


def retain_latest_team_hint(agent, *, leader_name: str | None = None) -> None:
    """推理前移除 peer 回报和旧 Team 消息，只保留 leader 最新任务。"""
    context = list(getattr(agent.state, "context", None) or [])
    if not context:
        return
    content = getattr(context[-1], "content", None)
    if not isinstance(content, list):
        return
    team_indexes = [index for index, block in enumerate(content) if _is_team_hint(block)]
    if not team_indexes:
        return
    leader_indexes = [
        index for index in team_indexes if leader_name is None or _team_hint_sender(content[index]) == leader_name
    ]
    latest = leader_indexes[-1] if leader_indexes else None
    content[:] = [block for index, block in enumerate(content) if index not in team_indexes or index == latest]


class TeamLifecycleModule:
    """隐藏 Team roster 差分、child Run 投影和 worker 事件持久化。"""

    def __init__(
        self,
        *,
        storage,
        uid: str,
        agent_id: str,
        session_id: str,
    ):
        self.storage = storage
        self.uid = str(uid)
        self.agent_id = agent_id
        self.session_id = session_id

    async def snapshot(self) -> TeamRosterSnapshot:
        """读取当前 Session 所属 Team 的显式 worker roster。"""
        session = await self.storage.get_session(self.uid, self.agent_id, self.session_id)
        if session is None or session.team_id is None:
            return TeamRosterSnapshot(team_id=None, members={})
        team = await self.storage.get_team(self.uid, session.team_id)
        if team is None:
            return TeamRosterSnapshot(team_id=session.team_id, members={})
        members = {
            member.session_id: (member.agent_id, member.role)
            for member in team.data.members
            if member.owner_id == self.uid
        }
        return TeamRosterSnapshot(team_id=team.id, members=members)

    async def resolve_leader_name(self) -> str | None:
        """解析当前 worker 的真实 leader 展示名。"""
        leader = await resolve_worker_team_leader(
            self.storage,
            uid=self.uid,
            agent_id=self.agent_id,
            session_id=self.session_id,
        )
        return leader.name if leader is not None else None

    async def project_created_member(
        self,
        *,
        before: TeamRosterSnapshot,
        after: TeamRosterSnapshot,
        tool_call_id: str,
        tool_input: dict[str, Any],
    ) -> None:
        """把一次成功 AgentCreate 差分幂等投影成 child Thread 和 Run。"""
        new_session_ids = set(after.members) - set(before.members)
        if len(new_session_ids) != 1 or not after.team_id:
            return
        worker_session_id = new_session_ids.pop()
        worker_agent_id, role = after.members[worker_session_id]
        if role != "created":
            return

        subagent_slug = str(tool_input.get("subagent_type") or "").strip()
        prompt = str(tool_input.get("prompt") or "").strip()
        if not subagent_slug or not prompt:
            raise ValueError("AgentCreate 生命周期投影缺少 subagent_type 或 prompt")

        worker_session = await self.storage.get_session(
            self.uid,
            worker_agent_id,
            worker_session_id,
        )
        workspace_id = str(getattr(getattr(worker_session, "config", None), "workspace_id", "") or "")
        if not workspace_id:
            raise ValueError("AgentCreate 创建的 worker Session 缺少 workspace_id")

        async with pg_manager.get_async_session_context() as db:
            binding_repo = AgentScopeTeamWorkerRepository(db)
            if await binding_repo.get_by_worker_session(uid=self.uid, worker_session_id=worker_session_id):
                return
            leader = await get_thread_session_by_agentscope_context(
                db,
                uid=self.uid,
                agentscope_agent_id=self.agent_id,
                agentscope_session_id=self.session_id,
            )
            if leader is None:
                raise ValueError("Team leader 不存在有效的 Yuxi 线程映射")
            parent_run = await AgentRunRepository(db).get_active_run_by_thread_for_user(
                uid=self.uid,
                agent_slug=leader.agent_slug,
                conversation_thread_id=leader.thread_id,
            )
            if parent_run is None:
                # 低层 runner/框架测试可以直接调用 AgentScope，不具备 Yuxi
                # AgentRun 产品上下文；这类调用只验证编排，不创建 child 投影。
                return
            subagent = await AgentRepository(db).get_by_slug(subagent_slug)
            if subagent is None or not subagent.is_subagent:
                raise ValueError(f"受管子智能体 {subagent_slug} 已不可用")

            child_thread_id = subagent_child_thread_id(leader.thread_id, subagent_slug, tool_call_id)
            conversation = await ConversationRepository(db).get_conversation_by_thread_id(child_thread_id)
            if conversation is None:
                parent_conversation = await db.get(Conversation, parent_run.conversation_id)
                if parent_conversation is None or not parent_conversation.project_id:
                    raise ValueError("Team child Conversation 缺少父 Project 归属")
                conversation = await ConversationRepository(db).add_conversation(
                    uid=self.uid,
                    agent_id=subagent_slug,
                    title=f"SubAgent: {subagent.name}",
                    thread_id=child_thread_id,
                    project_id=parent_conversation.project_id,
                    metadata={
                        "source": "agentscope_team",
                        "parent_thread_id": leader.thread_id,
                        "created_by_run_id": parent_run.id,
                        "parent_conversation_id": parent_run.conversation_id,
                        "subagent_slug": subagent_slug,
                    },
                )
                conversation.status = "subagent"
            relation_repo = SubagentThreadRepository(db)
            relation = await relation_repo.get_by_child_thread_for_user(child_thread_id, self.uid)
            if relation is None:
                relation = await relation_repo.create(
                    uid=self.uid,
                    parent_conversation_id=parent_run.conversation_id,
                    child_conversation_id=conversation.id,
                    child_thread_id=child_thread_id,
                    subagent_slug=subagent_slug,
                    created_by_run_id=parent_run.id,
                )

            request_id = hash_id("team:", f"{self.uid}:{worker_session_id}:{tool_call_id}", length=64)
            input_message = Message(
                conversation_id=conversation.id,
                role="user",
                content=prompt,
                message_type="text",
                request_id=request_id,
                delivery_status="complete",
                extra_metadata={"request_id": request_id, "source": "agentscope_team"},
            )
            db.add(input_message)
            await db.flush()
            run = await AgentRunRepository(db).create_run(
                run_id=str(uuid.uuid4()),
                conversation_thread_id=child_thread_id,
                agent_slug=subagent_slug,
                uid=self.uid,
                request_id=request_id,
                input_payload={
                    "model_spec": (parent_run.input_payload or {}).get("model_spec"),
                    "runtime": {
                        "tool_call_id": tool_call_id,
                        "subagent_name": subagent.name,
                        "parent_thread_id": leader.thread_id,
                        "file_thread_id": leader.thread_id,
                        "worker_agent_id": worker_agent_id,
                        "worker_session_id": worker_session_id,
                        "team_id": after.team_id,
                    },
                },
                source="subagent",
                channel="internal",
                conversation_id=conversation.id,
                created_by_run_id=parent_run.id,
                subagent_thread_relation_id=relation.id,
                run_type="subagent",
                input_message_id=input_message.id,
            )
            claimed_run, acquired = await AgentRunRepository(db).mark_running(
                run.id,
                lease_seconds=RUN_LEASE_SECONDS,
            )
            if claimed_run is None or not acquired or not claimed_run.worker_id:
                raise RuntimeError("Team child Run 无法取得执行 lease")
            worker_id = claimed_run.worker_id
            await _record_team_run_manifest(db, claimed_run, worker_id=worker_id)
            await binding_repo.create(
                uid=self.uid,
                parent_thread_id=leader.thread_id,
                child_thread_id=child_thread_id,
                subagent_slug=subagent_slug,
                created_by_run_id=parent_run.id,
                subagent_thread_relation_id=relation.id,
                team_id=after.team_id,
                worker_agent_id=worker_agent_id,
                worker_session_id=worker_session_id,
                agentscope_workspace_id=workspace_id,
                active_run_id=run.id,
            )
            await db.commit()

        start_run_lease_heartbeat(run.id, worker_id=worker_id)
        await append_run_stream_event(
            run.id,
            "metadata",
            {"run_type": "subagent", "source": "agentscope_team", "request_id": request_id},
            thread_id=child_thread_id,
        )

    async def project_worker_reply(
        self,
        input_kwargs: dict,
        next_handler,
        *,
        cache_input_mode: CacheInputMode = "unknown",
    ) -> AsyncIterator[Any]:
        """透传 worker Reply，同时把事件、历史、用量和终态写入 child Run。"""
        binding = await self._wait_for_binding()
        if binding is None:
            async for item in next_handler(**input_kwargs):
                yield item
            return

        run_id: str | None = None
        request_id: str | None = None
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_converter: ToolEventConverter | None = None
        usage = UsageAccumulator(
            configured_model_spec=None,
            native_cache_input_mode=cache_input_mode,
        )
        reply_id: str | None = None
        prompt: str | None = None
        leader_name = await self.resolve_leader_name()
        leader_instruction_seen = False
        peer_message_seen = False
        completed_reply_ends = 0
        terminal = None
        terminal_error_message: str | None = None
        finalized = False
        try:
            async for item in next_handler(**input_kwargs):
                event = item.model_dump(mode="json") if hasattr(item, "model_dump") else item
                if not isinstance(event, dict):
                    yield item
                    continue
                event_type = str(event.get("type") or "").upper()
                if event_type == "REPLY_START":
                    reply_id = str(event.get("reply_id") or "")
                elif run_id is None and reply_id:
                    if event_type == "HINT_BLOCK":
                        sender = _team_message_sender(event)
                        if sender and leader_name and sender != leader_name:
                            peer_message_seen = True
                        else:
                            team_text = _team_message_text(event)
                            if team_text:
                                prompt = team_text
                                leader_instruction_seen = True
                    else:
                        if peer_message_seen and not leader_instruction_seen:
                            yield item
                            continue
                        run_id, request_id = await self._open_worker_run(
                            binding.worker_session_id,
                            reply_id,
                            input_kwargs,
                            prompt=prompt,
                        )
                        tool_converter = ToolEventConverter(request_id)
                if run_id and request_id:
                    usage.observe(event)
                    if event_type == "TEXT_BLOCK_DELTA":
                        text_parts.append(str(event.get("delta") or ""))
                    elif event_type == "THINKING_BLOCK_DELTA":
                        reasoning_parts.append(str(event.get("delta") or ""))
                    if event_type == "REPLY_END":
                        terminal_event = event
                        reason = str(event.get("finished_reason") or "completed").lower()
                        final_reply_end = reason not in {"completed", "exceed_max_iters"}
                        if not final_reply_end:
                            completed_reply_ends += 1
                            tool_calls = tool_converter.history_tool_calls() if tool_converter else []
                            final_reply_end = _reported_to_team_leader(tool_calls, leader_name)
                            if completed_reply_ends > TEAM_MEMBER_MAX_NUDGES and not final_reply_end:
                                terminal_event = {
                                    **event,
                                    "finished_reason": "error",
                                    "error": {
                                        "message": ("Team worker 连续 3 次纠正后仍未通过 TeamSay 回报 leader"),
                                    },
                                }
                                final_reply_end = True

                        terminal = reply_end_to_terminal(terminal_event, request_id=request_id)
                        terminal_error_message = (event.get("error") or {}).get("message") or terminal.chunk.get(
                            "error_message"
                        )
                        if final_reply_end:
                            persisted_status = await self._finish_worker_run(
                                run_id=run_id,
                                terminal_status=terminal.run_status,
                                error_type=terminal.chunk.get("error_type"),
                                error_message=terminal_error_message,
                                text="".join(text_parts),
                                reasoning="".join(reasoning_parts),
                                usage=usage.snapshot(complete=True),
                                tool_calls=tool_converter.history_tool_calls() if tool_converter else [],
                            )
                            if persisted_status is not None:
                                await append_run_stream_event(
                                    run_id,
                                    "end",
                                    {"status": persisted_status, "chunk": terminal.chunk},
                                    thread_id=binding.child_thread_id,
                                )
                            finalized = True
                    else:
                        chunks = event_to_chunks(event, request_id=request_id)
                        if tool_converter is not None:
                            chunks.extend(tool_converter.feed(event))
                        if chunks:
                            await append_run_stream_event(
                                run_id,
                                "messages",
                                {"items": chunks},
                                thread_id=binding.child_thread_id,
                            )
                yield item

            if run_id and request_id and terminal is not None and not finalized:
                persisted_status = await self._finish_worker_run(
                    run_id=run_id,
                    terminal_status=terminal.run_status,
                    error_type=terminal.chunk.get("error_type"),
                    error_message=terminal_error_message,
                    text="".join(text_parts),
                    reasoning="".join(reasoning_parts),
                    usage=usage.snapshot(complete=True),
                    tool_calls=tool_converter.history_tool_calls() if tool_converter else [],
                )
                if persisted_status is not None:
                    await append_run_stream_event(
                        run_id,
                        "end",
                        {"status": persisted_status, "chunk": terminal.chunk},
                        thread_id=binding.child_thread_id,
                    )
            elif run_id and request_id and not finalized:
                error_message = "AgentScope worker reply stream ended without REPLY_END"
                terminal_status = await self._finish_worker_run(
                    run_id=run_id,
                    terminal_status="failed",
                    error_type="agentscope_team",
                    error_message=error_message,
                    text="".join(text_parts),
                    reasoning="".join(reasoning_parts),
                    usage=usage.snapshot(complete=False),
                    tool_calls=tool_converter.history_tool_calls() if tool_converter else [],
                )
                if terminal_status:
                    chunk = (
                        make_chunk(request_id, status="interrupted", message="对话已取消")
                        if terminal_status == "cancelled"
                        else make_chunk(
                            request_id,
                            status="error",
                            error_type="agentscope_team",
                            error_message=error_message,
                        )
                    )
                    await append_run_stream_event(
                        run_id,
                        "end",
                        {"status": terminal_status, "chunk": chunk},
                        thread_id=binding.child_thread_id,
                    )
        except BaseException as exc:
            if run_id and request_id and not finalized:
                cancelled = isinstance(exc, asyncio.CancelledError)
                terminal_status = await self._finish_worker_run(
                    run_id=run_id,
                    terminal_status="cancelled" if cancelled else "failed",
                    error_type=None if cancelled else "agentscope_team",
                    error_message=None if cancelled else str(exc),
                    text="".join(text_parts),
                    reasoning="".join(reasoning_parts),
                    usage=usage.snapshot(complete=False),
                    tool_calls=tool_converter.history_tool_calls() if tool_converter else [],
                )
                if terminal_status is not None:
                    chunk = (
                        make_chunk(request_id, status="interrupted", message="对话已取消")
                        if terminal_status == "cancelled"
                        else make_chunk(
                            request_id,
                            status="error",
                            error_type="agentscope_team",
                            error_message=str(exc),
                        )
                    )
                    await append_run_stream_event(
                        run_id,
                        "end",
                        {"status": terminal_status, "chunk": chunk},
                        thread_id=binding.child_thread_id,
                    )
            raise

    async def _wait_for_binding(self):
        """吸收 AgentCreate 投影与 worker wakeup 的短暂竞态。"""
        for _ in range(30):
            async with pg_manager.get_async_session_context() as db:
                binding = await AgentScopeTeamWorkerRepository(db).get_by_worker_session(
                    uid=self.uid,
                    worker_session_id=self.session_id,
                )
                if binding is not None:
                    return binding
            await asyncio.sleep(0.1)
        logger.debug("Team worker 没有 Yuxi 生命周期绑定 session=%s", self.session_id)
        return None

    async def fail_setup(self) -> bool:
        """结束尚未进入 Reply middleware 的 worker child Run。"""
        session = await self.storage.get_session(
            self.uid,
            self.agent_id,
            self.session_id,
        )
        if session is None or session.team_id is None:
            return False
        team = await self.storage.get_team(self.uid, session.team_id)
        if team is None or team.session_id == self.session_id:
            return False

        binding = await self._wait_for_binding()
        if binding is None or not binding.active_run_id:
            return False

        async with pg_manager.get_async_session_context() as db:
            run = await AgentRunRepository(db).get_run(binding.active_run_id)
            if run is None or run.status in TERMINAL_RUN_STATUSES:
                return False
            request_id = run.request_id

        error_type = "agentscope_team_setup"
        error_message = "Team worker 会话准备失败，请检查模型、工具、Skill 和知识库配置"
        terminal_status = await self._finish_worker_run(
            run_id=binding.active_run_id,
            terminal_status="failed",
            error_type=error_type,
            error_message=error_message,
            text="",
            reasoning="",
            usage={},
            tool_calls=[],
        )
        if terminal_status is None:
            return False
        await append_run_stream_event(
            binding.active_run_id,
            "end",
            {
                "status": terminal_status,
                "chunk": make_chunk(
                    request_id,
                    status="error",
                    error_type=error_type,
                    error_message=error_message,
                ),
            },
            thread_id=binding.child_thread_id,
        )
        return True

    async def _open_worker_run(
        self,
        worker_session_id: str,
        reply_id: str,
        input_kwargs: dict,
        *,
        prompt: str | None = None,
    ) -> tuple[str, str]:
        """复用首个投影 Run；后续 worker Reply 创建新的 child Run。"""
        async with pg_manager.get_async_session_context() as db:
            bindings = AgentScopeTeamWorkerRepository(db)
            binding = await bindings.get_by_worker_session(uid=self.uid, worker_session_id=worker_session_id)
            if binding is None or not binding.runtime_active:
                raise ValueError("Team worker runtime 已不存在")
            runs = AgentRunRepository(db)
            active = await runs.get_run(binding.active_run_id) if binding.active_run_id else None
            if active is not None and active.status not in TERMINAL_RUN_STATUSES and not binding.last_reply_id:
                claimed, acquired = await runs.mark_running(active.id, lease_seconds=RUN_LEASE_SECONDS)
                if claimed is None or not acquired or not claimed.worker_id:
                    raise RuntimeError("Team child Run 无法重新取得执行 lease")
                await _record_team_run_manifest(db, claimed, worker_id=claimed.worker_id)
                binding.last_reply_id = reply_id
                await db.commit()
                start_run_lease_heartbeat(claimed.id, worker_id=claimed.worker_id)
                return claimed.id, claimed.request_id

            request_id = hash_id("team-reply:", f"{worker_session_id}:{reply_id}", length=64)
            existing = await runs.get_run_by_request_id(request_id)
            if existing is not None:
                if existing.status not in TERMINAL_RUN_STATUSES:
                    claimed, acquired = await runs.mark_running(existing.id, lease_seconds=RUN_LEASE_SECONDS)
                    if claimed is None or not acquired or not claimed.worker_id:
                        raise RuntimeError("Team child Run 无法重新取得执行 lease")
                    existing = claimed
                    await _record_team_run_manifest(db, existing, worker_id=existing.worker_id)
                binding.active_run_id = existing.id
                binding.last_reply_id = reply_id
                await db.commit()
                if existing.status not in TERMINAL_RUN_STATUSES:
                    start_run_lease_heartbeat(existing.id, worker_id=existing.worker_id)
                return existing.id, existing.request_id
            relation = await SubagentThreadRepository(db).get_for_user(binding.subagent_thread_relation_id, self.uid)
            conversation = await ConversationRepository(db).get_conversation_by_thread_id(binding.child_thread_id)
            if relation is None or conversation is None:
                raise ValueError("Team child Thread 投影已损坏")
            prompt = prompt or _input_text(input_kwargs.get("inputs")) or "Team worker continuation"
            message = Message(
                conversation_id=conversation.id,
                role="user",
                content=prompt,
                message_type="text",
                request_id=request_id,
                delivery_status="complete",
                extra_metadata={"request_id": request_id, "source": "agentscope_team"},
            )
            db.add(message)
            await db.flush()
            parent = await runs.get_latest_run_by_thread_for_user(binding.parent_thread_id, self.uid)
            run = await runs.create_run(
                run_id=str(uuid.uuid4()),
                conversation_thread_id=binding.child_thread_id,
                agent_slug=binding.subagent_slug,
                uid=self.uid,
                request_id=request_id,
                input_payload={
                    "model_spec": (parent.input_payload or {}).get("model_spec") if parent else None,
                    "runtime": {
                        "tool_call_id": f"team-reply:{reply_id}",
                        "parent_thread_id": binding.parent_thread_id,
                        "file_thread_id": binding.parent_thread_id,
                        "worker_agent_id": binding.worker_agent_id,
                        "worker_session_id": binding.worker_session_id,
                        "team_id": binding.team_id,
                    },
                },
                source="subagent",
                channel="internal",
                conversation_id=conversation.id,
                created_by_run_id=parent.id if parent else binding.created_by_run_id,
                subagent_thread_relation_id=relation.id,
                run_type="subagent",
                input_message_id=message.id,
            )
            claimed_run, acquired = await runs.mark_running(
                run.id,
                lease_seconds=RUN_LEASE_SECONDS,
            )
            if claimed_run is None or not acquired or not claimed_run.worker_id:
                raise RuntimeError("Team child Run 无法取得执行 lease")
            await _record_team_run_manifest(db, claimed_run, worker_id=claimed_run.worker_id)
            binding.active_run_id = run.id
            binding.last_reply_id = reply_id
            await db.commit()
            start_run_lease_heartbeat(run.id, worker_id=claimed_run.worker_id)
            return run.id, request_id

    async def _finish_worker_run(
        self,
        *,
        run_id: str,
        terminal_status: str,
        error_type: str | None,
        error_message: str | None,
        text: str,
        reasoning: str,
        usage: dict,
        tool_calls: list[dict],
    ) -> str | None:
        """原子保存 worker 输出和 child Run 终态，重复终态事件保持幂等。"""
        await stop_run_lease_heartbeat(run_id)
        async with pg_manager.get_async_session_context() as db:
            runs = AgentRunRepository(db)
            get_run = getattr(runs, "get_run", None)
            terminal_persisted = not callable(get_run)
            if terminal_persisted:
                run, changed = await runs.set_terminal_status(
                    run_id,
                    status=terminal_status,
                    error_type=error_type if terminal_status == "failed" else None,
                    error_message=error_message,
                    token_usage=usage,
                    cancel_requested_as_cancelled=True,
                )
                if run is None or not changed:
                    return None
                terminal_status = run.status
            else:
                run = await get_run(run_id)
                if run is None or run.status in TERMINAL_RUN_STATUSES:
                    return None
            worker_id = getattr(run, "worker_id", None)
            owner_kwargs = {"worker_id": worker_id} if worker_id else {}
            output_message = None
            if text or reasoning or tool_calls:
                output_message = Message(
                    conversation_id=run.conversation_id,
                    role="assistant",
                    content=text,
                    message_type="text",
                    run_id=run.id,
                    request_id=run.request_id,
                    delivery_status="complete",
                    extra_metadata={
                        "request_id": run.request_id,
                        "run_id": run.id,
                        "token_usage": usage,
                        "additional_kwargs": {"reasoning_content": reasoning} if reasoning else {},
                    },
                )
                db.add(output_message)
                await db.flush()
                for call in tool_calls:
                    db.add(
                        ToolCall(
                            message_id=output_message.id,
                            langgraph_tool_call_id=call.get("id"),
                            tool_name=call.get("name") or "unknown",
                            tool_input=call.get("args") or {},
                            tool_output=call.get("output") or "",
                            status=call.get("status") or "pending",
                            error_message=call.get("error_message"),
                        )
                    )
                await runs.set_output_message(run.id, output_message.id, **owner_kwargs)
            if not terminal_persisted:
                persisted, changed = await runs.set_terminal_status(
                    run_id,
                    status=terminal_status,
                    error_type=error_type if terminal_status == "failed" else None,
                    error_message=error_message,
                    token_usage=usage,
                    cancel_requested_as_cancelled=True,
                    **owner_kwargs,
                )
                if persisted is None or not changed:
                    return None
                terminal_status = persisted.status
            await db.commit()
            return terminal_status


async def interrupt_team_worker_runs(*, uid: str, run_ids: list[str]) -> None:
    """把 Yuxi child Run 取消转发到唯一的 AgentScope worker Session。"""
    async with pg_manager.get_async_session_context() as db:
        bindings = await AgentScopeTeamWorkerRepository(db).list_for_active_runs(
            uid=str(uid),
            run_ids=run_ids,
        )
    if not bindings:
        return

    import os

    from yuxi.agentscope.client import AgentScopeServiceClient

    client = AgentScopeServiceClient(os.getenv("AGENTSCOPE_BASE_URL", "http://agentscope:8100"))
    results = await asyncio.gather(
        *(
            client.interrupt_session(
                str(uid),
                binding.worker_agent_id,
                binding.worker_session_id,
            )
            for binding in bindings
        ),
        return_exceptions=True,
    )
    for binding, result in zip(bindings, results, strict=True):
        if isinstance(result, Exception):
            logger.warning(
                "中断 Team worker 失败 session=%s: %s",
                binding.worker_session_id,
                result,
            )


def _input_text(value: Any) -> str:
    """从 AgentScope Msg、Block 或列表提取可审计的输入文本。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "\n".join(filter(None, (_input_text(item) for item in value))).strip()
    if hasattr(value, "model_dump"):
        return _input_text(value.model_dump(mode="json"))
    if isinstance(value, dict):
        for key in ("text", "hint", "content", "input"):
            if key in value:
                text = _input_text(value[key])
                if text:
                    return text
    return ""


def _is_team_hint(block: Any) -> bool:
    """识别 AgentScope Team inbox 注入的 HintBlock。"""
    source = getattr(block, "source", None)
    if not isinstance(source, str):
        return False
    try:
        return json.loads(source).get("label") in {"team", "team_message"}
    except (TypeError, ValueError):
        return False


def _team_hint_sender(block: Any) -> str:
    """读取上下文 Team HintBlock 的发送者展示名。"""
    source = getattr(block, "source", None)
    if not isinstance(source, str):
        return ""
    try:
        payload = json.loads(source)
    except (TypeError, ValueError):
        return ""
    if payload.get("label") not in {"team", "team_message"}:
        return ""
    return str(payload.get("sublabel") or "").strip()


def _team_message_text(event: dict[str, Any]) -> str:
    """从 Team HintBlockEvent 中提取 leader 派发的正文。"""
    source = event.get("source")
    if not isinstance(source, str):
        return ""
    try:
        if json.loads(source).get("label") not in {"team", "team_message"}:
            return ""
    except (TypeError, ValueError):
        return ""
    hint = str(event.get("hint") or "")
    start = hint.find(">\n")
    suffix = "\n</team-message>"
    if start < 0 or not hint.endswith(suffix):
        return ""
    return hint[start + 2 : -len(suffix)].strip()


def _team_message_sender(event: dict[str, Any]) -> str:
    """从 Team HintBlockEvent 来源中读取 leader 展示名。"""
    source = event.get("source")
    if not isinstance(source, str):
        return ""
    try:
        payload = json.loads(source)
    except (TypeError, ValueError):
        return ""
    if payload.get("label") not in {"team", "team_message"}:
        return ""
    return str(payload.get("sublabel") or "").strip()


def _reported_to_team_leader(tool_calls: list[dict], leader_name: str | None) -> bool:
    """判断本轮最后一次工具调用是否成功定向回报真实 leader。"""
    if not tool_calls:
        return False
    last_call = tool_calls[-1]
    if last_call.get("name") != "TeamSay" or last_call.get("status") != "success":
        return False
    target = (last_call.get("metadata") or {}).get("team_target")
    if target is None:
        target = (last_call.get("args") or {}).get("to")
    return bool(leader_name and target == leader_name)
