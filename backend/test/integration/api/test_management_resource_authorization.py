"""真实 HTTP 与 PostgreSQL 验证资源管理型探针要求 MANAGE。"""

import asyncio
import os
import socket
from types import SimpleNamespace
from uuid import uuid4

import httpx
import uvicorn
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from server.routers.knowledge_router import knowledge
from server.routers.mcp_router import mcp
from server.routers.model_provider_router import model_providers
from server.utils.auth_middleware import get_admin_user, get_db, get_required_user
from yuxi.knowledge.manager import authorize_knowledge_model_spec
from yuxi.knowledge.runtime import knowledge_base
from yuxi.storage.postgres.models_business import MCPServer, ModelProvider


async def test_department_admin_cannot_probe_global_provider_or_mcp_over_real_http(monkeypatch):
    """global 资源可读但连接测试、工具刷新和全局缓存刷新均不可管理。"""
    engine = create_async_engine(os.environ["POSTGRES_URL"])
    suffix = uuid4().hex
    user = SimpleNamespace(uid=f"department-admin-{suffix}", role="admin", department_id=10, username="admin")
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            async with AsyncSession(bind=connection, expire_on_commit=False) as db:
                provider = ModelProvider(
                    provider_id=f"global-provider-{suffix}",
                    display_name="Global Provider",
                    base_url="https://user:secret@example.test/v1?token=secret",
                    enabled_models=[{"id": "chat", "type": "chat"}],
                    is_enabled=True,
                    created_by="root",
                    share_config={
                        "version": 2,
                        "read_scope": {"access_level": "global", "department_ids": [], "user_uids": []},
                        "manage_scope": None,
                    },
                )
                server = MCPServer(
                    slug=f"global-mcp-{suffix}",
                    name="Global MCP",
                    transport="streamable_http",
                    url="https://user:secret@example.test/mcp?token=secret",
                    enabled=1,
                    created_by="root",
                    updated_by="root",
                    share_config={
                        "version": 2,
                        "read_scope": {"access_level": "global", "department_ids": [], "user_uids": []},
                        "manage_scope": None,
                    },
                )
                db.add_all([provider, server])
                await db.flush()

                from yuxi.models.providers.cache import model_cache

                monkeypatch.setattr(model_cache, "canonicalize_spec", lambda spec: spec)

                async def database():
                    yield db

                app = FastAPI()
                app.include_router(model_providers, prefix="/api")
                app.include_router(mcp, prefix="/api")
                app.dependency_overrides[get_db] = database
                app.dependency_overrides[get_required_user] = lambda: user
                app.dependency_overrides[get_admin_user] = lambda: user

                with socket.socket() as listener:
                    listener.bind(("127.0.0.1", 0))
                    api = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
                    task = asyncio.create_task(api.serve(sockets=[listener]))
                    try:
                        async with asyncio.timeout(10):
                            while not api.started:
                                if task.done():
                                    await task
                                await asyncio.sleep(0.01)
                        async with httpx.AsyncClient(
                            base_url=f"http://127.0.0.1:{listener.getsockname()[1]}",
                            trust_env=False,
                        ) as client:
                            paths = [
                                ("get", f"/api/system/model-providers/{provider.resource_id}/remote-models"),
                                ("get", f"/api/system/model-providers/models/status?spec={provider.resource_id}:chat"),
                                ("post", "/api/system/model-providers/models/cache/refresh"),
                                ("post", f"/api/system/mcp-servers/{server.resource_id}/test"),
                                ("get", f"/api/system/mcp-servers/{server.resource_id}/tools"),
                                ("post", f"/api/system/mcp-servers/{server.resource_id}/tools/refresh"),
                            ]
                            for method, path in paths:
                                response = await client.request(method, path)
                                assert response.status_code == 403, (path, response.text)
                                assert "example.test" not in response.text
                    finally:
                        api.should_exit = True
                        await asyncio.wait_for(task, timeout=10)

                provider_resource_id = provider.resource_id
                server_resource_id = server.resource_id
            await transaction.rollback()
            assert (
                await connection.execute(
                    select(ModelProvider.id).where(ModelProvider.resource_id == provider_resource_id)
                )
            ).scalar_one_or_none() is None
            assert (
                await connection.execute(select(MCPServer.id).where(MCPServer.resource_id == server_resource_id))
            ).scalar_one_or_none() is None
    finally:
        await engine.dispose()


async def test_provider_create_rejects_forged_builtin_flag_over_real_http():
    """公开 HTTP DTO 拒绝客户端伪造内置 Provider，且数据库不产生记录。"""
    engine = create_async_engine(os.environ["POSTGRES_URL"])
    suffix = uuid4().hex
    provider_id = f"forged-builtin-{suffix}"
    user = SimpleNamespace(uid=f"root-{suffix}", role="superadmin", department_id=None, username="root")
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            async with AsyncSession(bind=connection, expire_on_commit=False) as db:

                async def database():
                    yield db

                app = FastAPI()
                app.include_router(model_providers, prefix="/api")
                app.dependency_overrides[get_db] = database
                app.dependency_overrides[get_required_user] = lambda: user
                app.dependency_overrides[get_admin_user] = lambda: user

                with socket.socket() as listener:
                    listener.bind(("127.0.0.1", 0))
                    api = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
                    task = asyncio.create_task(api.serve(sockets=[listener]))
                    try:
                        async with asyncio.timeout(10):
                            while not api.started:
                                if task.done():
                                    await task
                                await asyncio.sleep(0.01)
                        async with httpx.AsyncClient(
                            base_url=f"http://127.0.0.1:{listener.getsockname()[1]}",
                            trust_env=False,
                        ) as client:
                            response = await client.post(
                                "/api/system/model-providers",
                                json={
                                    "provider_id": provider_id,
                                    "display_name": "Forged Builtin",
                                    "base_url": "https://example.invalid/v1",
                                    "is_builtin": True,
                                    "share_config": {
                                        "version": 2,
                                        "read_scope": {
                                            "access_level": "global",
                                            "department_ids": [],
                                            "user_uids": [],
                                        },
                                        "manage_scope": None,
                                    },
                                },
                            )
                            assert response.status_code == 422
                            assert response.json()["detail"][0]["loc"] == ["body", "is_builtin"]
                            assert response.json()["detail"][0]["type"] == "extra_forbidden"
                    finally:
                        api.should_exit = True
                        await asyncio.wait_for(task, timeout=10)

                assert (
                    await db.scalar(select(ModelProvider.id).where(ModelProvider.provider_id == provider_id))
                ) is None
            await transaction.rollback()
    finally:
        await engine.dispose()


async def test_department_admin_cannot_bind_other_department_provider_to_knowledge_base_over_real_http(monkeypatch):
    """知识库创建必须在持久化前拒绝跨部门模型资源。"""

    engine = create_async_engine(os.environ["POSTGRES_URL"])
    suffix = uuid4().hex
    user = SimpleNamespace(uid=f"department-b-{suffix}", role="admin", department_id=20, username="admin-b")
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            async with AsyncSession(bind=connection, expire_on_commit=False) as db:
                provider = ModelProvider(
                    provider_id=f"department-a-provider-{suffix}",
                    display_name="Department A Provider",
                    base_url="https://example.invalid/v1",
                    enabled_models=[{"id": "embedding", "type": "embedding"}],
                    is_enabled=True,
                    created_by=f"department-a-{suffix}",
                    share_config={
                        "version": 2,
                        "read_scope": {
                            "access_level": "department",
                            "department_ids": [10],
                            "user_uids": [],
                        },
                        "manage_scope": None,
                    },
                )
                db.add(provider)
                await db.flush()

                from yuxi.models.providers.cache import model_cache

                canonical_spec = f"{provider.resource_id}:embedding"
                monkeypatch.setattr(model_cache, "canonicalize_spec", lambda spec: canonical_spec)
                monkeypatch.setattr(
                    model_cache,
                    "get_model_info",
                    lambda spec: SimpleNamespace(
                        resource_id=provider.resource_id,
                        provider_id=provider.provider_id,
                        model_type="embedding",
                    ),
                )
                monkeypatch.setattr(knowledge_base, "database_name_exists", lambda name: asyncio.sleep(0, result=False))
                monkeypatch.setattr(
                    knowledge_base,
                    "_get_or_create_kb_instance",
                    lambda kb_type: SimpleNamespace(
                        requires_embedding_model=True,
                        normalize_additional_params=lambda params: params,
                    ),
                )

                async def database():
                    yield db

                app = FastAPI()
                app.include_router(knowledge, prefix="/api")
                app.dependency_overrides[get_db] = database
                app.dependency_overrides[get_admin_user] = lambda: user

                with socket.socket() as listener:
                    listener.bind(("127.0.0.1", 0))
                    api = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
                    task = asyncio.create_task(api.serve(sockets=[listener]))
                    try:
                        async with asyncio.timeout(10):
                            while not api.started:
                                if task.done():
                                    await task
                                await asyncio.sleep(0.01)
                        async with httpx.AsyncClient(
                            base_url=f"http://127.0.0.1:{listener.getsockname()[1]}",
                            trust_env=False,
                        ) as client:
                            response = await client.post(
                                "/api/knowledge/databases",
                                json={
                                    "database_name": f"department-b-kb-{suffix}",
                                    "description": "cross-department binding must fail",
                                    "embedding_model_spec": f"{provider.provider_id}:embedding",
                                    "kb_type": "milvus",
                                    "share_config": {
                                        "version": 2,
                                        "read_scope": {
                                            "access_level": "department",
                                            "department_ids": [20],
                                            "user_uids": [],
                                        },
                                        "manage_scope": None,
                                    },
                                },
                            )
                            assert response.status_code == 400, response.text
                            assert "模型资源不可访问" in response.json()["detail"]
                    finally:
                        api.should_exit = True
                        await asyncio.wait_for(task, timeout=10)
            await transaction.rollback()
    finally:
        await engine.dispose()


async def test_legacy_provider_id_cannot_collide_with_other_provider_resource_id(monkeypatch):
    """旧 provider_id 解析只能查询逻辑 ID，不能优先命中另一资源 UUID。"""

    engine = create_async_engine(os.environ["POSTGRES_URL"])
    collision_id = str(uuid4())
    user = SimpleNamespace(uid=f"department-b-{uuid4().hex}", role="admin", department_id=20)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            async with AsyncSession(bind=connection, expire_on_commit=False) as db:
                legacy_provider = ModelProvider(
                    resource_id=str(uuid4()),
                    provider_id=collision_id,
                    display_name="Restricted Legacy Provider",
                    base_url="https://restricted.example.invalid/v1",
                    enabled_models=[{"id": "embedding", "type": "embedding"}],
                    is_enabled=True,
                    created_by="department-a",
                    share_config={
                        "version": 2,
                        "read_scope": {
                            "access_level": "department",
                            "department_ids": [10],
                            "user_uids": [],
                        },
                        "manage_scope": None,
                    },
                )
                colliding_provider = ModelProvider(
                    resource_id=collision_id,
                    provider_id=f"visible-provider-{uuid4().hex}",
                    display_name="Visible Provider",
                    base_url="https://visible.example.invalid/v1",
                    enabled_models=[{"id": "embedding", "type": "embedding"}],
                    is_enabled=True,
                    created_by=user.uid,
                    share_config={
                        "version": 2,
                        "read_scope": {
                            "access_level": "department",
                            "department_ids": [20],
                            "user_uids": [],
                        },
                        "manage_scope": None,
                    },
                )
                db.add_all([legacy_provider, colliding_provider])
                await db.flush()

                from yuxi.models.providers.cache import model_cache

                monkeypatch.setattr(model_cache, "canonicalize_spec", lambda spec: f"{collision_id}:embedding")
                monkeypatch.setattr(
                    model_cache,
                    "get_model_info",
                    lambda spec: SimpleNamespace(
                        resource_id=collision_id,
                        provider_id=collision_id,
                        model_id="embedding",
                        model_type="embedding",
                    ),
                )

                try:
                    await authorize_knowledge_model_spec(
                        f"{collision_id}:embedding",
                        expected_type="embedding",
                        db=db,
                        user=user,
                        knowledge_share_config={
                            "version": 2,
                            "read_scope": {
                                "access_level": "department",
                                "department_ids": [20],
                                "user_uids": [],
                            },
                            "manage_scope": None,
                        },
                        owner_uid=user.uid,
                    )
                except ValueError as exc:
                    assert "不可访问" in str(exc)
                else:
                    raise AssertionError("legacy provider_id collision must not authorize another resource")
            await transaction.rollback()
    finally:
        await engine.dispose()
