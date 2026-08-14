"""agentscope service 骨架 e2e（迁移工单 01）。

验证 worker 容器内 agentscope service 的基础回路：
健康检查、agent 创建、session 创建与读取，
以及会话事实落在 PostgreSQL 独立 database 且不外溢到业务库。
默认地址面向 compose 内执行（docker compose exec api pytest），
宿主执行时通过 AGENTSCOPE_BASE_URL / AGENTSCOPE_DATABASE_URL 覆盖。
"""

import os
from urllib.parse import quote, urlparse

import asyncpg
import httpx
import pytest

AGENTSCOPE_BASE_URL = os.getenv(
    "AGENTSCOPE_BASE_URL", "http://agentscope:8100"
).rstrip("/")
AGENTSCOPE_DATABASE_URL = os.getenv(
    "AGENTSCOPE_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@postgres:5432/agentscope",
)
BUSINESS_DATABASE = os.getenv("POSTGRES_DB", "yuxi")
HEADERS = {"X-User-ID": "e2e-skeleton"}

pytestmark = pytest.mark.e2e


def _pg_dsn(database: str) -> str:
    """从 agentscope 存储 URL 推导指定 database 的 asyncpg DSN。"""
    parsed = urlparse(AGENTSCOPE_DATABASE_URL)
    auth = f"{quote(parsed.username or '')}:{quote(parsed.password or '')}@"
    return f"postgresql://{auth}{parsed.hostname}:{parsed.port or 5432}/{database}"


@pytest.fixture
async def client():
    async with httpx.AsyncClient(
        base_url=AGENTSCOPE_BASE_URL, headers=HEADERS, timeout=30.0
    ) as client:
        yield client


async def _create_session(client: httpx.AsyncClient) -> str:
    agent_resp = await client.post("/agent/", json={"name": "skeleton-e2e"})
    assert agent_resp.status_code == 201, agent_resp.text
    agent_id = agent_resp.json()["agent_id"]

    session_resp = await client.post(
        "/sessions/", json={"agent_id": agent_id, "name": "skeleton-e2e"}
    )
    assert session_resp.status_code == 201, session_resp.text
    return session_resp.json()["session_id"]


async def test_health_reports_ready(client: httpx.AsyncClient):
    resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    for component in ("storage", "message_bus", "workspace_manager"):
        assert body["components"][component] == "ok", body["components"]


async def test_agent_and_session_roundtrip(client: httpx.AsyncClient):
    agent_resp = await client.post("/agent/", json={"name": "skeleton-e2e"})
    assert agent_resp.status_code == 201, agent_resp.text
    agent_id = agent_resp.json()["agent_id"]

    session_resp = await client.post(
        "/sessions/", json={"agent_id": agent_id, "name": "skeleton-e2e"}
    )
    assert session_resp.status_code == 201, session_resp.text
    session_id = session_resp.json()["session_id"]

    list_resp = await client.get("/sessions/", params={"agent_id": agent_id})
    assert list_resp.status_code == 200, list_resp.text
    listed = list_resp.json()["sessions"]
    listed_ids = {item["session"]["id"] for item in listed}
    assert session_id in listed_ids
    assert listed[0]["session"]["user_id"] == HEADERS["X-User-ID"]


async def test_session_facts_in_independent_database(client: httpx.AsyncClient):
    session_id = await _create_session(client)

    conn = await asyncpg.connect(_pg_dsn("agentscope"))
    try:
        row = await conn.fetchrow(
            "SELECT id, user_id FROM sessions WHERE id = $1", session_id
        )
        assert row is not None, "会话事实未落入 agentscope 独立 database"
        assert row["user_id"] == HEADERS["X-User-ID"]
    finally:
        await conn.close()

    business = await asyncpg.connect(_pg_dsn(BUSINESS_DATABASE))
    try:
        leaked = await business.fetchval("SELECT to_regclass('public.sessions')")
        assert leaked is None, "agentscope 会话表不应出现在业务库"
    finally:
        await business.close()
