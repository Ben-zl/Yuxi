"""agentscope service 骨架入口（agentscope 承接迁移 · 工单 01/06）。

在 worker 容器内运行 agentscope app：PostgreSQL 独立 database 作持久存储，
Redis 作 message bus，workspace 按 AGENTSCOPE_WORKSPACE_BACKEND 选择
本地目录（默认）或 Docker 容器（沙盒工单接入，按线程隔离）。
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
from agentscope.app.workspace_manager import (
    DockerWorkspaceManager,
    IsolationPolicy,
    LocalWorkspaceManager,
)

AGENTSCOPE_DATABASE_URL = os.getenv(
    "AGENTSCOPE_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@postgres:5432/agentscope",
)
# message bus 复用全栈共享的 REDIS_URL，不为 agentscope 单独配置
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
AGENTSCOPE_WORKSPACE_BASEDIR = os.getenv(
    "AGENTSCOPE_WORKSPACE_BASEDIR", "/app/saves/agentscope-workspaces"
)
# docker 后端的 basedir 必须是 docker daemon 视角的路径（Docker Desktop 下
# 为宿主路径，需将同一路径自镜像挂载进本容器）
AGENTSCOPE_WORKSPACE_BACKEND = os.getenv("AGENTSCOPE_WORKSPACE_BACKEND", "local")


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
    if AGENTSCOPE_WORKSPACE_BACKEND == "docker":
        # 会话与线程一一对应，per_session 即按线程隔离；同线程跨轮次复用
        workspace_manager = DockerWorkspaceManager(
            basedir=AGENTSCOPE_WORKSPACE_BASEDIR,
            isolation=IsolationPolicy.PER_SESSION,
        )
    else:
        workspace_manager = LocalWorkspaceManager(basedir=AGENTSCOPE_WORKSPACE_BASEDIR)
    return create_app(
        storage=AsyncSQLAlchemyStorage(AGENTSCOPE_DATABASE_URL, create_tables=True),
        message_bus=RedisMessageBus(
            host=redis_parsed.hostname or "redis",
            port=redis_parsed.port or 6379,
            db=int(redis_parsed.path.lstrip("/") or 0),
            password=redis_parsed.password,
        ),
        workspace_manager=workspace_manager,
        title="Yuxi AgentScope Service",
    )


app = _create_service_app_sync()


if __name__ == "__main__":
    uvicorn.run("server.agentscope_main:app", host="0.0.0.0", port=8100)
