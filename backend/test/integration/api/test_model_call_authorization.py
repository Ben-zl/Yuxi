"""真实 HTTP 与 PostgreSQL 验证简单模型调用的资源边界。"""

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

from server.routers.chat_router import chat
from server.utils.auth_middleware import get_db, get_required_user
from yuxi.services import model_call_service
from yuxi.storage.postgres.models_business import ModelProvider


async def test_call_checks_current_scope_over_real_http_and_postgres(monkeypatch):
    """隔离事务与 HTTP 监听器覆盖移动、旧引用、歧义和停用。"""
    engine = create_async_engine(os.environ["POSTGRES_URL"])
    invoked = []
    user = SimpleNamespace(uid=str(uuid4()), role="admin", department_id=10)

    class Model:
        """记录授权后调用，不连接外部供应商。"""

        async def call(self, query):
            """返回确定性模型输出。"""
            invoked.append(query)
            return SimpleNamespace(content="authorized answer")

    monkeypatch.setattr(model_call_service, "select_model", lambda **kwargs: Model())
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            async with AsyncSession(bind=connection, expire_on_commit=False) as db:
                provider = ModelProvider(
                    provider_id=f"probe-{uuid4().hex}",
                    display_name="Authorization probe",
                    base_url="https://example.invalid/v1",
                    created_by=user.uid,
                    enabled_models=[{"id": "model", "type": "chat"}],
                    is_enabled=True,
                    share_config={
                        "version": 2,
                        "read_scope": {"access_level": "department", "department_ids": [20]},
                        "manage_scope": None,
                    },
                )
                db.add(provider)
                await db.flush()

                async def database():
                    """请求复用仅本测试可见的事务。"""
                    yield db

                app = FastAPI()
                app.include_router(chat, prefix="/api")
                app.dependency_overrides[get_db] = database
                app.dependency_overrides[get_required_user] = lambda: user
                with socket.socket() as listener:
                    listener.bind(("127.0.0.1", 0))
                    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
                    task = asyncio.create_task(server.serve(sockets=[listener]))
                    try:
                        async with asyncio.timeout(10):
                            while not server.started:
                                if task.done():
                                    await task
                                await asyncio.sleep(0.01)
                        async with httpx.AsyncClient(
                            base_url=f"http://127.0.0.1:{listener.getsockname()[1]}",
                            trust_env=False,
                        ) as client:
                            for reference in (provider.resource_id, provider.provider_id):
                                response = await client.post(
                                    "/api/chat/call",
                                    json={"query": "denied", "meta": {"model_spec": f"{reference}:model"}},
                                )
                                assert response.status_code == 404
                                assert response.json() == {"detail": "模型不存在或无权访问"}
                            assert invoked == []
                            provider.share_config = {
                                "version": 2,
                                "read_scope": {"access_level": "global"},
                                "manage_scope": None,
                            }
                            await db.flush()
                            response = await client.post(
                                "/api/chat/call",
                                json={
                                    "query": "allowed",
                                    "meta": {"model": f"{provider.provider_id}:model", "request_id": "probe"},
                                },
                            )
                            assert response.json() == {"response": "authorized answer", "request_id": "probe"}
                            assert invoked == ["allowed"]
                            duplicate = ModelProvider(
                                provider_id=provider.provider_id,
                                display_name="Duplicate",
                                base_url="https://example.invalid/v1",
                            )
                            db.add(duplicate)
                            await db.flush()
                            response = await client.post(
                                "/api/chat/call",
                                json={"query": "ambiguous", "meta": {"model": f"{provider.provider_id}:model"}},
                            )
                            assert response.status_code == 404
                            provider.is_enabled = False
                            await db.flush()
                            response = await client.post(
                                "/api/chat/call",
                                json={"query": "disabled", "meta": {"model_spec": f"{provider.resource_id}:model"}},
                            )
                            assert response.status_code == 404
                            assert response.json() == {"detail": "模型不可用"}
                            assert invoked == ["allowed"]
                    finally:
                        server.should_exit = True
                        await asyncio.wait_for(task, timeout=10)
                resource_id = provider.resource_id
            await transaction.rollback()
            assert (
                await connection.execute(select(ModelProvider.id).where(ModelProvider.resource_id == resource_id))
            ).scalar_one_or_none() is None
    finally:
        await engine.dispose()
