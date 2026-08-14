"""agentscope service 骨架入口（agentscope 承接迁移 · 工单 01）。

在 worker 容器内运行 agentscope app：PostgreSQL 独立 database 作持久存储，
Redis 作 message bus，本地目录作 workspace（Docker workspace 由沙盒工单接入）。
鉴权使用其内置的 X-User-ID 头；接入 yuxi 网关后由网关注入统一身份。
"""

import asyncio
import os
import threading
from urllib.parse import quote, urlparse

import asyncpg
import uvicorn
from agentscope.app import create_app
from agentscope.app.message_bus import RedisMessageBus
from agentscope.app.storage import AsyncSQLAlchemyStorage
from agentscope.app.workspace_manager import LocalWorkspaceManager

AGENTSCOPE_DATABASE_URL = os.getenv(
    "AGENTSCOPE_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@postgres:5432/agentscope",
)
# message bus 复用全栈共享的 REDIS_URL，不为 agentscope 单独配置
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
AGENTSCOPE_WORKSPACE_BASEDIR = os.getenv(
    "AGENTSCOPE_WORKSPACE_BASEDIR", "/app/saves/agentscope-workspaces"
)


async def _ensure_database_exists(database_url: str) -> None:
    """连接维护库，目标 database 不存在时创建。

    幂等：兼容已有数据卷的 dev 环境与全新初始化的环境。
    """
    parsed = urlparse(database_url)
    database = parsed.path.lstrip("/")
    auth = f"{quote(parsed.username or '')}:{quote(parsed.password or '')}@"
    maintenance_dsn = (
        f"postgresql://{auth}{parsed.hostname}:{parsed.port or 5432}/postgres"
    )
    conn = await asyncpg.connect(maintenance_dsn)
    try:
        exists = await conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", database
        )
        if not exists:
            await conn.execute(f'CREATE DATABASE "{database}"')
    finally:
        await conn.close()


def _create_service_app_sync():
    """构造 agentscope service app，并在构造前确保独立 database 存在。

    uvicorn --reload 会在已运行的事件循环内导入模块，因此建库
    不能直接 asyncio.run，放到无运行循环的工作线程中执行。
    持久存储指向独立 database，实时 bus 走 Redis，workspace 先用本地目录。
    """
    bootstrap_error = []

    def _run_bootstrap():
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(_ensure_database_exists(AGENTSCOPE_DATABASE_URL))
        except Exception as exc:  # noqa: BLE001 - 线程内异常需带回主线程
            bootstrap_error.append(exc)
        finally:
            loop.close()

    bootstrap_thread = threading.Thread(target=_run_bootstrap)
    bootstrap_thread.start()
    bootstrap_thread.join()
    if bootstrap_error:
        raise bootstrap_error[0]

    redis_parsed = urlparse(REDIS_URL)
    return create_app(
        storage=AsyncSQLAlchemyStorage(AGENTSCOPE_DATABASE_URL, create_tables=True),
        message_bus=RedisMessageBus(
            host=redis_parsed.hostname or "redis",
            port=redis_parsed.port or 6379,
            db=int(redis_parsed.path.lstrip("/") or 0),
            password=redis_parsed.password,
        ),
        workspace_manager=LocalWorkspaceManager(basedir=AGENTSCOPE_WORKSPACE_BASEDIR),
        title="Yuxi AgentScope Service",
    )


app = _create_service_app_sync()


if __name__ == "__main__":
    uvicorn.run("server.agentscope_main:app", host="0.0.0.0", port=8100)
