"""非交互入口（API Key / CLI / Channel / 定时任务）部门上下文集成测试。

四条入口各自记录绑定部门，提交的 AgentRunRequest 部门回读必须等于
建立时的部门；Web 会话切换部门不改变任一入口的提交部门；无部门绑定
的旧事实明确拒绝且不创建请求。
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta

import pytest
import pytest_asyncio
from sqlalchemy import text

from test.integration.api.test_knowledge_router import _create_test_department
from test.integration.conftest import admin_revision_headers

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

TEST_AGENT_SLUG = "noninteractive-it-agent"


@pytest_asyncio.fixture(autouse=True)
async def _reset_postgres_between_event_loops():
    """避免全局 asyncpg pool 跨 pytest event loop 复用。"""
    from yuxi.storage.postgres.manager import pg_manager

    await pg_manager.reset()
    pg_manager.initialize()
    yield
    await pg_manager.reset()


@pytest_asyncio.fixture
async def case_env(test_client, admin_headers):
    """部门 A/B + 双部门成员账号 + 全局 mock Agent，yield 上下文与清理。"""
    from test.e2e.agentscope_e2e_fixtures import cleanup_fixture_agents, upsert_mock_provider
    from yuxi.storage.postgres.manager import pg_manager
    from yuxi.storage.postgres.models_business import Agent

    suffix = uuid.uuid4().hex[:8]
    password = "Noninteractive-Pass-2026"
    department_ids: list[int] = []
    user_id = None
    uid = None
    try:
        department_a = await _create_test_department(test_client, admin_headers, f"ni_a_{suffix}")
        department_b = await _create_test_department(test_client, admin_headers, f"ni_b_{suffix}")
        department_ids = [department_a["id"], department_b["id"]]

        created = await test_client.post(
            "/api/auth/users",
            headers=admin_headers,
            json={"username": f"非交互测试{suffix}", "password": password},
        )
        assert created.status_code == 200, created.text
        user_id = created.json()["id"]
        uid = str(created.json()["uid"])
        rev = await admin_revision_headers(test_client, admin_headers)
        for dep_id in department_ids:
            added = await test_client.post(f"/api/departments/{dep_id}/members", headers=rev, json={"user_id": user_id})
            assert added.status_code == 200, added.text

        login = await test_client.post(
            "/api/auth/token", data={"username": created.json()["uid"], "password": password}
        )
        assert login.status_code == 200, login.text
        token = login.json()["access_token"]
        admin_me = await test_client.get("/api/auth/me", headers=admin_headers)
        assert admin_me.status_code == 200, admin_me.text
        admin_original_department = admin_me.json()["department_id"]

        pg_manager.initialize()
        await pg_manager.ensure_business_schema()
        async with pg_manager.get_async_session_context() as db:
            await cleanup_fixture_agents(db, TEST_AGENT_SLUG)
            await upsert_mock_provider(db)
            db.add(
                Agent(
                    slug=TEST_AGENT_SLUG,
                    name="非交互入口测试智能体",
                    backend_id="ChatbotAgent",
                    config_json={"context": {"model": "e2e-openai-mock:mock-chat-model"}},
                    share_config={
                        "version": 2,
                        "read_scope": {
                            "access_level": "global",
                            "department_ids": [],
                            "user_uids": [],
                        },
                        "manage_scope": None,
                    },
                )
            )
            await db.commit()

        yield {
            "uid": uid,
            "user_id": user_id,
            "token": token,
            "department_ids": department_ids,
        }

        await _settle_runs(uid)
        # 先恢复超管会话活动部门，避免随后的会话清理误删共享测试会话
        await _restore_admin_department(test_client, admin_headers, admin_original_department, department_ids)
        # thread_artifacts 等扩展表在本环境不存在，静态清理不可用：直接按 uid 清业务事实
        async with pg_manager.get_async_session_context() as db:
            await db.execute(
                text(
                    "DELETE FROM task_executions WHERE task_id IN"
                    " (SELECT id FROM agent_tasks WHERE owner_uid = :u OR name LIKE 'NI-%')"
                ),
                {"u": uid},
            )
            await db.execute(
                text("DELETE FROM agent_tasks WHERE owner_uid = :u OR name LIKE 'NI-%'"),
                {"u": uid},
            )
            await db.execute(
                text("DELETE FROM agentscope_channel_bindings WHERE created_by = :u OR owner_uid = :u"),
                {"u": uid},
            )
            await db.execute(
                text("DELETE FROM agent_run_attempts WHERE run_id IN (SELECT id FROM agent_runs WHERE uid = :u)"),
                {"u": uid},
            )
            await db.execute(
                text(
                    "DELETE FROM tool_calls WHERE message_id IN (SELECT id FROM messages"
                    " WHERE conversation_id IN (SELECT id FROM conversations WHERE uid = :u))"
                ),
                {"u": uid},
            )
            await db.execute(
                text(
                    "DELETE FROM message_feedbacks WHERE message_id IN (SELECT id FROM messages"
                    " WHERE conversation_id IN (SELECT id FROM conversations WHERE uid = :u))"
                ),
                {"u": uid},
            )
            await db.execute(text("DELETE FROM agent_run_requests WHERE uid = :u"), {"u": uid})
            await db.execute(
                text("DELETE FROM messages WHERE conversation_id IN (SELECT id FROM conversations WHERE uid = :u)"),
                {"u": uid},
            )
            await db.execute(text("DELETE FROM agent_runs WHERE uid = :u"), {"u": uid})
            await db.execute(text("DELETE FROM agentscope_thread_sessions WHERE uid = :u"), {"u": uid})
            await db.execute(
                text(
                    "DELETE FROM subagent_threads WHERE parent_conversation_id IN"
                    " (SELECT id FROM conversations WHERE uid = :u)"
                    " OR child_conversation_id IN (SELECT id FROM conversations WHERE uid = :u)"
                ),
                {"u": uid},
            )
            await db.execute(
                text(
                    "DELETE FROM conversation_stats WHERE conversation_id IN"
                    " (SELECT id FROM conversations WHERE uid = :u)"
                ),
                {"u": uid},
            )
            await db.execute(text("DELETE FROM conversations WHERE uid = :u"), {"u": uid})
            await db.execute(text("DELETE FROM projects WHERE uid = :u"), {"u": uid})
            await db.execute(text("DELETE FROM cli_auth_sessions WHERE approved_user_id = :u"), {"u": user_id})
            await db.execute(text("DELETE FROM api_keys WHERE user_id = :u"), {"u": user_id})
            if user_id is not None:
                await db.execute(text("DELETE FROM department_memberships WHERE user_id = :u"), {"u": user_id})
                await db.execute(text("DELETE FROM auth_sessions WHERE user_id = :u"), {"u": user_id})
            for dep_id in department_ids:
                await db.execute(text("DELETE FROM department_memberships WHERE department_id = :d"), {"d": dep_id})
                await db.execute(text("DELETE FROM auth_sessions WHERE active_department_id = :d"), {"d": dep_id})
            await db.commit()
            await cleanup_fixture_agents(db, TEST_AGENT_SLUG)
        _remove_user_workspace_root(uid)
        if user_id is not None:
            await test_client.delete(f"/api/auth/users/{user_id}", headers=admin_headers)
        for dep_id in department_ids:
            await test_client.delete(f"/api/departments/{dep_id}", headers=admin_headers)
    finally:
        from yuxi.storage.redis.manager import close_async_redis_client
        from yuxi.services import run_queue_service

        if run_queue_service._arq_pool is not None:
            await run_queue_service._arq_pool.aclose()
            run_queue_service._arq_pool = None
        await close_async_redis_client()
        try:
            await pg_manager.close()
        finally:
            pg_manager._initialized = False


async def _settle_runs(uid: str) -> None:
    """取消并等待测试 UID 的全部活动 Run 收束到终态。"""
    from yuxi.repositories.agent_run_repository import TERMINAL_RUN_STATUSES
    from yuxi.services.agent_run_service import request_cancel_agent_run
    from yuxi.storage.postgres.manager import pg_manager

    terminal = ",".join(f"'{status}'" for status in TERMINAL_RUN_STATUSES)
    async with pg_manager.get_async_session_context() as db:
        for _ in range(30):
            await db.rollback()
            rows = (
                await db.execute(
                    text(f"SELECT id FROM agent_runs WHERE uid = :u AND status NOT IN ({terminal})"),
                    {"u": uid},
                )
            ).all()
            if not rows:
                return
            for (run_id,) in rows:
                await request_cancel_agent_run(run_id=run_id, current_uid=uid, db=db)
            await db.commit()
            await asyncio.sleep(1)
        raise RuntimeError(f"非交互测试 Run 未能在窗口内收束: {uid}")


def _remove_user_workspace_root(uid: str) -> None:
    """尽力清理测试账号的共享 workspace 根，拒绝 symlink 越界。"""
    import shutil

    from yuxi.config import get_user_data_dir

    target = get_user_data_dir() / "shared" / uid
    if target.is_symlink():
        return
    if target.is_dir():
        shutil.rmtree(target, ignore_errors=True)


async def _restore_admin_department(test_client, admin_headers, original_department_id, test_department_ids) -> None:
    """把超管会话切回非测试部门；原部门缺失时退到任意非测试部门。"""
    me = await test_client.get("/api/auth/me", headers=admin_headers)
    if me.status_code != 200:
        return
    current = me.json()["department_id"]
    if current not in test_department_ids:
        return
    target = original_department_id if original_department_id not in test_department_ids else None
    if target is None:
        departments = await test_client.get("/api/departments", headers=admin_headers)
        target = next(
            (item["id"] for item in departments.json() if item["id"] not in test_department_ids),
            None,
        )
    if target is not None:
        await _switch_department(test_client, admin_headers, target)


async def _switch_department(test_client, headers: dict, department_id: int) -> dict:
    """切换当前会话活动部门并返回新身份。"""
    me = await test_client.get("/api/auth/me", headers=headers)
    assert me.status_code == 200, me.text
    payload = {
        "department_id": department_id,
        "expected_revision": me.json()["context_revision"],
    }
    response = await test_client.post("/api/auth/department-context", json=payload, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


async def _read_request_department(request_id: str) -> int | None:
    """按 request_id 从 PostgreSQL 回读请求固定部门。"""
    from yuxi.storage.postgres.manager import pg_manager

    async with pg_manager.get_async_session_context() as db:
        value = await db.scalar(
            text("SELECT department_id FROM agent_run_requests WHERE request_id = :r"),
            {"r": request_id},
        )
    return value


async def _submit_agent_call(test_client, secret: str, request_id: str) -> dict:
    """用非交互凭证沿真实 HTTP 提交一次 agent-call 运行。"""
    response = await test_client.post(
        "/api/agent-invocation/agent-call/runs",
        headers={"Authorization": f"Bearer {secret}"},
        json={
            "agent_slug": TEST_AGENT_SLUG,
            "messages": [{"role": "user", "content": "请回复：完成"}],
            "async_mode": True,
            "queue_policy": "enqueue",
            "request_id": request_id,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


# ================================================================
# 路径一：API Key 创建即绑定部门，key 提交固定原部门
# ================================================================


async def test_api_key_submission_fixes_department(test_client, case_env):
    case = case_env
    dept_a, dept_b = case["department_ids"]
    headers = {"Authorization": f"Bearer {case['token']}"}

    # 登录会话当前部门为 A（成员部门中 ID 最小者）；key 创建即绑定 A
    me = await test_client.get("/api/auth/me", headers=headers)
    assert me.status_code == 200, me.text
    assert me.json()["department_id"] == dept_a

    created = await test_client.post(
        "/api/user/apikey/",
        headers=headers,
        json={"request_id": f"ni-key-{uuid.uuid4().hex[:16]}", "name": "非交互部门测试 Key"},
    )
    assert created.status_code == 200, created.text
    api_key = created.json()["api_key"]
    assert api_key["department_id"] == dept_a
    secret = created.json()["secret"]

    request_id = f"ni-apikey-run-{uuid.uuid4().hex[:12]}"
    result = await _submit_agent_call(test_client, secret, request_id)
    assert await _read_request_department(result["request_id"]) == dept_a

    # Web 切换到 B 后，同一把 key 提交仍固定 A
    await _switch_department(test_client, headers, dept_b)
    request_id_b = f"ni-apikey-run-b-{uuid.uuid4().hex[:12]}"
    result_b = await _submit_agent_call(test_client, secret, request_id_b)
    assert await _read_request_department(result_b["request_id"]) == dept_a


# ================================================================
# 路径二：CLI 批准固定部门，兑换写入 key，不随批准者会话漂移
# ================================================================


async def _start_cli_session(test_client) -> tuple[str, str]:
    response = await test_client.post("/api/auth/cli/sessions", json={"key_name": "非交互 CLI"})
    assert response.status_code == 200, response.text
    return response.json()["device_code"], response.json()["user_code"]


async def test_cli_exchange_binds_approved_department(test_client, case_env, admin_headers):
    case = case_env
    dept_a, dept_b = case["department_ids"]
    headers = {"Authorization": f"Bearer {case['token']}"}

    device_code, user_code = await _start_cli_session(test_client)
    approve = await test_client.post(f"/api/auth/cli/sessions/{user_code}/approve", headers=headers)
    assert approve.status_code == 200, approve.text

    # A 批准 → Web 切 B → 兑换仍是 A
    await _switch_department(test_client, headers, dept_b)
    exchange = await test_client.post("/api/auth/cli/sessions/token", json={"device_code": device_code})
    assert exchange.status_code == 200, exchange.text
    token_data = exchange.json()
    assert token_data["api_key"]["department_id"] == dept_a
    cli_secret = token_data["secret"]

    request_id = f"ni-cli-run-{uuid.uuid4().hex[:12]}"
    result = await _submit_agent_call(test_client, cli_secret, request_id)
    assert await _read_request_department(result["request_id"]) == dept_a

    # 重放兑换复用同一 api_key_id 与原部门
    replay = await test_client.post("/api/auth/cli/sessions/token", json={"device_code": device_code})
    assert replay.status_code == 200, replay.text
    assert replay.json()["api_key"]["id"] == token_data["api_key"]["id"]
    assert replay.json()["api_key"]["department_id"] == dept_a

    # 批准后撤销 A 成员 → 兑换拒绝（批准时会话需回到 A）
    await _switch_department(test_client, headers, dept_a)
    device_2, user_code_2 = await _start_cli_session(test_client)
    approve_2 = await test_client.post(f"/api/auth/cli/sessions/{user_code_2}/approve", headers=headers)
    assert approve_2.status_code == 200, approve_2.text
    rev = await admin_revision_headers(test_client, admin_headers)
    removed = await test_client.delete(f"/api/departments/{dept_a}/members/{case['user_id']}", headers=rev)
    assert removed.status_code == 204, removed.text
    rejected = await test_client.post("/api/auth/cli/sessions/token", json={"device_code": device_2})
    assert rejected.status_code == 409, rejected.text


# ================================================================
# 路径三：Channel binding 创建记录部门，ingress 提交固定绑定部门
# ================================================================


async def test_channel_binding_submission_fixes_department(test_client, case_env, admin_headers):
    from yuxi.channels.wps_xiezuo import WPSChannelEvent
    from yuxi.services.wps_channel_ingress_service import submit_wps_channel_event

    case = case_env
    dept_a, dept_b = case["department_ids"]

    # 超管切到 A 创建 binding：binding 部门 = 创建者当前有效部门
    await _switch_department(test_client, admin_headers, dept_a)
    created = await test_client.post(
        "/api/agent-channels",
        headers=admin_headers,
        json={
            "agent_slug": TEST_AGENT_SLUG,
            "name": "非交互部门 Channel",
            "app_id": f"ni-wps-{uuid.uuid4().hex[:12]}",
            "app_secret": "wps-secret-value",
            "allow_from": ["wps-user-1"],
            "group_reply_policy": "mention_only",
            "enabled": True,
        },
    )
    assert created.status_code == 201, created.text
    binding = created.json()["data"]
    assert binding["department_id"] == dept_a
    assert binding["sync_status"] == "synced", binding.get("last_error")

    first = await submit_wps_channel_event(
        binding["id"],
        WPSChannelEvent(
            channel_id=binding["id"],
            channel_user_id="wps-user-1",
            channel_message_id=f"msg-{uuid.uuid4().hex[:8]}",
            chat_id=f"chat-{uuid.uuid4().hex[:8]}",
            text="请处理这条协作消息",
            received_at="2026-09-17T00:00:00Z",
        ),
    )
    assert await _read_request_department(first["request_id"]) == dept_a

    # Web 切换到 B 后，ingress 提交仍固定 binding 部门 A
    await _switch_department(test_client, admin_headers, dept_b)
    second = await submit_wps_channel_event(
        binding["id"],
        WPSChannelEvent(
            channel_id=binding["id"],
            channel_user_id="wps-user-1",
            channel_message_id=f"msg-{uuid.uuid4().hex[:8]}",
            chat_id=f"chat-{uuid.uuid4().hex[:8]}",
            text="切换部门后的第二条消息",
            received_at="2026-09-17T00:01:00Z",
        ),
    )
    assert await _read_request_department(second["request_id"]) == dept_a


# ================================================================
# 路径四：定时任务创建记录部门；手动/API/定时触发复制同一部门
# ================================================================


async def _create_task(test_client, headers: dict, name: str, *, schedule: dict | None) -> dict:
    payload = {
        "name": name,
        "agent_slug": TEST_AGENT_SLUG,
        "prompt": "请回复：任务执行完成",
        "tool_approval_mode": "always_trust",
        "api_enabled": True,
    }
    if schedule is not None:
        payload["schedule"] = schedule
    response = await test_client.post("/api/agent-tasks", headers=headers, json=payload)
    assert response.status_code == 200, response.text
    return response.json()


async def test_scheduled_task_records_and_copies_department(test_client, case_env, admin_headers):
    from yuxi.repositories.agent_task_repository import TaskExecutionRepository
    from yuxi.services.agent_task_scheduler_service import AgentTaskSchedulerService
    from yuxi.storage.postgres.manager import pg_manager

    case = case_env
    dept_a, dept_b = case["department_ids"]
    headers = {"Authorization": f"Bearer {case['token']}"}

    # 定时配置延后启用：先完成手动/API 验证并收束队列，避免后台 worker 抢先扫描
    task = await _create_task(
        test_client,
        headers,
        "NI-定时部门",
        schedule=None,
    )
    async with pg_manager.get_async_session_context() as db:
        task_department = await db.scalar(
            text("SELECT department_id FROM agent_tasks WHERE id = :t"), {"t": task["id"]}
        )
    assert task_department == dept_a

    # Web 切到 B 后手动触发：execution 复制任务部门，request 回读仍 A
    await _switch_department(test_client, headers, dept_b)
    triggered = await test_client.post(f"/api/agent-tasks/{task['id']}/executions", headers=headers)
    assert triggered.status_code == 200, triggered.text
    execution_id = triggered.json()["id"]
    async with pg_manager.get_async_session_context() as db:
        execution_department = await db.scalar(
            text("SELECT department_id FROM task_executions WHERE id = :e"), {"e": execution_id}
        )
    assert execution_department == dept_a
    assert await _read_request_department(execution_id) == dept_a

    # API 触发：切换 Web 部门后，A key 仍按任务部门 A 触发；B key 拒绝且不创建事实
    key_b = await test_client.post(
        "/api/user/apikey/",
        headers=headers,
        json={"request_id": f"ni-task-key-b-{uuid.uuid4().hex[:12]}", "name": "NI-B"},
    )
    assert key_b.status_code == 200 and key_b.json()["api_key"]["department_id"] == dept_b
    await _switch_department(test_client, headers, dept_a)
    key_a = await test_client.post(
        "/api/user/apikey/",
        headers=headers,
        json={"request_id": f"ni-task-key-a-{uuid.uuid4().hex[:12]}", "name": "NI-A"},
    )
    assert key_a.status_code == 200 and key_a.json()["api_key"]["department_id"] == dept_a
    secret_a = key_a.json()["secret"]
    secret_b = key_b.json()["secret"]
    # 切回 B，保证触发时 Web 会话部门 ≠ key/task 部门
    await _switch_department(test_client, headers, dept_b)

    api_ok = await test_client.post(
        f"/api/agent-tasks/{task['id']}/trigger",
        headers={"Authorization": f"Bearer {secret_a}", "Idempotency-Key": f"ni-trigger-a-{uuid.uuid4().hex[:8]}"},
    )
    assert api_ok.status_code == 202, api_ok.text

    async with pg_manager.get_async_session_context() as db:
        before_count = await db.scalar(
            text("SELECT count(*) FROM task_executions WHERE task_id = :t"), {"t": task["id"]}
        )

    api_rejected = await test_client.post(
        f"/api/agent-tasks/{task['id']}/trigger",
        headers={"Authorization": f"Bearer {secret_b}", "Idempotency-Key": f"ni-trigger-b-{uuid.uuid4().hex[:8]}"},
    )
    assert api_rejected.status_code == 403, api_rejected.text
    async with pg_manager.get_async_session_context() as db:
        after_count = await db.scalar(
            text("SELECT count(*) FROM task_executions WHERE task_id = :t"), {"t": task["id"]}
        )
        rejected_request = await db.scalar(
            text("SELECT count(*) FROM agent_run_requests WHERE request_id LIKE 'ni-trigger-b-%'")
        )
    assert after_count == before_count
    assert rejected_request == 0

    # 撤销 A 成员后：A key 触发被拒（key 部门上下文失效）
    rev = await admin_revision_headers(test_client, admin_headers)
    removed = await test_client.delete(f"/api/departments/{dept_a}/members/{case['user_id']}", headers=rev)
    assert removed.status_code == 204, removed.text
    revoked = await test_client.post(
        f"/api/agent-tasks/{task['id']}/trigger",
        headers={"Authorization": f"Bearer {secret_a}", "Idempotency-Key": f"ni-trigger-c-{uuid.uuid4().hex[:8]}"},
    )
    assert revoked.status_code == 403, revoked.text

    # 定时扫描：到期任务按任务固定部门解析所有者并提交；先等既有执行收束，
    # 避免定时触发被 skipped 合并路径吞掉
    from yuxi.utils.datetime_utils import utc_now_naive

    exec_repo = None
    for _ in range(45):
        await asyncio.sleep(2)
        async with pg_manager.get_async_session_context() as db:
            exec_repo = TaskExecutionRepository(db)
            all_exec = await exec_repo.list_by_task(task["id"])
            if all(e.status in ("succeeded", "failed", "cancelled") for e in all_exec):
                break
    else:
        raise AssertionError(f"前置执行未收束: {[(e.id, e.status) for e in all_exec]}")

    async with pg_manager.get_async_session_context() as db:
        # 所有者已无 A 成员关系：恢复成员事实以验证定时路径的固定部门提交
        await db.execute(
            text("INSERT INTO department_memberships (user_id, department_id, role) VALUES (:u, :d, 'user')"),
            {"u": case["user_id"], "d": dept_a},
        )
        # 此刻才启用定时：任务对后台 worker 扫描可见的同时由本测试立即触发
        await db.execute(
            text(
                "UPDATE agent_tasks SET schedule_mode = 'cron', schedule_cron = '*/5 * * * *',"
                " schedule_timezone = 'UTC', next_run_at = :past WHERE id = :t"
            ),
            {"past": utc_now_naive() - timedelta(minutes=2), "t": task["id"]},
        )
        await db.commit()
        await AgentTaskSchedulerService(db).scan_due()

    # 后台 worker 与本测试共用扫描入口：轮询任一入口创建的定时执行
    schedule_execution = None
    for _ in range(30):
        async with pg_manager.get_async_session_context() as db:
            rows = (
                await db.execute(
                    text(
                        "SELECT id, department_id FROM task_executions"
                        " WHERE task_id = :t AND trigger_type = 'schedule' ORDER BY queued_at DESC LIMIT 1"
                    ),
                    {"t": task["id"]},
                )
            ).first()
        if rows is not None:
            schedule_execution = rows
            break
        await asyncio.sleep(2)
    assert schedule_execution is not None, "定时扫描未创建执行"
    assert schedule_execution[1] == dept_a
    for _ in range(30):
        if await _read_request_department(schedule_execution[0]) is not None:
            break
        await asyncio.sleep(1)
    assert await _read_request_department(schedule_execution[0]) == dept_a


# ================================================================
# 无部门绑定：四类入口明确拒绝且不创建请求
# ================================================================


async def test_unbound_entries_rejected_without_request(test_client, case_env):
    from yuxi.channels.wps_xiezuo import WPSChannelEvent
    from yuxi.services.agentscope_channel_service import encrypt_app_secret
    from yuxi.services.wps_channel_ingress_service import submit_wps_channel_event
    from yuxi.storage.postgres.manager import pg_manager
    from yuxi.utils.auth_utils import AuthUtils

    case = case_env
    headers = {"Authorization": f"Bearer {case['token']}"}

    # 无部门 API Key（旧事实）：HTTP 提交 403，不创建请求
    unbound_request_id = f"ni-unbound-key-{uuid.uuid4().hex[:12]}"
    async with pg_manager.get_async_session_context() as db:
        full_key, key_hash, key_prefix = AuthUtils.derive_api_key(f"ni-legacy-key:{uuid.uuid4().hex}", case["user_id"])
        await db.execute(
            text(
                "INSERT INTO api_keys (key_hash, key_prefix, request_id, name, user_id, department_id,"
                " created_by, created_at, is_enabled) VALUES (:h, :p, :r, :n, :u, NULL, :c, now(), true)"
            ),
            {
                "h": key_hash,
                "p": key_prefix,
                "r": f"ni-legacy-{uuid.uuid4().hex[:12]}",
                "n": "无部门旧 Key",
                "u": case["user_id"],
                "c": str(case["user_id"]),
            },
        )
        await db.commit()
    rejected = await test_client.post(
        "/api/agent-invocation/agent-call/runs",
        headers={"Authorization": f"Bearer {full_key}"},
        json={
            "agent_slug": TEST_AGENT_SLUG,
            "messages": [{"role": "user", "content": "请回复：完成"}],
            "async_mode": True,
            "queue_policy": "enqueue",
            "request_id": unbound_request_id,
        },
    )
    assert rejected.status_code == 403, rejected.text
    assert await _read_request_department(unbound_request_id) is None

    # 无部门 CLI 批准记录：兑换 409
    device_code, user_code = await _start_cli_session(test_client)
    approve = await test_client.post(f"/api/auth/cli/sessions/{user_code}/approve", headers=headers)
    assert approve.status_code == 200, approve.text
    async with pg_manager.get_async_session_context() as db:
        await db.execute(
            text("UPDATE cli_auth_sessions SET approved_department_id = NULL WHERE user_code = :c"),
            {"c": user_code},
        )
        await db.commit()
    exchange = await test_client.post("/api/auth/cli/sessions/token", json={"device_code": device_code})
    assert exchange.status_code == 409, exchange.text

    # 无部门 Channel binding：ingress 拒绝且不创建请求
    binding_id = str(uuid.uuid4())
    async with pg_manager.get_async_session_context() as db:
        await db.execute(
            text(
                "INSERT INTO agentscope_channel_bindings (id, owner_uid, department_id, agent_slug, name,"
                " channel_type, app_id, encrypted_app_secret, allow_from, group_reply_policy, enabled,"
                " sync_status, created_by, updated_by, created_at, updated_at)"
                " VALUES (:i, :o, NULL, :s, :n, 'wps_xiezuo', :a, :sec, '[\"wps-user-1\"]'::jsonb,"
                " 'mention_only', true, 'synced', :o, :o, now(), now())"
            ),
            {
                "i": binding_id,
                "o": case["uid"],
                "s": TEST_AGENT_SLUG,
                "n": "无部门旧 binding",
                "a": f"ni-wps-legacy-{uuid.uuid4().hex[:8]}",
                "sec": encrypt_app_secret("legacy-secret"),
            },
        )
        await db.commit()
    event = WPSChannelEvent(
        channel_id=binding_id,
        channel_user_id="wps-user-1",
        channel_message_id=f"msg-{uuid.uuid4().hex[:8]}",
        chat_id=f"chat-{uuid.uuid4().hex[:8]}",
        text="无部门 binding 的消息",
        received_at="2026-09-17T00:00:00Z",
    )
    with pytest.raises(ValueError, match="未绑定部门"):
        await submit_wps_channel_event(binding_id, event)
    async with pg_manager.get_async_session_context() as db:
        unbound_requests = await db.scalar(
            text("SELECT count(*) FROM agent_run_requests WHERE request_id LIKE 'wps_request_%' AND uid = :u"),
            {"u": case["uid"]},
        )
    assert unbound_requests == 0

    # 无部门任务：定时扫描跳过派发，不创建执行/请求
    async with pg_manager.get_async_session_context() as db:
        unbound_task_id = str(uuid.uuid4())
        import json as _json

        share_config = _json.dumps(
            {
                "version": 2,
                "read_scope": {
                    "access_level": "user",
                    "department_ids": [],
                    "user_uids": [case["uid"]],
                },
                "manage_scope": None,
            }
        )
        await db.execute(
            text(
                "INSERT INTO agent_tasks (id, name, owner_uid, department_id, agent_id, agent_slug_snapshot,"
                " prompt, share_config, enabled, api_enabled, schedule_mode, schedule_cron, schedule_timezone,"
                " next_run_at, tool_approval_mode, created_at, updated_at)"
                " VALUES (:i, 'NI-无部门任务', :o, NULL, NULL, :s, 'prompt', CAST(:share AS jsonb),"
                " true, false, 'cron', '*/5 * * * *', 'UTC',"
                " now() - interval '2 minutes', 'always_trust', now(), now())"
            ),
            {"i": unbound_task_id, "o": case["uid"], "s": TEST_AGENT_SLUG, "share": share_config},
        )
        await db.commit()
        from yuxi.services.agent_task_scheduler_service import AgentTaskSchedulerService

        counts = await AgentTaskSchedulerService(db).scan_due()
        assert counts["triggered"] == 0
        unbound_executions = await db.scalar(
            text("SELECT count(*) FROM task_executions WHERE task_id = :t"), {"t": unbound_task_id}
        )
    assert unbound_executions == 0
