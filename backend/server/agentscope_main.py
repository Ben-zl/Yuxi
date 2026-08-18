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
from fastapi import File, Header, HTTPException, Query, UploadFile, status

from yuxi.storage.postgres.manager import pg_manager
from agentscope.app import create_app
from agentscope.app.message_bus import RedisMessageBus
from agentscope.app.storage import AsyncSQLAlchemyStorage
from agentscope.app.workspace_manager import (
    DockerWorkspaceManager,
    IsolationPolicy,
    LocalWorkspaceManager,
)
from yuxi.agentscope.runtime_models import register_yuxi_credentials

AGENTSCOPE_DATABASE_URL = os.getenv(
    "AGENTSCOPE_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@postgres:5432/agentscope",
)
# message bus 复用全栈共享的 REDIS_URL，不为 agentscope 单独配置
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
AGENTSCOPE_WORKSPACE_BASEDIR = os.getenv("AGENTSCOPE_WORKSPACE_BASEDIR", "/app/saves/agentscope-workspaces")
# docker 后端的 basedir 必须是 docker daemon 视角的路径（Docker Desktop 下
# 为宿主路径，需将同一路径自镜像挂载进本容器）
AGENTSCOPE_WORKSPACE_BACKEND = os.getenv("AGENTSCOPE_WORKSPACE_BACKEND", "docker")


async def _ensure_database_exists(database_url: str) -> None:
    """连接维护库，目标 database 不存在时创建。

    幂等：兼容已有数据卷的 dev 环境与全新初始化的环境。
    """
    parsed = urlparse(database_url)
    database = parsed.path.lstrip("/")
    auth = f"{quote(parsed.username or '')}:{quote(parsed.password or '')}@"
    maintenance_dsn = f"postgresql://{auth}{parsed.hostname}:{parsed.port or 5432}/postgres"
    conn = await asyncpg.connect(maintenance_dsn)
    try:
        exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", database)
        if not exists:
            await conn.execute(f'CREATE DATABASE "{database}"')
    finally:
        await conn.close()


async def _extra_agent_middlewares(user_id: str, agent_id: str, session_id: str) -> list:
    """每轮 chat 注入观测中间件（OTel GenAI 语义；未配置 provider 时近零开销）。

    Langfuse 对接：设置 OTEL_EXPORTER_OTLP_ENDPOINT 等 OTel 环境变量即可
    （Langfuse v3 原生收 OTLP），主链路 reply/model/tool span 自动上报。
    """
    from agentscope.middleware import TracingMiddleware

    return [TracingMiddleware()]


async def _extra_agent_tools(user_id: str, agent_id: str, session_id: str) -> list:
    """每轮 chat 按会话注入 yuxi 工具（KB 工具按可见性，LITE 自动裁剪）。"""
    from yuxi.agentscope.tools import (
        build_extra_tools,
        build_kb_tools,
        build_mcp_tools,
        build_shared_workspace_tools,
        build_skill_dependency_gateway,
        build_subagent_tools,
    )
    from yuxi.agentscope.runtime_resources import resolve_runtime_projection

    async with pg_manager.get_async_session_context() as db:
        projection = await resolve_runtime_projection(
            db,
            app.state.storage,
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
        )

    session = await app.state.storage.get_session(user_id, agent_id, session_id)
    if session is None:
        raise ValueError("AgentScope 会话不存在，无法装配运行时工具")
    workspace = await app.state.workspace_manager.get_workspace(
        user_id,
        agent_id,
        session_id,
        session.config.workspace_id,
    )

    tools = await build_kb_tools(uid=user_id, knowledge_slugs=projection.knowledge_slugs)
    tools.extend(await build_mcp_tools(mcp_servers=projection.mcp_servers))
    tools.extend(
        await build_extra_tools(
            uid=user_id,
            knowledge_slugs=projection.knowledge_slugs,
            agent_id=agent_id,
            session_id=session_id,
            tool_slugs=projection.tool_slugs,
            workspace=workspace,
        )
    )
    tools.extend(build_shared_workspace_tools(user_id))
    tools.extend(
        await build_subagent_tools(
            storage=app.state.storage,
            message_bus=app.state.message_bus,
            workspace_manager=app.state.workspace_manager,
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            templates=projection.subagent_templates,
        )
    )
    tools.extend(
        await build_skill_dependency_gateway(
            projection=projection,
            workspace=workspace,
            uid=user_id,
            agent_id=agent_id,
            session_id=session_id,
        )
    )
    return tools


def _setup_otel_if_configured() -> None:
    """按 OTel 标准环境变量初始化全局 TracerProvider（未配置则跳过）。

    TracingMiddleware 使用全局 tracer；OTLP exporter 自动读取
    OTEL_EXPORTER_OTLP_ENDPOINT / OTEL_EXPORTER_OTLP_HEADERS——
    Langfuse v3+ 原生收 OTLP（Basic 认证 = 项目 pk:sk）。
    """
    if not os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    provider = TracerProvider(
        resource=Resource.create({"service.name": os.getenv("OTEL_SERVICE_NAME", "yuxi-agentscope")})
    )
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)


def _create_service_app_sync():
    """构造 agentscope service app，并在构造前确保独立 database 存在。

    uvicorn --reload 会在已运行的事件循环内导入模块，因此建库
    不能直接 asyncio.run，放到无运行循环的工作线程中执行。
    持久存储指向独立 database，实时 bus 走 Redis，workspace 先用本地目录。
    """
    bootstrap_error = []

    async def _bootstrap() -> None:
        await _ensure_database_exists(AGENTSCOPE_DATABASE_URL)
        # initialize 需在事件循环内执行（内部创建 asyncpg/psycopg 连接池）；
        # 用毕立即释放并复位，避免连接池绑定 bootstrap 线程的循环——
        # 服务主循环内的首次使用（extra_agent_tools）会按需重新初始化
        pg_manager.initialize()
        await pg_manager.ensure_business_schema()
        await pg_manager.reset()

    def _run_bootstrap():
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(_bootstrap())
        except Exception as exc:  # noqa: BLE001 - 线程内异常需带回主线程
            bootstrap_error.append(exc)
        finally:
            loop.close()

    register_yuxi_credentials()
    _setup_otel_if_configured()

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
        storage=AsyncSQLAlchemyStorage(
            AGENTSCOPE_DATABASE_URL,
            create_tables=True,
            engine_kwargs={"pool_pre_ping": True},
        ),
        message_bus=RedisMessageBus(
            host=redis_parsed.hostname or "redis",
            port=redis_parsed.port or 6379,
            db=int(redis_parsed.path.lstrip("/") or 0),
            password=redis_parsed.password,
        ),
        extra_agent_middlewares=_extra_agent_middlewares,
        extra_agent_tools=_extra_agent_tools,
        workspace_manager=workspace_manager,
        title="Yuxi AgentScope Service",
    )


app = _create_service_app_sync()


@app.post("/yuxi/workspace/file", status_code=status.HTTP_201_CREATED)
async def upload_yuxi_workspace_file(
    file: UploadFile = File(...),
    agent_id: str = Query(...),
    session_id: str = Query(...),
    destination: str = Query(...),
    x_user_id: str = Header(...),
) -> dict:
    """把 Yuxi 已授权附件写入指定会话的隔离 workspace。"""
    from urllib.parse import unquote
    from yuxi.agentscope.workspace_files import MAX_WORKSPACE_UPLOAD_BYTES, store_workspace_upload

    data = await file.read(MAX_WORKSPACE_UPLOAD_BYTES + 1)
    try:
        return await store_workspace_upload(
            app.state.workspace_service,
            user_id=unquote(x_user_id),
            agent_id=agent_id,
            session_id=session_id,
            destination=destination,
            data=data,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except OverflowError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc


@app.delete("/yuxi/workspace/output")
async def delete_yuxi_workspace_output(
    agent_id: str = Query(...),
    session_id: str = Query(...),
    path: str = Query(...),
    x_user_id: str = Header(...),
) -> dict:
    """删除 Yuxi 当前线程的产出文件或目录。"""
    from urllib.parse import unquote

    from yuxi.agentscope.workspace_files import delete_workspace_output

    try:
        return await delete_workspace_output(
            app.state.workspace_service,
            user_id=unquote(x_user_id),
            agent_id=agent_id,
            session_id=session_id,
            path=path,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="文件不存在") from exc


if __name__ == "__main__":
    uvicorn.run("server.agentscope_main:app", host="0.0.0.0", port=8100)
