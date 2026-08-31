"""AgentScope 跨版本 Session/消息/Workspace 升级演练服务。"""

import os
from urllib.parse import urlparse

from agentscope.app import create_app
from agentscope.app.message_bus import RedisMessageBus
from agentscope.app.storage import AsyncSQLAlchemyStorage
from agentscope.app.workspace_manager import DockerWorkspaceManager, IsolationPolicy


def create_upgrade_test_app():
    """构造不注入 Yuxi 业务投影的最小持久化 AgentScope 服务。"""
    redis = urlparse(os.environ["REDIS_URL"])
    return create_app(
        storage=AsyncSQLAlchemyStorage(
            os.environ["AGENTSCOPE_DATABASE_URL"],
            create_tables=True,
            engine_kwargs={"pool_pre_ping": True},
        ),
        message_bus=RedisMessageBus(
            host=redis.hostname or "redis",
            port=redis.port or 6379,
            db=int(redis.path.lstrip("/") or 0),
            password=redis.password,
        ),
        workspace_manager=DockerWorkspaceManager(
            basedir=os.environ["AGENTSCOPE_WORKSPACE_BASEDIR"],
            isolation=IsolationPolicy.PER_SESSION,
        ),
        title="AgentScope Upgrade E2E",
    )


app = create_upgrade_test_app()
