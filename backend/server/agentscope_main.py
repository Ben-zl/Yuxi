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

from yuxi.storage.postgres.manager import pg_manager
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
# yuxi 挂载的 saves 目录（Skill 源目录相对其解析）
AGENTSCOPE_SAVE_DIR = os.getenv("YUXI_SAVE_DIR", "/app/saves")
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


async def _thread_knowledge_slugs(agentscope_agent_id: str) -> list[str] | None:
    """按 agentscope agent_id 反查线程的会话启用知识库集（None=全部）。"""
    from yuxi.agentscope.projection import agent_context
    from yuxi.repositories.agentscope_thread_sessions import (
        get_thread_session_by_agentscope_agent,
    )
    from yuxi.storage.postgres.models_business import Agent
    from sqlalchemy import select

    async with pg_manager.get_async_session_context() as db:
        mapping = await get_thread_session_by_agentscope_agent(
            db, agentscope_agent_id=agentscope_agent_id
        )
        if mapping is None:
            return None
        row = (
            await db.execute(select(Agent).where(Agent.slug == mapping.agent_slug))
        ).scalar_one_or_none()
        if row is None:
            return None
        knowledges = agent_context(row).get("knowledges")
        return None if knowledges is None else list(knowledges)


async def _extra_agent_tools(user_id: str, agent_id: str, session_id: str) -> list:
    """每轮 chat 按会话注入 yuxi 工具（KB 工具按可见性，LITE 自动裁剪）。"""
    from yuxi.agentscope.tools import build_kb_tools, build_web_search_tool

    tools = await build_kb_tools(
        uid=user_id, knowledge_slugs=await _thread_knowledge_slugs(agent_id)
    )
    web_search_tool = build_web_search_tool()
    if web_search_tool is not None:
        tools.append(web_search_tool)
    return tools


async def _resolve_skill_paths() -> list[str]:
    """读取启用的 Skill 源目录（相对 save_dir），作为 workspace 技能种子。

    渐进披露由 agentscope 的 SkillViewer 机制承担（模型按需查看
    SKILL.md），替代旧栈「读到激活」语义；目录在服务启动期快照。
    """
    import os

    from sqlalchemy import select

    from yuxi.storage.postgres.manager import pg_manager
    from yuxi.storage.postgres.models_business import Skill

    async with pg_manager.get_async_session_context() as db:
        rows = (
            await db.execute(select(Skill.slug, Skill.dir_path).where(Skill.enabled.is_(True)))
        ).all()
    paths = []
    for _, dir_path in rows:
        absolute = os.path.join(AGENTSCOPE_SAVE_DIR, dir_path)
        if os.path.isdir(absolute):
            paths.append(absolute)
    return paths


def _create_service_app_sync():
    """构造 agentscope service app，并在构造前确保独立 database 存在。

    uvicorn --reload 会在已运行的事件循环内导入模块，因此建库
    不能直接 asyncio.run，放到无运行循环的工作线程中执行。
    持久存储指向独立 database，实时 bus 走 Redis，workspace 先用本地目录。
    """
    bootstrap_error = []
    skill_paths: list[str] = []

    async def _bootstrap() -> list[str]:
        await _ensure_database_exists(AGENTSCOPE_DATABASE_URL)
        # initialize 需在事件循环内执行（内部创建 asyncpg/psycopg 连接池）
        pg_manager.initialize()
        await pg_manager.ensure_business_schema()
        return await _resolve_skill_paths()

    def _run_bootstrap():
        loop = asyncio.new_event_loop()
        try:
            skill_paths.extend(loop.run_until_complete(_bootstrap()))
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
            skill_paths=skill_paths,
        )
    else:
        workspace_manager = LocalWorkspaceManager(
            basedir=AGENTSCOPE_WORKSPACE_BASEDIR, skill_paths=skill_paths
        )
    return create_app(
        storage=AsyncSQLAlchemyStorage(AGENTSCOPE_DATABASE_URL, create_tables=True),
        message_bus=RedisMessageBus(
            host=redis_parsed.hostname or "redis",
            port=redis_parsed.port or 6379,
            db=int(redis_parsed.path.lstrip("/") or 0),
            password=redis_parsed.password,
        ),
        extra_agent_tools=_extra_agent_tools,
        workspace_manager=workspace_manager,
        title="Yuxi AgentScope Service",
    )


app = _create_service_app_sync()


if __name__ == "__main__":
    uvicorn.run("server.agentscope_main:app", host="0.0.0.0", port=8100)
