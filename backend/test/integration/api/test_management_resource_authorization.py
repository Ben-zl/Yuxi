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

from server.routers.mcp_router import mcp
from server.routers.model_provider_router import model_providers
from server.utils.auth_middleware import get_admin_user, get_db, get_required_user
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
