"""Yuxi 使用的 AgentScope Team 身份解析。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class TeamLeader:
    """worker 当前所属 Team 的 leader 身份。"""

    session_id: str
    agent_id: str
    name: str


async def resolve_worker_team_leader(
    storage,
    *,
    uid: str,
    agent_id: str,
    session_id: str,
) -> TeamLeader | None:
    """从公开 Storage 记录解析 worker 的真实 leader。"""
    session = await storage.get_session(uid, agent_id, session_id)
    if session is None or session.team_id is None:
        return None
    team = await storage.get_team(uid, session.team_id)
    if team is None or team.session_id == session_id:
        return None

    leader_agent_id = str(getattr(team, "leader_agent_id", "") or "")
    if not leader_agent_id:
        leader_session = await storage.get_session(uid, "", team.session_id)
        if leader_session is None:
            raise ValueError("AgentScope Team leader Session 不存在")
        leader_agent_id = leader_session.agent_id

    leader_agent = await storage.get_agent(uid, leader_agent_id)
    if leader_agent is None:
        raise ValueError("AgentScope Team leader Agent 不存在")
    return TeamLeader(
        session_id=team.session_id,
        agent_id=leader_agent_id,
        name=leader_agent.data.name,
    )
