"""把 AgentScope 会话上下文解析为唯一的 Yuxi 运行时投影。"""

import asyncio
import uuid
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.agentscope.config_projection import RuntimeProjection, project_runtime
from yuxi.repositories.agentscope_thread_sessions import (
    create_thread_session,
    get_thread_session_by_agentscope_context,
)
from yuxi.repositories.agentscope_channel_bindings import (
    AgentScopeChannelBindingRepository,
)
from yuxi.repositories.conversation_repository import ConversationRepository
from yuxi.repositories.agentscope_team_workers import AgentScopeTeamWorkerRepository


async def sync_runtime_skills(workspace, projection: RuntimeProjection, *, agent_id: str) -> None:
    """将当前 Agent 的 workspace Skill 对齐到运行时投影。"""
    current_skills = {item.name: item for item in await workspace.list_skills(agent_id=agent_id)}
    desired_skills = {item["slug"]: item["source_dir"] for item in projection.skills}

    for slug in sorted(current_skills.keys() - desired_skills.keys()):
        await workspace.remove_skill(slug, agent_id=agent_id)

    for slug, source_dir in desired_skills.items():
        current = current_skills.get(slug)
        if current is not None:
            source_markdown = (Path(source_dir) / "SKILL.md").read_text(encoding="utf-8")
            if current.markdown == source_markdown:
                continue
            # AgentScope 按 Skill 名称去重，不会覆盖同名旧副本。
            await workspace.remove_skill(slug, agent_id=agent_id)
        await workspace.add_skill(desired_skills[slug], agent_id=agent_id)


async def resolve_runtime_projection(
    db: AsyncSession,
    storage,
    *,
    user_id: str,
    agent_id: str,
    session_id: str,
) -> RuntimeProjection:
    """解析会话投影；Team worker 使用子智能体资源和父 Run 上下文。"""
    mapping = await get_thread_session_by_agentscope_context(
        db,
        uid=user_id,
        agentscope_agent_id=agent_id,
        agentscope_session_id=session_id,
    )
    runtime_agent_slug: str | None = None
    if mapping is None:
        session = await storage.get_session(user_id, agent_id, session_id)
        source = getattr(session, "source", None) if session is not None else None
        source_value = getattr(source, "value", source)
        if session is not None and source_value == "channel" and session.source_channel_id:
            binding = await AgentScopeChannelBindingRepository(db).get_by_channel_id(
                session.source_channel_id,
            )
            if binding is None or binding.sync_status != "synced":
                raise ValueError("AgentScope Channel 不存在已同步的 Yuxi binding")
            if user_id != binding.owner_uid:
                raise ValueError("AgentScope Channel 执行身份与 binding owner 不一致")

            thread_id = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"yuxi:agentscope-channel:{session.source_channel_id}:{session_id}",
                ),
            )
            conversation = await ConversationRepository(db).get_conversation_by_thread_id(
                thread_id,
            )
            if conversation is None:
                conversation = await ConversationRepository(db).add_conversation(
                    uid=binding.owner_uid,
                    agent_id=binding.agent_slug,
                    title=f"WPS 协作 · {binding.name}",
                    thread_id=thread_id,
                    metadata={
                        "source": "agentscope_channel",
                        "channel": "wps_xiezuo",
                        "agentscope_channel_id": session.source_channel_id,
                        "agentscope_session_id": session_id,
                    },
                )
            mapping = await create_thread_session(
                db,
                uid=binding.owner_uid,
                thread_id=thread_id,
                agent_slug=binding.agent_slug,
                model_spec=binding.model_spec,
                agentscope_agent_id=agent_id,
                agentscope_credential_id=binding.agentscope_credential_id,
                agentscope_session_id=session_id,
                agentscope_workspace_id=session.config.workspace_id,
            )
            await db.commit()

        if session is None or session.team_id is None:
            if mapping is None:
                raise ValueError("AgentScope 会话不存在有效的 Yuxi 线程映射")
        if mapping is not None:
            return await project_runtime(
                db,
                uid=user_id,
                agent_slug=mapping.agent_slug,
                model_spec=mapping.model_spec,
                thread_id=mapping.thread_id,
                is_team_worker=False,
            )

        binding_repo = AgentScopeTeamWorkerRepository(db)
        binding = None
        # AgentCreate 启动 worker 与 leader 中间件落 binding 存在短暂竞态。
        for _ in range(20):
            binding = await binding_repo.get_by_worker_session(
                uid=user_id,
                worker_session_id=session_id,
            )
            if binding is not None:
                break
            await asyncio.sleep(0.1)
        if binding is None:
            raise ValueError("AgentScope Team worker 不存在有效的子智能体绑定")
        runtime_agent_slug = binding.subagent_slug

        team = await storage.get_team(user_id, session.team_id)
        if team is None:
            raise ValueError("AgentScope Team 已不存在，无法解析运行时配置")
        leader_session = await storage.get_session(user_id, "", team.session_id)
        if leader_session is None:
            raise ValueError("AgentScope Team leader 会话不存在，无法解析运行时配置")
        mapping = await get_thread_session_by_agentscope_context(
            db,
            uid=user_id,
            agentscope_agent_id=leader_session.agent_id,
            agentscope_session_id=leader_session.id,
        )
        if mapping is None:
            raise ValueError("AgentScope Team leader 不存在有效的 Yuxi 线程映射")

    return await project_runtime(
        db,
        uid=user_id,
        agent_slug=runtime_agent_slug or mapping.agent_slug,
        model_spec=mapping.model_spec,
        thread_id=mapping.thread_id,
        is_team_worker=runtime_agent_slug is not None,
    )
