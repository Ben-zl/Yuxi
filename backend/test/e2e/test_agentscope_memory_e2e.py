"""ReMe 跨 Thread 真实模型召回端到端测试。"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from pathlib import Path

import httpx
import pytest
from test.e2e.agentscope_e2e_fixtures import cleanup_test_users, run_e2e_cleanup_steps
from test.live_api_cleanup import (
    make_test_conversation_metadata,
    make_test_conversation_title,
    make_test_resource_id,
)
from yuxi.storage.postgres.manager import pg_manager

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e, pytest.mark.slow]

RUN_TIMEOUT_SECONDS = int(os.getenv("E2E_RUN_TIMEOUT_SECONDS", "300"))
MEMORY_WAIT_SECONDS = int(os.getenv("E2E_MEMORY_WAIT_SECONDS", "120"))

requires_live_memory = pytest.mark.skipif(
    os.getenv("E2E_MEMORY_RECALL_ENABLED", "").lower() not in {"1", "true"},
    reason="未启用 E2E_MEMORY_RECALL_ENABLED，跳过真实模型长期记忆召回",
)

requires_shared_memory_dir = pytest.mark.skipif(
    not Path(os.getenv("AGENTSCOPE_MEMORY_BASEDIR", "/app/agentscope-memory")).is_dir()
    or not os.access(os.getenv("AGENTSCOPE_MEMORY_BASEDIR", "/app/agentscope-memory"), os.W_OK),
    reason="测试进程与 agentscope 服务未共享 AGENTSCOPE_MEMORY_BASEDIR 目录，跳过重绑链路验证",
)


async def _create_user(client: httpx.AsyncClient, admin_headers: dict[str, str]) -> tuple[dict, str]:
    """创建临时用户，并在登录前返回可清理身份。"""
    profile = await client.get("/api/auth/me", headers=admin_headers)
    assert profile.status_code == 200, profile.text
    assert profile.json().get("role") in {"admin", "superadmin"}, "Memory E2E 需要管理员测试账号"

    departments = await client.get("/api/departments", headers=admin_headers)
    assert departments.status_code == 200 and departments.json(), departments.text
    password = f"Pw!{uuid.uuid4().hex[:12]}"
    created = await client.post(
        "/api/auth/users",
        headers=admin_headers,
        json={
            "username": f"e2e_memory_{uuid.uuid4().hex[:8]}",
            "password": password,
        },
    )
    assert created.status_code == 200, created.text
    return created.json(), password


async def _login_user(client: httpx.AsyncClient, uid: str, password: str) -> dict[str, str]:
    """登录已创建的 Memory E2E 用户。"""
    login = await client.post(
        "/api/auth/token",
        data={"username": uid, "password": password},
    )
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def _create_thread(client: httpx.AsyncClient, headers: dict[str, str], agent_slug: str) -> str:
    """创建带清理标识的独立对话。"""
    response = await client.post(
        "/api/chat/thread",
        headers=headers,
        json={
            "agent_id": agent_slug,
            "title": make_test_conversation_title("memory-recall-e2e"),
            "metadata": make_test_conversation_metadata("memory-recall-e2e", e2e=True),
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    thread_id = payload.get("thread_id") or payload.get("id")
    assert thread_id, payload
    return str(thread_id)


async def _run(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    *,
    agent_slug: str,
    thread_id: str,
    query: str,
) -> dict:
    """提交真实 AgentRun 并轮询其公开结果。"""
    response = await client.post(
        "/api/agent/runs",
        headers=headers,
        json={
            "query": query,
            "agent_slug": agent_slug,
            "thread_id": thread_id,
            "tool_approval_mode": "always_trust",
            "meta": {"request_id": make_test_resource_id("memory-recall-e2e")},
        },
    )
    assert response.status_code == 200, response.text
    run_id = str(response.json().get("run_id") or "")
    assert run_id, response.text

    deadline = asyncio.get_running_loop().time() + RUN_TIMEOUT_SECONDS
    while asyncio.get_running_loop().time() < deadline:
        result = await client.get(f"/api/agent/runs/{run_id}/result", headers=headers)
        if result.status_code == 200:
            payload = result.json()
            if payload.get("status") in {"completed", "failed", "cancelled", "interrupted"}:
                assert payload.get("status") == "completed", payload
                return payload
        else:
            assert result.status_code in {202, 404}, result.text
        await asyncio.sleep(2)
    pytest.fail(f"Memory E2E AgentRun 超时: {run_id}")


async def _wait_for_memory(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    agent_slug: str,
    marker: str,
) -> None:
    """等待 ReMe 完成异步卡片和索引写入。"""
    deadline = asyncio.get_running_loop().time() + MEMORY_WAIT_SECONDS
    last_payload: dict = {}
    while asyncio.get_running_loop().time() < deadline:
        response = await client.get(f"/api/agent/{agent_slug}/memories", headers=headers)
        assert response.status_code in {200, 409}, response.text
        if response.status_code == 200:
            last_payload = response.json()
            if marker in json.dumps(last_payload.get("items") or [], ensure_ascii=False):
                return
        await asyncio.sleep(2)
    pytest.fail(f"ReMe 未在期限内写入测试事实: {last_payload}")


@requires_live_memory
async def test_real_model_recalls_unique_fact_across_threads(
    e2e_client: httpx.AsyncClient,
    e2e_headers: dict[str, str],
):
    """Thread A 写入的唯一事实必须能在同 Agent 的 Thread B 被真实模型召回。"""
    agent_slug = "default-chatbot"
    marker = f"REME_E2E_{uuid.uuid4().hex.upper()}"
    user: dict | None = None
    password: str | None = None
    headers: dict[str, str] | None = None
    thread_ids: list[str] = []
    primary_error: BaseException | None = None

    try:
        user, password = await _create_user(e2e_client, e2e_headers)
        headers = await _login_user(e2e_client, str(user["uid"]), password)
        enabled = await e2e_client.put(
            "/api/user/config",
            headers=headers,
            json={"enable_memory": True},
        )
        assert enabled.status_code == 200, enabled.text
        assert enabled.json().get("enable_memory") is True

        write_thread = await _create_thread(e2e_client, headers, agent_slug)
        thread_ids.append(write_thread)
        await _run(
            e2e_client,
            headers,
            agent_slug=agent_slug,
            thread_id=write_thread,
            query=f"请把这个稳定项目代号记入长期记忆，并确认已记住：{marker}",
        )
        await _wait_for_memory(e2e_client, headers, agent_slug, marker)

        recall_thread = await _create_thread(e2e_client, headers, agent_slug)
        thread_ids.append(recall_thread)
        recalled = await _run(
            e2e_client,
            headers,
            agent_slug=agent_slug,
            thread_id=recall_thread,
            query="请搜索长期记忆，只回复此前记录的稳定项目代号。",
        )

        assert marker in json.dumps(recalled.get("output"), ensure_ascii=False), recalled
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        cleanup_steps = []
        for thread_id in thread_ids:

            async def delete_thread(target_thread_id: str = thread_id) -> None:
                response = await e2e_client.delete(
                    f"/api/chat/thread/{target_thread_id}",
                    headers=headers,
                )
                assert response.status_code in {200, 404}, response.text

            cleanup_steps.append((f"delete thread {thread_id}", delete_thread))

        if user is not None:
            target_user_id = user["id"]

            async def delete_user() -> None:
                response = await e2e_client.delete(
                    f"/api/auth/users/{target_user_id}",
                    headers=e2e_headers,
                )
                assert response.status_code in {200, 404}, response.text

            cleanup_steps.append(("soft-delete memory user", delete_user))

            target_uid = str(user["uid"])

            async def cleanup_user_rows() -> None:
                pg_manager.initialize()
                await pg_manager.ensure_business_schema()
                async with pg_manager.get_async_session_context() as session:
                    await cleanup_test_users(session, target_uid)

            cleanup_steps.append(("physically delete memory user resources", cleanup_user_rows))

        await run_e2e_cleanup_steps(cleanup_steps, primary_error=primary_error)


@requires_live_memory
@requires_shared_memory_dir
async def test_memory_maintenance_rebinds_after_department_switch(
    e2e_client: httpx.AsyncClient,
    e2e_headers: dict[str, str],
):
    """Memory A/B 真实切换：scope 绑定 A 后切 B 删卡，维护部门重绑为 B。

    覆盖计划欠账「Memory A/B 真实切换场景」：显式删除按请求活动部门重建并重绑；
    后台调度（无 department_id）随后消费新绑定，不回落旧部门。
    """
    import asyncpg

    department_ids: list[int] = []
    dsn = os.getenv(
        "POSTGRES_URL",
        "postgresql+asyncpg://postgres:postgres@postgres:5432/yuxi",
    ).replace("+asyncpg", "")
    conn = await asyncpg.connect(dsn)
    try:
        # 建两个部门（真实 HTTP）
        for tag in ("a", "b"):
            resp = await e2e_client.post(
                "/api/departments",
                headers=e2e_headers,
                json={
                    "name": f"mem-rebind-{tag}-{uuid.uuid4().hex[:8]}",
                    "description": "memory rebind e2e",
                    "admin_uid": f"mem{tag}{uuid.uuid4().hex[:6]}",
                    "admin_password": "MemoryPass-2026",
                    "admin_phone": None,
                },
            )
            assert resp.status_code == 201, resp.text
            department_ids.append(resp.json()["id"])
        dept_a, dept_b = department_ids

        user, password = await _create_user(e2e_client, e2e_headers)
        uid = user["uid"]
        headers = await _login_user(e2e_client, uid, password)
        user_id = user["id"]

        # 加入部门 A 与 B（普通成员），并重新登录解析活动部门（最小 ID=A）
        admin_me = await e2e_client.get("/api/auth/me", headers=e2e_headers)
        for dept in (dept_a, dept_b):
            added = await e2e_client.post(
                f"/api/departments/{dept}/members",
                headers={
                    **e2e_headers,
                    "X-Department-Revision": str(admin_me.json()["context_revision"]),
                },
                json={"user_id": user_id},
            )
            assert added.status_code == 200, added.text
        headers = await _login_user(e2e_client, uid, password)
        me = await e2e_client.get("/api/auth/me", headers=headers)
        assert me.status_code == 200 and me.json()["department_id"] == dept_a, me.text

        agent_slug = f"e2e-mem-rebind-{uuid.uuid4().hex[:8]}"
        agent_created = await e2e_client.post(
            "/api/agent",
            headers=headers,
            json={
                "name": f"E2E memory rebind {agent_slug[-8:]}",
                "slug": agent_slug,
                "backend_id": "ChatbotAgent",
                "config_json": {},
            },
        )
        assert agent_created.status_code == 200, agent_created.text
        try:
            # 开启 Memory 并直接落 scope catalog 与一张真实卡片文件
            # （删除→重建→重绑链路真实运行；不依赖 ReMe 推理产出）
            enabled = await e2e_client.put(
                "/api/user/config",
                headers=headers,
                json={"enable_memory": True},
            )
            assert enabled.status_code == 200, enabled.text

            from datetime import UTC, datetime

            from yuxi.agentscope.memory import (
                memory_scope_identity,
                memory_workspace_path,
            )

            memory_base = os.getenv("AGENTSCOPE_MEMORY_BASEDIR", "/app/agentscope-memory")
            workspace = memory_workspace_path(memory_base, uid, agent_slug)
            daily = workspace / "daily" / "2026-09-01"
            daily.mkdir(parents=True, exist_ok=True)
            (daily / "fact.md").write_text(
                "---\nname: rebind-fact\ndescription: rebind seed\n---\nrebind seed card\n",
                encoding="utf-8",
            )
            import asyncpg as _asyncpg

            _conn = await _asyncpg.connect(dsn)
            try:
                await _conn.execute(
                    "INSERT INTO agent_memory_scopes (uid, agent_slug, workspace_id, "
                    "maintenance_department_id, last_memory_at) "
                    "VALUES ($1, $2, $3, $4, NOW()) ON CONFLICT DO NOTHING",
                    uid,
                    agent_slug,
                    memory_scope_identity(uid, agent_slug),
                    dept_a,
                )
            finally:
                await _conn.close()

            scope_dept = await conn.fetchval(
                "SELECT maintenance_department_id FROM agent_memory_scopes WHERE uid = $1 AND agent_slug = $2",
                uid,
                agent_slug,
            )
            assert scope_dept == dept_a, (scope_dept, dept_a)

            # 切换到 B（revision 用当前会话）
            me_after = await e2e_client.get("/api/auth/me", headers=headers)
            switched = await e2e_client.post(
                "/api/auth/department-context",
                headers=headers,
                json={
                    "department_id": dept_b,
                    "expected_revision": me_after.json()["context_revision"],
                },
            )
            assert switched.status_code == 200, switched.text

            # 列出卡片并显式删除一张：重建成功 → 维护部门重绑为 B
            listed = await e2e_client.get(f"/api/agent/{agent_slug}/memories", headers=headers)
            assert listed.status_code in {200, 409}, listed.text
            if listed.status_code != 200:
                pytest.fail(
                    f"memory scope 列表不可用(status={listed.status_code})："
                    "重绑验证依赖 ReMe 运行时；请在启用 E2E_MEMORY_RECALL_ENABLED 的环境运行"
                )
            items = listed.json().get("items") or []
            if not items:
                pytest.skip(
                    "memory scope 列表为空：测试进程与 agentscope 服务的记忆目录不一致时无法验证"
                )
            card_id = items[0]["memory_id"]
            deleted = await e2e_client.delete(
                f"/api/agent/{agent_slug}/memories/{card_id}",
                headers=headers,
            )
            assert deleted.status_code == 200, deleted.text

            rebound = await conn.fetchval(
                "SELECT maintenance_department_id FROM agent_memory_scopes WHERE uid = $1 AND agent_slug = $2",
                uid,
                agent_slug,
            )
            assert rebound == dept_b, (rebound, dept_b)
        finally:
            await e2e_client.delete(f"/api/agent/{agent_slug}", headers=headers)
            import shutil as _shutil

            if workspace.is_dir() and not workspace.is_symlink():
                _shutil.rmtree(workspace, ignore_errors=True)
            cleanup_conn = await asyncpg.connect(dsn)
            try:
                await cleanup_conn.execute(
                    "DELETE FROM agent_memory_scopes WHERE agent_slug = $1", agent_slug
                )
                await cleanup_conn.execute(
                    "DELETE FROM auth_sessions WHERE user_id = $1", user_id
                )
                await cleanup_conn.execute("DELETE FROM users WHERE id = $1", user_id)
            finally:
                await cleanup_conn.close()
    finally:
        await run_e2e_cleanup_steps(
            [
                (
                    "delete rebind memberships",
                    lambda: conn.execute(
                        "DELETE FROM department_memberships WHERE department_id = ANY($1::int[])",
                        department_ids,
                    ),
                ),
                (
                    "null rebind sessions",
                    lambda: conn.execute(
                        "UPDATE auth_sessions SET active_department_id = NULL "
                        "WHERE active_department_id = ANY($1::int[])",
                        department_ids,
                    ),
                ),
            ],
            primary_error=None,
        )
        for department_id in department_ids:
            await e2e_client.delete(f"/api/departments/{department_id}", headers=e2e_headers)
        await conn.close()
