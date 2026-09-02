"""长期记忆公开 API 的真实服务集成测试。"""

from __future__ import annotations

import os
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from sqlalchemy import delete, select

from yuxi.agentscope.memory import memory_scope_identity, memory_workspace_path
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import AgentMemoryScope, User

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

MEMORY_BASE_DIR = Path(os.getenv("AGENTSCOPE_MEMORY_BASEDIR", "/app/saves/memory/reme"))


async def _create_user(
    test_client: httpx.AsyncClient,
    admin_headers: dict[str, str],
    *,
    label: str,
) -> dict:
    """通过公开 API 创建并登录一个临时用户。"""
    departments = await test_client.get("/api/departments", headers=admin_headers)
    assert departments.status_code == 200 and departments.json(), departments.text
    password = f"Pw!{uuid.uuid4().hex[:12]}"
    response = await test_client.post(
        "/api/auth/users",
        headers=admin_headers,
        json={
            "username": f"pytest_memory_{label}_{uuid.uuid4().hex[:8]}",
            "password": password,
            "role": "user",
            "department_id": departments.json()[0]["id"],
        },
    )
    assert response.status_code == 200, response.text
    user = response.json()
    login = await test_client.post(
        "/api/auth/token",
        data={"username": user["uid"], "password": password},
    )
    assert login.status_code == 200, login.text
    return {
        "user": user,
        "headers": {"Authorization": f"Bearer {login.json()['access_token']}"},
    }


async def _seed_memory_scope(uid: str, agent_slug: str, marker: str) -> Path:
    """准备真实 scope catalog 和 Markdown 卡片，供公开 API 读取及删除。"""
    workspace = memory_workspace_path(MEMORY_BASE_DIR, uid, agent_slug)
    daily = workspace / "daily" / "2026-09-01"
    daily.mkdir(parents=True, exist_ok=True)
    (daily / "fact.md").write_text(
        f"---\nname: {marker}\ndescription: {marker} private memory\n---\n{marker}\n",
        encoding="utf-8",
    )
    async with pg_manager.get_async_session_context() as db:
        await db.execute(
            delete(AgentMemoryScope).where(
                AgentMemoryScope.uid == uid,
                AgentMemoryScope.agent_slug == agent_slug,
            )
        )
        db.add(
            AgentMemoryScope(
                uid=uid,
                agent_slug=agent_slug,
                workspace_id=memory_scope_identity(uid, agent_slug),
                last_memory_at=datetime.now(UTC),
            )
        )
        await db.commit()
    return workspace


async def _scope_exists(uid: str, agent_slug: str) -> bool:
    """检查真实 catalog 是否仍存在。"""
    async with pg_manager.get_async_session_context() as db:
        record = await db.scalar(
            select(AgentMemoryScope).where(
                AgentMemoryScope.uid == uid,
                AgentMemoryScope.agent_slug == agent_slug,
            )
        )
    return record is not None


async def _force_cleanup_scope(uid: str, agent_slug: str) -> None:
    """仅清理本测试显式创建的确定性 scope。"""
    workspace = memory_workspace_path(MEMORY_BASE_DIR, uid, agent_slug)
    if workspace.is_dir() and not workspace.is_symlink():
        shutil.rmtree(workspace)
    async with pg_manager.get_async_session_context() as db:
        await db.execute(
            delete(AgentMemoryScope).where(
                AgentMemoryScope.uid == uid,
                AgentMemoryScope.agent_slug == agent_slug,
            )
        )
        await db.commit()


async def _delete_user_if_present(
    test_client: httpx.AsyncClient,
    admin_headers: dict[str, str],
    user_id: int,
) -> None:
    response = await test_client.delete(f"/api/auth/users/{user_id}", headers=admin_headers)
    assert response.status_code in {200, 404}, response.text


async def test_disabled_memory_scope_can_be_listed_and_cleared(
    test_client: httpx.AsyncClient,
    admin_headers: dict[str, str],
):
    """Memory 默认关闭时，临时 Agent 的保留数据管理接口仍可用且清理幂等。"""
    slug = f"pytest-memory-{uuid.uuid4().hex[:10]}"
    created = await test_client.post(
        "/api/agent",
        headers=admin_headers,
        json={
            "name": "pytest memory agent",
            "slug": slug,
            "backend_id": "ChatbotAgent",
            "config_json": {},
        },
    )
    assert created.status_code == 200, created.text

    try:
        listed = await test_client.get(f"/api/agent/{slug}/memories", headers=admin_headers)
        assert listed.status_code == 200, listed.text
        payload = listed.json()
        assert payload["items"] == []
        assert payload["pagination"] == {"page": 1, "page_size": 20, "total": 0}
        assert payload["scope"]["enabled"] is False

        cleared = await test_client.delete(f"/api/agent/{slug}/memories", headers=admin_headers)
        assert cleared.status_code == 200, cleared.text
        assert cleared.json() == {"success": True}
    finally:
        deleted = await test_client.delete(f"/api/agent/{slug}", headers=admin_headers)
        assert deleted.status_code in (200, 404), deleted.text


async def test_two_users_only_list_their_own_real_cards_for_same_agent(
    test_client: httpx.AsyncClient,
    admin_headers: dict[str, str],
    standard_user: dict,
):
    """两个用户访问同一共享 Agent 时，列表只能返回各自真实 scope 的卡片。"""
    user_a = standard_user
    user_b = await _create_user(test_client, admin_headers, label="isolation")
    agent_slug = "default-chatbot"
    uid_a = str(user_a["user"]["uid"])
    uid_b = str(user_b["user"]["uid"])

    try:
        await _seed_memory_scope(uid_a, agent_slug, "USER_A_PRIVATE_MEMORY")
        await _seed_memory_scope(uid_b, agent_slug, "USER_B_PRIVATE_MEMORY")

        response_a = await test_client.get(
            f"/api/agent/{agent_slug}/memories",
            headers=user_a["headers"],
        )
        response_b = await test_client.get(
            f"/api/agent/{agent_slug}/memories",
            headers=user_b["headers"],
        )

        assert response_a.status_code == 200, response_a.text
        assert response_b.status_code == 200, response_b.text
        text_a = str(response_a.json()["items"])
        text_b = str(response_b.json()["items"])
        assert "USER_A_PRIVATE_MEMORY" in text_a
        assert "USER_B_PRIVATE_MEMORY" not in text_a
        assert "USER_B_PRIVATE_MEMORY" in text_b
        assert "USER_A_PRIVATE_MEMORY" not in text_b
    finally:
        await _force_cleanup_scope(uid_a, agent_slug)
        await _force_cleanup_scope(uid_b, agent_slug)
        await _delete_user_if_present(test_client, admin_headers, user_b["user"]["id"])


async def test_agent_delete_removes_all_user_memory_scopes(
    test_client: httpx.AsyncClient,
    admin_headers: dict[str, str],
    standard_user: dict,
):
    """删除临时 Agent 时，通过真实内部服务清理所有用户的目录和 catalog。"""
    user_b = await _create_user(test_client, admin_headers, label="agent_cleanup")
    uid_a = str(standard_user["user"]["uid"])
    uid_b = str(user_b["user"]["uid"])
    agent_slug = f"pytest-memory-cleanup-{uuid.uuid4().hex[:10]}"
    created = await test_client.post(
        "/api/agent",
        headers=admin_headers,
        json={
            "name": "pytest memory cleanup agent",
            "slug": agent_slug,
            "backend_id": "ChatbotAgent",
            "config_json": {},
            "share_config": {
                "version": 2,
                "read_scope": {"access_level": "global", "department_ids": [], "user_uids": []},
                "manage_scope": None,
            },
        },
    )
    assert created.status_code == 200, created.text

    workspace_a = await _seed_memory_scope(uid_a, agent_slug, "AGENT_DELETE_A")
    workspace_b = await _seed_memory_scope(uid_b, agent_slug, "AGENT_DELETE_B")
    try:
        deleted = await test_client.delete(f"/api/agent/{agent_slug}", headers=admin_headers)

        assert deleted.status_code == 200, deleted.text
        assert not workspace_a.exists()
        assert not workspace_b.exists()
        assert await _scope_exists(uid_a, agent_slug) is False
        assert await _scope_exists(uid_b, agent_slug) is False
    finally:
        await _force_cleanup_scope(uid_a, agent_slug)
        await _force_cleanup_scope(uid_b, agent_slug)
        await test_client.delete(f"/api/agent/{agent_slug}", headers=admin_headers)
        await _delete_user_if_present(test_client, admin_headers, user_b["user"]["id"])


async def test_user_delete_removes_all_agent_memory_scopes(
    test_client: httpx.AsyncClient,
    admin_headers: dict[str, str],
):
    """删除临时用户时清理其全部 Agent scope，并使旧登录令牌失效。"""
    target = await _create_user(test_client, admin_headers, label="user_cleanup")
    uid = str(target["user"]["uid"])
    agent_slugs = ["default-chatbot", "deep-research"]
    workspaces = [await _seed_memory_scope(uid, slug, f"USER_DELETE_{index}") for index, slug in enumerate(agent_slugs)]
    try:
        deleted = await test_client.delete(
            f"/api/auth/users/{target['user']['id']}",
            headers=admin_headers,
        )

        assert deleted.status_code == 200, deleted.text
        assert all(not workspace.exists() for workspace in workspaces)
        assert all([not await _scope_exists(uid, slug) for slug in agent_slugs])
        profile = await test_client.get("/api/auth/me", headers=target["headers"])
        assert profile.status_code == 401, profile.text
        async with pg_manager.get_async_session_context() as db:
            user = await db.scalar(select(User).where(User.uid == uid))
            assert user is not None and user.is_deleted == 1
    finally:
        for slug in agent_slugs:
            await _force_cleanup_scope(uid, slug)
        await _delete_user_if_present(test_client, admin_headers, target["user"]["id"])
