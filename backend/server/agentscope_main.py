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
from agentscope.app.channel import WPSXiezuoChannel
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
AGENTSCOPE_MEMORY_BASEDIR = os.getenv("AGENTSCOPE_MEMORY_BASEDIR", "/app/saves/memory/reme")


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


async def _finalize_team_worker_setup_failure(
    user_id: str,
    agent_id: str,
    session_id: str,
    error: Exception,
) -> None:
    """尽力结束 AgentScope 准备阶段失败的 Team child Run。"""
    from yuxi.agentscope.team_lifecycle import TeamLifecycleModule
    from yuxi.utils.logging_config import logger

    try:
        finalized = await TeamLifecycleModule(
            storage=app.state.storage,
            uid=user_id,
            agent_id=agent_id,
            session_id=session_id,
        ).fail_setup()
    except Exception:
        logger.exception(
            "Team worker 准备失败后的 child Run 收口失败 session=%s",
            session_id,
        )
        return
    if finalized:
        logger.warning(
            "Team worker 会话准备失败，已结束 child Run session=%s cause=%s",
            session_id,
            type(error).__name__,
        )


async def _extra_agent_middlewares(user_id: str, agent_id: str, session_id: str) -> list:
    """装配额外 middleware，并收口准备阶段失败的 Team child Run。"""
    try:
        return await _build_extra_agent_middlewares(user_id, agent_id, session_id)
    except Exception as error:
        await _finalize_team_worker_setup_failure(user_id, agent_id, session_id, error)
        raise


async def _build_extra_agent_middlewares(user_id: str, agent_id: str, session_id: str) -> list:
    """每轮 chat 注入当前提示词、观测和运行控制中间件。

    Langfuse 对接：设置 OTEL_EXPORTER_OTLP_ENDPOINT 等 OTel 环境变量即可
    （Langfuse v3 原生收 OTLP），主链路 reply/model/tool span 自动上报。
    """
    from agentscope.middleware import TracingMiddleware
    from yuxi.agentscope.middleware import (
        NativeScheduleBlockMiddleware,
        RuntimeSystemPromptMiddleware,
        build_context_observability_middleware,
        build_steer_middleware,
        build_team_lifecycle_middleware,
    )
    from yuxi.agentscope.runtime_resources import resolve_runtime_projection
    from yuxi.agentscope.memory import (
        build_scoped_reme_middleware,
        memory_scope_identity,
        validate_memory_workspace,
    )
    from yuxi.agentscope.memory_management import latest_memory_at
    from yuxi.repositories.agent_memory_scope_repository import AgentMemoryScopeRepository

    async with pg_manager.get_async_session_context() as db:
        projection = await resolve_runtime_projection(
            db,
            app.state.storage,
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
        )
        if projection.memory_enabled:
            await AgentMemoryScopeRepository(db).ensure(
                user_id,
                projection.agent_slug,
                memory_scope_identity(user_id, projection.agent_slug),
            )
            await db.commit()

    middlewares = [
        TracingMiddleware(),
        RuntimeSystemPromptMiddleware(projection.agent_request["system_prompt"]),
        NativeScheduleBlockMiddleware(),
        build_context_observability_middleware(app.state.message_bus, session_id),
    ]
    if projection.memory_enabled:

        async def mark_memory_updated() -> None:
            """仅在 ReMe 已实际生成卡片时推进 scope 最近记忆时间。"""
            memory_at = latest_memory_at(
                validate_memory_workspace(AGENTSCOPE_MEMORY_BASEDIR, user_id, projection.agent_slug),
            )
            if memory_at is None:
                return
            async with pg_manager.get_async_session_context() as update_db:
                repo = AgentMemoryScopeRepository(update_db)
                record = await repo.get(user_id, projection.agent_slug)
                if record is not None:
                    await repo.mark_memory_at(record, memory_at)
                    await update_db.commit()

        memory_middleware = build_scoped_reme_middleware(
            projection,
            registry=app.state.reme_registry,
            uid=user_id,
            on_memory_updated=mark_memory_updated,
        )
        if memory_middleware is not None:
            middlewares.append(memory_middleware)
    session = await app.state.storage.get_session(user_id, agent_id, session_id)
    source = getattr(session, "source", None) if session is not None else None
    if getattr(source, "value", source) == "channel":
        from yuxi.agentscope.channel_middleware import (
            build_channel_run_mirror_middleware,
        )

        middlewares.append(
            build_channel_run_mirror_middleware(
                uid=user_id,
                agent_id=agent_id,
                session_id=session_id,
            ),
        )
    team_lifecycle = await build_team_lifecycle_middleware(
        app.state.storage,
        app.state.message_bus,
        app.state.workspace_manager,
        user_id,
        agent_id,
        session_id,
    )
    if team_lifecycle is not None:
        middlewares.append(team_lifecycle)
    steer = await build_steer_middleware(user_id, agent_id, session_id)
    if steer is not None:
        middlewares.append(steer)
    return middlewares


async def _extra_agent_tools(user_id: str, agent_id: str, session_id: str) -> list:
    """装配额外工具，并收口准备阶段失败的 Team child Run。"""
    try:
        return await _build_extra_agent_tools(user_id, agent_id, session_id)
    except Exception as error:
        await _finalize_team_worker_setup_failure(user_id, agent_id, session_id, error)
        raise


async def _build_extra_agent_tools(user_id: str, agent_id: str, session_id: str) -> list:
    """每轮 chat 按会话注入 yuxi 工具（KB 工具按可见性，LITE 自动裁剪）。"""
    from yuxi.agentscope.tools import (
        build_extra_tools,
        filter_runtime_tools_for_role,
        build_kb_tools,
        build_mcp_tools,
        build_shared_workspace_tools,
        build_skill_dependency_gateway,
        build_subagent_tools,
    )
    from yuxi.agentscope.runtime_resources import (
        resolve_runtime_projection,
        sync_runtime_skills,
    )

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
    await sync_runtime_skills(workspace, projection, agent_id=agent_id)

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
    return filter_runtime_tools_for_role(
        tools,
        is_team_worker=projection.is_team_worker,
    )


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
    if not os.getenv("AGENTSCOPE_CHANNEL_CREDENTIAL_KEY"):
        raise RuntimeError(
            "注册 WPS 协作 Channel 必须配置 AGENTSCOPE_CHANNEL_CREDENTIAL_KEY",
        )

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
    service_app = create_app(
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
        channels=[WPSXiezuoChannel],
        workspace_manager=workspace_manager,
        title="Yuxi AgentScope Service",
    )
    from yuxi.agentscope.memory import ReMeRegistry, ensure_memory_base_dir
    from yuxi.agentscope.memory_compaction_scheduler import MemoryCompactionScheduler
    from yuxi.agentscope.memory_service import AgentMemoryService
    from yuxi.agentscope.memory_scheduler import MemoryDreamScheduler

    memory_base_dir = ensure_memory_base_dir(AGENTSCOPE_MEMORY_BASEDIR)
    service_app.state.reme_registry = ReMeRegistry(base_dir=memory_base_dir)
    service_app.state.agent_memory_service = AgentMemoryService(
        registry=service_app.state.reme_registry,
        base_dir=memory_base_dir,
    )
    service_app.state.memory_dream_scheduler = MemoryDreamScheduler(
        registry=service_app.state.reme_registry,
        base_dir=memory_base_dir,
    )
    service_app.state.memory_compaction_scheduler = MemoryCompactionScheduler(
        registry=service_app.state.reme_registry,
        memory_service=service_app.state.agent_memory_service,
        base_dir=memory_base_dir,
    )
    original_lifespan = service_app.router.lifespan_context

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def yuxi_lifespan(current_app):
        """在 AgentScope 原生资源关闭前释放 Yuxi 长期记忆资源。"""
        async with original_lifespan(current_app):
            from yuxi.config import config

            config.start_runtime_sync()
            await current_app.state.memory_dream_scheduler.start()
            await current_app.state.memory_compaction_scheduler.start()
            try:
                yield
            finally:
                await current_app.state.memory_compaction_scheduler.stop()
                await current_app.state.memory_dream_scheduler.stop()
                await current_app.state.reme_registry.close_all()

    service_app.router.lifespan_context = yuxi_lifespan
    return service_app


app = _create_service_app_sync()


def _memory_busy() -> HTTPException:
    """返回统一的 scope 忙错误。"""
    return HTTPException(status_code=409, detail="memory_scope_busy")


@app.get("/yuxi/memory")
async def list_yuxi_memories(
    agent_slug: str = Query(...),
    kind: str = Query("all", pattern="^(all|daily|digest)$"),
    category: str = Query("all", pattern="^(all|personal|procedure|wiki)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    x_user_id: str = Header(...),
) -> dict:
    """列出当前用户在一个 Agent scope 下可管理的记忆卡片。"""
    from urllib.parse import unquote

    from yuxi.agentscope.memory import MemoryScopeBusyError

    uid = unquote(x_user_id)
    try:
        return await app.state.agent_memory_service.list_memories(
            uid,
            agent_slug,
            kind=kind,
            category=category,
            page=page,
            page_size=page_size,
        )
    except MemoryScopeBusyError as exc:
        raise _memory_busy() from exc


@app.delete("/yuxi/memory/item")
async def delete_yuxi_memory_item(
    agent_slug: str = Query(...),
    memory_id: str = Query(..., pattern="^[0-9a-f]{64}$"),
    x_user_id: str = Header(...),
) -> dict:
    """删除一张记忆卡片，并在提交文件删除前完成完整 reindex。"""
    from urllib.parse import unquote

    from yuxi.agentscope.memory import MemoryScopeBusyError

    uid = unquote(x_user_id)
    try:
        await app.state.agent_memory_service.delete_item(uid, agent_slug, memory_id)
    except MemoryScopeBusyError as exc:
        raise _memory_busy() from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="memory_not_found") from exc
    return {"success": True}


@app.delete("/yuxi/memory/scope")
async def clear_yuxi_memory_scope(
    agent_slug: str = Query(...),
    x_user_id: str = Header(...),
) -> dict:
    """清空当前用户在指定 Agent 下的全部长期记忆。"""
    from urllib.parse import unquote
    from yuxi.agentscope.memory import MemoryScopeBusyError

    try:
        removed = await app.state.agent_memory_service.clear_scope(unquote(x_user_id), agent_slug)
    except MemoryScopeBusyError as exc:
        raise _memory_busy() from exc
    return {"success": True, "already_absent": not removed}


@app.delete("/yuxi/memory/agent")
async def clear_yuxi_memory_agent(
    agent_slug: str = Query(...),
    x_user_id: str = Header(...),
) -> dict:
    """清除一个 Agent 在所有用户下的长期记忆。"""
    from yuxi.agentscope.memory import MemoryScopeBusyError

    try:
        return await app.state.agent_memory_service.clear_agent(agent_slug)
    except MemoryScopeBusyError as exc:
        raise _memory_busy() from exc


@app.delete("/yuxi/memory/user")
async def clear_yuxi_memory_user(
    uid: str = Query(...),
    x_user_id: str = Header(...),
) -> dict:
    """清除一个用户在所有 Agent 下的长期记忆。"""
    from yuxi.agentscope.memory import MemoryScopeBusyError

    try:
        return await app.state.agent_memory_service.clear_user(uid)
    except MemoryScopeBusyError as exc:
        raise _memory_busy() from exc


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


@app.delete("/yuxi/workspace/thread")
async def delete_yuxi_thread_workspaces(
    thread_id: str = Query(...),
    x_user_id: str = Header(...),
) -> dict:
    """在 AgentScope Session 删除后清理线程的全部物理 Workspace。"""
    from urllib.parse import unquote

    from yuxi.agentscope.workspace_cleanup import (
        WorkspaceCleanupConflict,
        destroy_thread_workspaces,
    )

    try:
        async with pg_manager.get_async_session_context() as db:
            return await destroy_thread_workspaces(
                db,
                storage=app.state.storage,
                workspace_manager=app.state.workspace_manager,
                uid=unquote(x_user_id),
                thread_id=thread_id,
                base_dir=AGENTSCOPE_WORKSPACE_BASEDIR,
                backend=AGENTSCOPE_WORKSPACE_BACKEND,
            )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except WorkspaceCleanupConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Workspace 清理失败: {exc}") from exc


if __name__ == "__main__":
    uvicorn.run("server.agentscope_main:app", host="0.0.0.0", port=8100)
