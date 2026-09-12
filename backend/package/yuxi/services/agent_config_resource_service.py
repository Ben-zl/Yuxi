"""Agent 配置资源授权用例。"""

from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.permissions.agent_config_resource import authorize_agent_config_resources as authorize_resource_policy
from yuxi.storage.postgres.models_business import User


async def authorize_agent_config_resources(
    config_json: dict | None,
    *,
    db: AsyncSession,
    user: User,
    agent_share_config: dict,
    owner_uid: str,
) -> dict:
    """在用例层执行固定的 Agent 配置资源授权策略。"""
    return await authorize_resource_policy(
        config_json,
        db=db,
        user=user,
        agent_share_config=agent_share_config,
        owner_uid=owner_uid,
    )
