"""部门固定运行上下文 e2e（Task 5）。

真实链路验证：普通 chat 提交把当前会话活动部门固化进 agent_run_requests 与
agent_runs（直连 PG 回读，不信响应），worker 执行前校验固定部门成员身份；
提交后切换部门不影响已固化部门；同 request_id 跨部门重试必须 409；成员被
移除后活动部门折叠为无部门，提交立即 403。
"""

import uuid

import asyncpg
import pytest
from e2e_helpers import postgres_dsn, wait_for_run
from test.e2e.agentscope_e2e_fixtures import (
    PROVIDER_RESOURCE_ID,
    cleanup_fixture_agents,
    upsert_mock_provider,
)

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import Agent

AGENT_SLUG = "e2e-deptctx-chatbot"
USER_PASSWORD = "DeptCtx-User-2026"
DEPT_ADMIN_PASSWORD = "DeptCtx-Admin-2026"

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e]


@pytest.fixture
async def db_session():
    pg_manager.initialize()
    await pg_manager.ensure_business_schema()
    async with pg_manager.get_async_session_context() as session:
        await cleanup_fixture_agents(session, AGENT_SLUG)
        session.add(
            Agent(
                slug=AGENT_SLUG,
                name="部门上下文测试智能体",
                backend_id="ChatbotAgent",
                config_json={
                    "context": {
                        "model": f"{PROVIDER_RESOURCE_ID}:mock-chat-model",
                        "mcps": [],
                        "tools": [],
                        "skills": [],
                        "subagents": [],
                        "system_prompt": "你是部门上下文测试助手。",
                    }
                },
                share_config={
                    "version": 2,
                    "read_scope": {"access_level": "global", "department_ids": [], "user_uids": []},
                    "manage_scope": None,
                },
            )
        )
        await upsert_mock_provider(session)
        await session.commit()
        yield session
        await cleanup_fixture_agents(session, AGENT_SLUG)
    await pg_manager.close()
    pg_manager._initialized = False


async def _admin_revision_headers(client, admin_headers: dict[str, str]) -> dict[str, str]:
    """为超管的部门成员写请求附加当前会话 revision。"""
    me = await client.get("/api/auth/me", headers=admin_headers)
    assert me.status_code == 200, me.text
    merged = dict(admin_headers)
    merged["X-Department-Revision"] = str(me.json()["context_revision"])
    return merged


async def _create_department(client, admin_headers: dict[str, str], *, tag: str, suffix: str) -> dict:
    created = await client.post(
        "/api/departments",
        headers=admin_headers,
        json={
            "name": f"运行上下文部门{tag}-{suffix}",
            "description": "pytest department run context e2e",
            "admin_uid": f"deptctxadm{tag}{suffix}",
            "admin_password": DEPT_ADMIN_PASSWORD,
            "admin_phone": None,
        },
    )
    assert created.status_code == 201, created.text
    return created.json()


async def _cleanup_departments(
    client,
    admin_headers: dict[str, str],
    *,
    department_ids: list[int],
    admin_uids: list[str],
    user_ids: list[int],
) -> None:
    """记录式清理：成员关系/会话直连清除，账号与部门走正式删除接口。"""
    conn = await asyncpg.connect(postgres_dsn())
    try:
        admin_rows = await conn.fetch("SELECT id FROM users WHERE uid = ANY($1::text[])", admin_uids)
        admin_user_ids = [row["id"] for row in admin_rows]
        all_user_ids = admin_user_ids + list(user_ids)
        if department_ids:
            await conn.execute(
                "DELETE FROM department_memberships WHERE department_id = ANY($1::int[])", department_ids
            )
        if all_user_ids:
            await conn.execute("DELETE FROM auth_sessions WHERE user_id = ANY($1::int[])", all_user_ids)
    finally:
        await conn.close()
    for user_id in user_ids + admin_user_ids:
        await client.delete(f"/api/auth/users/{user_id}", headers=admin_headers)
    for department_id in department_ids:
        await client.delete(f"/api/departments/{department_id}", headers=admin_headers)


async def _submit_chat(client, headers: dict[str, str], *, slug: str, thread_id: str, request_id: str):
    return await client.post(
        "/api/agent/runs",
        headers=headers,
        json={
            "query": "打个招呼",
            "agent_slug": slug,
            "thread_id": thread_id,
            "meta": {"request_id": request_id},
        },
    )


async def _read_run_department_ids(request_id: str, run_id: str | None) -> tuple[int | None, int | None]:
    """直连 PG 回读 request 与 run 行的固化部门。"""
    conn = await asyncpg.connect(postgres_dsn())
    try:
        request_row = await conn.fetchrow(
            "SELECT department_id FROM agent_run_requests WHERE request_id = $1", request_id
        )
        run_row = await conn.fetchrow("SELECT department_id FROM agent_runs WHERE id = $1", run_id) if run_id else None
        return (
            request_row["department_id"] if request_row else None,
            run_row["department_id"] if run_row else None,
        )
    finally:
        await conn.close()


async def test_run_department_fixed_across_switch_and_retry(db_session, e2e_client, e2e_headers):
    """场景 1-3：提交固化部门、切换不影响已提交、跨部门重试 409。"""
    suffix = uuid.uuid4().hex[:8]
    department_a = await _create_department(e2e_client, e2e_headers, tag="a", suffix=suffix)
    department_b = await _create_department(e2e_client, e2e_headers, tag="b", suffix=suffix)
    department_ids = [department_a["id"], department_b["id"]]
    admin_uids = [f"deptctxadma{suffix}", f"deptctxadmb{suffix}"]
    user_ids: list[int] = []
    try:
        user_created = await e2e_client.post(
            "/api/auth/users",
            headers=e2e_headers,
            json={"username": f"deptctxu{suffix}", "password": USER_PASSWORD},
        )
        assert user_created.status_code == 200, user_created.text
        user_id = user_created.json()["id"]
        user_uid = user_created.json()["uid"]
        user_ids.append(user_id)
        for department_id in department_ids:
            added = await e2e_client.post(
                f"/api/departments/{department_id}/members",
                headers=await _admin_revision_headers(e2e_client, e2e_headers),
                json={"user_id": user_id},
            )
            assert added.status_code == 200, added.text

        user_login = await e2e_client.post("/api/auth/token", data={"username": user_uid, "password": USER_PASSWORD})
        assert user_login.status_code == 200, user_login.text
        user_headers = {"Authorization": f"Bearer {user_login.json()['access_token']}"}
        user_me = await e2e_client.get("/api/auth/me", headers=user_headers)
        assert user_me.status_code == 200, user_me.text
        # 登录会话活动部门取成员部门中 ID 最小项，A 先创建即为 A。
        assert user_me.json()["department_id"] == department_a["id"], user_me.text

        thread_created = await e2e_client.post(
            "/api/chat/thread",
            headers=user_headers,
            json={"agent_id": AGENT_SLUG, "title": f"deptctx-{suffix}"},
        )
        assert thread_created.status_code == 200, thread_created.text
        thread_id = str(thread_created.json().get("thread_id") or thread_created.json().get("id"))

        # 场景 1：A 上下文提交 chat，request 与 run 的固化部门都等于 A。
        request_id_1 = f"deptctx-req1-{suffix}"
        submitted_1 = await _submit_chat(
            e2e_client, user_headers, slug=AGENT_SLUG, thread_id=thread_id, request_id=request_id_1
        )
        assert submitted_1.status_code == 200, submitted_1.text
        run_id_1 = submitted_1.json()["run_id"]
        assert run_id_1, submitted_1.text
        run_1 = await wait_for_run(e2e_client, user_headers, run_id_1)
        assert run_1["status"] == "completed", run_1
        request_dept_1, run_dept_1 = await _read_run_department_ids(request_id_1, run_id_1)
        assert request_dept_1 == department_a["id"], (request_id_1, request_dept_1)
        assert run_dept_1 == department_a["id"], (run_id_1, run_dept_1)

        # 场景 2：提交后立即切换到 B，已提交 run 的固化部门仍为 A。
        request_id_2 = f"deptctx-req2-{suffix}"
        submitted_2 = await _submit_chat(
            e2e_client, user_headers, slug=AGENT_SLUG, thread_id=thread_id, request_id=request_id_2
        )
        assert submitted_2.status_code == 200, submitted_2.text
        run_id_2 = submitted_2.json()["run_id"]
        switched = await e2e_client.post(
            "/api/auth/department-context",
            headers=user_headers,
            json={
                "department_id": department_b["id"],
                "expected_revision": user_me.json()["context_revision"],
            },
        )
        assert switched.status_code == 200, switched.text
        run_2 = await wait_for_run(e2e_client, user_headers, run_id_2)
        assert run_2["status"] == "completed", run_2
        request_dept_2, run_dept_2 = await _read_run_department_ids(request_id_2, run_id_2)
        assert request_dept_2 == department_a["id"], (request_id_2, request_dept_2)
        assert run_dept_2 == department_a["id"], (run_id_2, run_dept_2)

        # 场景 3：B 上下文复用 A 的 request_id，必须 409（request_id 已绑定其他部门）。
        retried = await _submit_chat(
            e2e_client, user_headers, slug=AGENT_SLUG, thread_id=thread_id, request_id=request_id_2
        )
        assert retried.status_code == 409, (retried.status_code, retried.text)
        assert "部门" in str(retried.json().get("detail") or "")
    finally:
        await _cleanup_departments(
            e2e_client,
            e2e_headers,
            department_ids=department_ids,
            admin_uids=admin_uids,
            user_ids=user_ids,
        )


async def test_submission_rejected_after_membership_removal(e2e_client, e2e_headers):
    """场景 4：活动部门成员关系被移除后，上下文折叠为无部门，提交立即 403。"""
    suffix = uuid.uuid4().hex[:8]
    department_b = await _create_department(e2e_client, e2e_headers, tag="b", suffix=suffix)
    department_ids = [department_b["id"]]
    admin_uids = [f"deptctxadmb{suffix}"]
    user_ids: list[int] = []
    try:
        user_created = await e2e_client.post(
            "/api/auth/users",
            headers=e2e_headers,
            json={"username": f"deptctxu{suffix}", "password": USER_PASSWORD},
        )
        assert user_created.status_code == 200, user_created.text
        user_id = user_created.json()["id"]
        user_uid = user_created.json()["uid"]
        user_ids.append(user_id)
        added = await e2e_client.post(
            f"/api/departments/{department_b['id']}/members",
            headers=await _admin_revision_headers(e2e_client, e2e_headers),
            json={"user_id": user_id},
        )
        assert added.status_code == 200, added.text

        user_login = await e2e_client.post("/api/auth/token", data={"username": user_uid, "password": USER_PASSWORD})
        assert user_login.status_code == 200, user_login.text
        user_headers = {"Authorization": f"Bearer {user_login.json()['access_token']}"}
        user_me = await e2e_client.get("/api/auth/me", headers=user_headers)
        assert user_me.json()["department_id"] == department_b["id"], user_me.text

        removed = await e2e_client.delete(
            f"/api/departments/{department_b['id']}/members/{user_id}",
            headers=await _admin_revision_headers(e2e_client, e2e_headers),
        )
        assert removed.status_code == 204, removed.text

        rejected = await _submit_chat(
            e2e_client,
            user_headers,
            slug=AGENT_SLUG,
            thread_id=f"deptctx-thread-{suffix}",
            request_id=f"deptctx-req4-{suffix}",
        )
        assert rejected.status_code == 403, (rejected.status_code, rejected.text)
        assert rejected.json()["detail"]["code"] == "department_context_invalid", rejected.text
    finally:
        await _cleanup_departments(
            e2e_client,
            e2e_headers,
            department_ids=department_ids,
            admin_uids=admin_uids,
            user_ids=user_ids,
        )


async def test_resume_uses_original_department_after_switch(db_session, e2e_client, e2e_headers, e2e_mock_model_spec):
    """挂起 Run 恢复固定原部门：A 上下文中断后切到 B，resume Run 的固化部门仍是 A。

    覆盖计划欠账「resume 挂起中切部门 E2E」：恢复不得把当前会话活动部门当作原任务部门。
    """
    import asyncio as _asyncio

    suffix = uuid.uuid4().hex[:8]
    # 只开放主动提问工具的智能体：首轮触发 interrupted
    question_agent_slug = f"e2e-dept-question-{suffix}"
    agent_created = await e2e_client.post(
        "/api/agent",
        headers=e2e_headers,
        json={
            "name": f"部门恢复提问 {suffix}",
            "slug": question_agent_slug,
            "backend_id": "ChatbotAgent",
            "description": "部门 resume e2e",
            "config_json": {
                "context": {
                    "model": e2e_mock_model_spec,
                    "system_prompt": "需要用户选择时必须调用 ask_user_question；得到回答后简短确认。",
                    "tools": ["ask_user_question"],
                    "knowledges": [],
                    "mcps": [],
                    "skills": [],
                    "subagents": [],
                }
            },
            "share_config": {
                "version": 2,
                "read_scope": {"access_level": "global", "department_ids": [], "user_uids": []},
                "manage_scope": None,
            },
        },
    )
    assert agent_created.status_code in (200, 201), agent_created.text

    department_a = await _create_department(e2e_client, e2e_headers, tag="ra", suffix=suffix)
    department_b = await _create_department(e2e_client, e2e_headers, tag="rb", suffix=suffix)
    department_ids = [department_a["id"], department_b["id"]]
    admin_uids = [f"deptctxadmra{suffix}", f"deptctxadmrb{suffix}"]
    try:
        # 超管切到 A
        me = await e2e_client.get("/api/auth/me", headers=e2e_headers)
        assert me.status_code == 200, me.text
        switched_a = await e2e_client.post(
            "/api/auth/department-context",
            headers=e2e_headers,
            json={"department_id": department_a["id"], "expected_revision": me.json()["context_revision"]},
        )
        assert switched_a.status_code == 200, switched_a.text

        thread_created = await e2e_client.post(
            "/api/chat/thread",
            headers=e2e_headers,
            json={"agent_id": question_agent_slug, "title": f"dept-resume-{suffix}"},
        )
        assert thread_created.status_code == 200, thread_created.text
        thread_id = str(thread_created.json().get("thread_id") or thread_created.json().get("id"))

        request_id = f"dept-resume-{suffix}"
        # mock 模型按最后一条用户消息的关键词触发 ask_user_question
        submitted = await e2e_client.post(
            "/api/agent/runs",
            headers=e2e_headers,
            json={
                "query": "这个任务需要确认方案后再继续",
                "agent_slug": question_agent_slug,
                "thread_id": thread_id,
                "meta": {"request_id": request_id},
            },
        )
        assert submitted.status_code == 200, submitted.text
        interrupted_run_id = str(submitted.json()["run_id"])

        from e2e_helpers import consume_events

        events = await _asyncio.wait_for(
            consume_events(e2e_client, e2e_headers, interrupted_run_id), timeout=180
        )
        interrupted = await wait_for_run(e2e_client, e2e_headers, interrupted_run_id)
        assert interrupted["status"] == "interrupted", (interrupted, events)

        request_dept, run_dept = await _read_run_department_ids(request_id, interrupted_run_id)
        assert request_dept == department_a["id"], (request_id, request_dept)
        assert run_dept == department_a["id"], (interrupted_run_id, run_dept)

        # 挂起期间切换到 B：恢复必须仍用 A
        me_b = await e2e_client.get("/api/auth/me", headers=e2e_headers)
        switched_b = await e2e_client.post(
            "/api/auth/department-context",
            headers=e2e_headers,
            json={"department_id": department_b["id"], "expected_revision": me_b.json()["context_revision"]},
        )
        assert switched_b.status_code == 200, switched_b.text

        resume_response = await e2e_client.post(
            "/api/agent/runs",
            json={
                "agent_slug": question_agent_slug,
                "thread_id": thread_id,
                "resume": {"answer": {"希望采用哪种交付方式？": "分步交付"}},
                "created_by_run_id": interrupted_run_id,
                "meta": {"request_id": f"dept-resume-r-{suffix}"},
            },
            headers=e2e_headers,
        )
        assert resume_response.status_code == 200, resume_response.text
        resume_run_id = str(resume_response.json()["run_id"])
        await _asyncio.wait_for(consume_events(e2e_client, e2e_headers, resume_run_id), timeout=180)
        resumed = await wait_for_run(e2e_client, e2e_headers, resume_run_id)
        assert resumed["status"] == "completed", resumed

        # resume 路径跳过 request 入队（router 直接建 run），固化部门只看 run 行
        _, resume_run_dept = await _read_run_department_ids(f"dept-resume-r-{suffix}", resume_run_id)
        assert resume_run_dept == department_a["id"], (resume_run_id, resume_run_dept)
    finally:
        await e2e_client.delete(f"/api/agent/{question_agent_slug}", headers=e2e_headers)
        await _cleanup_departments(
            e2e_client,
            e2e_headers,
            department_ids=department_ids,
            admin_uids=admin_uids,
            user_ids=[],
        )
