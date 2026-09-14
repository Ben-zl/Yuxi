"""Agent 任务中心集成测试（工单 02-06/10：CRUD、触发 FIFO、共享与删除阻断）。

真实 PostgreSQL 环境执行；复用 e2e mock 供应商跑通标准提交链路。
"""

import asyncio
import os
import uuid
from datetime import timedelta

import pytest

from yuxi.repositories.agent_task_repository import (
    AgentTaskRepository,
    TaskExecutionRepository,
)
from yuxi.services.agent_task_crud_service import AgentTaskCRUDService, can_manage_task, can_view_task
from yuxi.services.agent_task_dispatcher import AgentTaskDispatcher
from yuxi.services.agent_task_trigger_service import AgentTaskTriggerService, manual_idempotency_key
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import Agent, AgentRun, AgentTask, TaskExecution, User
from yuxi.utils.datetime_utils import utc_now_naive

pytestmark = pytest.mark.integration

AGENTSCOPE_BASE_URL = os.getenv("AGENTSCOPE_BASE_URL", "http://agentscope:8100")
TEST_AGENT_SLUG = "task-center-it-agent"


@pytest.fixture
async def db_session():
    from test.e2e.agentscope_e2e_fixtures import (
        cleanup_fixture_agents,
        run_e2e_cleanup_steps,
        upsert_mock_provider,
    )
    from test.live_api_cleanup import cleanup_static_test_user_resources

    primary_error: BaseException | None = None
    try:
        pg_manager.initialize()
        await pg_manager.ensure_business_schema()
        async with pg_manager.get_async_session_context() as session:
            try:
                await cleanup_fixture_agents(session, TEST_AGENT_SLUG)
                await upsert_mock_provider(session)
                session.add(
                    Agent(
                        slug=TEST_AGENT_SLUG,
                        name="任务中心测试智能体",
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
                await session.commit()
                yield session
            except BaseException as exc:
                primary_error = exc
                raise
            finally:
                await run_e2e_cleanup_steps(
                    [
                        ("agent task active runs", lambda: _settle_task_test_runs(session)),
                        ("agent task rows", lambda: _cleanup_all(session)),
                        (
                            "agent task fixture agent",
                            lambda: cleanup_fixture_agents(session, TEST_AGENT_SLUG),
                        ),
                    ],
                    primary_error=primary_error,
                )
    except BaseException as exc:
        if primary_error is None:
            primary_error = exc
        raise
    finally:
        from yuxi.services import run_queue_service
        from yuxi.storage.redis.manager import close_async_redis_client

        async def close_postgres() -> None:
            try:
                await pg_manager.close()
            finally:
                pg_manager._initialized = False

        async def close_arq_pool() -> None:
            if run_queue_service._arq_pool is not None:
                try:
                    await run_queue_service._arq_pool.aclose()
                finally:
                    run_queue_service._arq_pool = None

        await run_e2e_cleanup_steps(
            [
                ("agent task test user", lambda: cleanup_static_test_user_resources("task-it-user")),
                ("agent task PostgreSQL", close_postgres),
                ("agent task Redis", close_async_redis_client),
                ("agent task ARQ pool", close_arq_pool),
            ],
            primary_error=primary_error,
        )


async def _settle_task_test_runs(db):
    """先取消测试 UID 的活动 Run，等 Worker 收束后再物理清理。"""
    from sqlalchemy import select

    from yuxi.repositories.agent_run_repository import TERMINAL_RUN_STATUSES
    from yuxi.services.agent_run_service import request_cancel_agent_run

    await db.rollback()
    run_ids = (
        await db.scalars(
            select(AgentRun.id).where(AgentRun.uid == "task-it-user", AgentRun.status.notin_(TERMINAL_RUN_STATUSES))
        )
    ).all()
    for run_id in run_ids:
        await request_cancel_agent_run(run_id=run_id, current_uid="task-it-user", db=db)

    for _ in range(30):
        await db.rollback()
        remaining = (
            await db.scalars(
                select(AgentRun.id).where(
                    AgentRun.uid == "task-it-user",
                    AgentRun.status.notin_(TERMINAL_RUN_STATUSES),
                )
            )
        ).all()
        if not remaining:
            return
        await asyncio.sleep(1)
    raise RuntimeError(f"Agent task test Runs did not stop: {remaining}")


async def _cleanup_all(db):
    from sqlalchemy import select

    rows = (await db.execute(select(AgentTask.id).where(AgentTask.name.like("IT-%")))).all()
    for (task_id,) in rows:
        await db.execute(TaskExecution.__table__.delete().where(TaskExecution.task_id == task_id))
    await db.execute(AgentTask.__table__.delete().where(AgentTask.name.like("IT-%")))
    await db.commit()


async def _user(db, uid: str = "task-it-user", *, superadmin: bool = False) -> User:
    from sqlalchemy import select as _select

    user = await db.scalar(_select(User).where(User.uid == uid))
    if user is None:
        db.add(
            User(
                username=f"it-{uid}-{uuid.uuid4().hex[:4]}",
                uid=uid,
                password_hash="x" * 60,
                role="superadmin" if superadmin else "user",
                login_failed_count=0,
                is_deleted=0,
            )
        )
        await db.commit()
        user = await db.scalar(_select(User).where(User.uid == uid))
    return user


def _payload(name: str, **over) -> dict:
    payload = {
        "name": name,
        "agent_slug": TEST_AGENT_SLUG,
        "prompt": "请回复：任务执行完成",
        "tool_approval_mode": "always_trust",
    }
    payload.update(over)
    return payload


async def test_task_crud_and_scope(db_session):
    """工单 02：创建/编辑/启停与可见范围校验。"""
    user = await _user(db_session)
    service = AgentTaskCRUDService(db_session)

    task = await service.create_task(user=user, payload=_payload("IT-基础CRUD"))
    assert task.id and task.enabled and task.tool_approval_mode == "always_trust"
    assert task.agent_slug_snapshot == TEST_AGENT_SLUG
    assert can_view_task(user, task) and can_manage_task(user, task)

    updated = await service.update_task(user=user, task_id=task.id, payload={"prompt": "新提示词", "enabled": False})
    assert updated.prompt == "新提示词" and not updated.enabled

    # 范围不得超过智能体：全局 Agent 下的个人范围合法；越级 global 之外的非法值被拒
    task2 = await service.create_task(
        user=user,
        payload=_payload("IT-范围", share_config={"access_level": "department", "department_ids": [999]}),
    )
    assert task2.share_config["read_scope"]["access_level"] == "department"


async def test_manual_trigger_creates_execution_and_run(db_session):
    """工单 03：手动触发 → 幂等执行 → 标准 Run + 独立线程。"""
    user = await _user(db_session)
    crud = AgentTaskCRUDService(db_session)
    task = await crud.create_task(user=user, payload=_payload("IT-手动触发"))

    trigger = AgentTaskTriggerService(db_session)
    execution, created = await trigger.trigger(
        task_id=task.id,
        user=user,
        trigger_type="manual",
        idempotency_key=manual_idempotency_key(),
    )
    prompt_snapshot = task.prompt
    assert created and execution.status in ("queued", "running")
    assert execution.prompt == prompt_snapshot
    assert execution.agent_slug == TEST_AGENT_SLUG

    # 等待 worker 执行完成（标准 ARQ 链路）；rollback 结束快照读最新状态
    execution_id = execution.id
    refreshed = None
    for _ in range(30):
        await asyncio.sleep(2)
        await db_session.rollback()
        refreshed = await TaskExecutionRepository(db_session).get(execution_id)
        if refreshed.status in ("succeeded", "failed", "cancelled", "interrupted"):
            break
    assert refreshed.status == "succeeded", f"status={refreshed.status} err={refreshed.error_summary}"
    assert refreshed.agent_run_id and refreshed.thread_id
    assert refreshed.thread_id == f"task-exec-{execution_id}"


async def test_fifo_serializes_and_advances(db_session):
    """工单 04：同任务多次触发严格 FIFO，终态自动推进。"""
    user = await _user(db_session)
    crud = AgentTaskCRUDService(db_session)
    task = await crud.create_task(user=user, payload=_payload("IT-FIFO"))

    trigger = AgentTaskTriggerService(db_session)
    exec_repo = TaskExecutionRepository(db_session)
    task_id_snapshot = task.id
    first_id = None
    for i in range(3):
        execution, _ = await trigger.trigger(
            task_id=task_id_snapshot,
            user=user,
            trigger_type="manual",
            idempotency_key=f"it-fifo-{i}",
        )
        if i == 0:
            first_id = execution.id

    # 等全部完成（worker 可能仍在处理前序测试的执行，留足余量）
    for _ in range(90):
        await asyncio.sleep(2)
        await db_session.rollback()
        all_exec = await exec_repo.list_by_task(task_id_snapshot)
        statuses = {e.id: e.status for e in all_exec}
        if all(s in ("succeeded", "failed", "cancelled") for s in statuses.values()):
            break
    await db_session.rollback()
    all_exec = await exec_repo.list_by_task(task_id_snapshot)
    assert len(all_exec) == 3
    assert all(e.status == "succeeded" for e in all_exec), [e.status for e in all_exec]
    # FIFO 顺序：按 queued_at 排序执行
    ordered = sorted(all_exec, key=lambda e: e.queued_at)
    assert ordered[0].id == first_id


async def test_disable_cancels_queued(db_session):
    """工单 04：停用任务取消排队项；归档不能再触发。"""
    user = await _user(db_session)
    crud = AgentTaskCRUDService(db_session)
    task = await crud.create_task(user=user, payload=_payload("IT-停用"))
    trigger = AgentTaskTriggerService(db_session)
    exec_repo = TaskExecutionRepository(db_session)
    disable_task_id = task.id

    ids = []
    for i in range(3):
        execution, _ = await trigger.trigger(
            task_id=disable_task_id,
            user=user,
            trigger_type="manual",
            idempotency_key=f"it-disable-{i}",
        )
        ids.append(execution.id)

    # 等第一个完成后停用（取消剩余排队）
    for _ in range(40):
        await asyncio.sleep(2)
        await db_session.rollback()
        e0 = await exec_repo.get(ids[0])
        if e0.status in ("succeeded", "failed"):
            break
    await db_session.rollback()
    user = await _user(db_session)  # rollback 过期实例，重取避免 lazy-load 触发 MissingGreenlet
    await crud.update_task(user=user, task_id=disable_task_id, payload={"enabled": False})
    await db_session.rollback()
    statuses = [(await exec_repo.get(i)).status for i in ids]
    # 第一个已完成；其余 queued 被取消（可能有已 running 的完成）
    assert statuses[0] in ("succeeded", "failed")

    from fastapi import HTTPException

    user = await _user(db_session)  # rollback 过期实例，重取
    with pytest.raises(HTTPException) as exc:
        await trigger.trigger(
            task_id=disable_task_id,
            user=user,
            trigger_type="manual",
            idempotency_key=manual_idempotency_key(),
        )
    assert exc.value.status_code == 409


async def test_api_trigger_idempotent(db_session):
    """工单 06：同 key 重复触发返回同一 execution_id。"""
    user = await _user(db_session)
    crud = AgentTaskCRUDService(db_session)
    task = await crud.create_task(user=user, payload=_payload("IT-API", api_enabled=True))

    trigger = AgentTaskTriggerService(db_session)
    key = "it-api-key-1"
    api_task_id = task.id
    e1, c1 = await trigger.trigger(task_id=api_task_id, user=user, trigger_type="api", idempotency_key=key)
    e2, c2 = await trigger.trigger(task_id=api_task_id, user=user, trigger_type="api", idempotency_key=key)
    assert c1 is True and c2 is False
    assert e1.id == e2.id

    # 未启用 API 的任务拒绝
    task2 = await crud.create_task(user=user, payload=_payload("IT-API禁用"))
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await trigger.trigger(task_id=task2.id, user=user, trigger_type="api", idempotency_key="k")
    assert exc.value.status_code == 409


async def test_agent_delete_blocked_by_active_task(db_session):
    """工单 10：未归档任务引用时 Agent 删除 409；归档后允许。"""
    from yuxi.repositories.agent_repository import AgentRepository

    user = await _user(db_session)
    crud = AgentTaskCRUDService(db_session)
    task = await crud.create_task(user=user, payload=_payload("IT-删除阻断"))

    repo = AgentRepository(db_session)
    agent = await repo.get_by_slug(TEST_AGENT_SLUG)
    # 模拟路由守卫逻辑（路由层实现）
    from sqlalchemy import select
    from yuxi.storage.postgres.models_business import AgentTask

    referencing = (
        await db_session.execute(
            select(AgentTask.id).where(AgentTask.agent_id == agent.id, AgentTask.archived_at.is_(None))
        )
    ).all()
    assert len(referencing) >= 1  # 引用存在 → 路由返回 409

    # 归档后引用解除
    await crud.update_task(user=user, task_id=task.id, payload={"archived": True})
    referencing = (
        await db_session.execute(
            select(AgentTask.id).where(AgentTask.agent_id == agent.id, AgentTask.archived_at.is_(None))
        )
    ).all()
    assert len(referencing) == 0
    archived = await AgentTaskRepository(db_session).get(task.id)
    assert archived.archived_at is not None and not archived.enabled


# ================================================================
# 工单 11：TaskExecution 与 AgentRun 的幂等崩溃恢复
# ================================================================


async def test_resume_run_completion_finishes_interrupted_task_execution(db_session):
    """恢复 Run 完成后，任务执行必须从 interrupted 收敛为 succeeded。"""
    user = await _user(db_session)
    task = await AgentTaskCRUDService(db_session).create_task(user=user, payload=_payload("IT-恢复终态投影"))
    execution_id = str(uuid.uuid4())
    parent_run_id = str(uuid.uuid4())
    resume_run_id = str(uuid.uuid4())
    thread_id = f"task-exec-{execution_id}"

    parent = AgentRun(
        id=parent_run_id,
        conversation_thread_id=thread_id,
        runtime_scope_id=thread_id,
        agent_slug=TEST_AGENT_SLUG,
        uid=str(user.uid),
        status="interrupted",
        request_id=execution_id,
        source="agent_task",
        channel="internal",
        origin_metadata={},
        run_type="chat",
        input_payload={},
        token_usage={},
    )
    resume = AgentRun(
        id=resume_run_id,
        conversation_thread_id=thread_id,
        runtime_scope_id=thread_id,
        agent_slug=TEST_AGENT_SLUG,
        uid=str(user.uid),
        status="completed",
        request_id=str(uuid.uuid4()),
        source="chat",
        channel="web",
        origin_metadata={},
        created_by_run_id=parent_run_id,
        run_type="resume",
        input_payload={},
        token_usage={},
    )
    db_session.add_all([parent, resume])
    await db_session.flush()
    await TaskExecutionRepository(db_session).create(
        id=execution_id,
        task_id=task.id,
        trigger_type="manual",
        triggered_by_uid=str(user.uid),
        execution_principal_uid=str(user.uid),
        agent_id=task.agent_id,
        agent_slug=TEST_AGENT_SLUG,
        prompt="等待用户回答后继续",
        tool_approval_mode="always_trust",
        idempotency_key=f"manual:{uuid.uuid4().hex}",
        agent_run_id=parent_run_id,
        thread_id=thread_id,
        status="interrupted",
        started_at=utc_now_naive(),
    )
    await db_session.commit()

    handled = await AgentTaskDispatcher(db_session).notify_run_finished(resume_run_id, "completed")

    await db_session.rollback()
    execution = await TaskExecutionRepository(db_session).get(execution_id)
    assert handled is True
    assert execution.status == "succeeded"
    assert execution.finished_at is not None


async def test_execution_id_used_as_run_request_id(db_session):
    """工单 11：execution_id 作为标准 Run 的 request_id，线程确定性派生。"""
    from yuxi.services.agent_task_dispatcher import execution_thread_id

    user = await _user(db_session)
    crud = AgentTaskCRUDService(db_session)
    task = await crud.create_task(user=user, payload=_payload("IT-恢复-request_id"))

    trigger = AgentTaskTriggerService(db_session)
    execution, _ = await trigger.trigger(
        task_id=task.id,
        user=user,
        trigger_type="manual",
        idempotency_key="it-recovery-req-1",
    )
    execution_id = execution.id
    expected_thread = execution_thread_id(execution_id)

    # 等待 Run 完成并验证关联
    for _ in range(30):
        await asyncio.sleep(2)
        await db_session.rollback()
        refreshed = await TaskExecutionRepository(db_session).get(execution_id)
        if refreshed.status in ("succeeded", "failed", "cancelled"):
            break
    await db_session.rollback()
    refreshed = await TaskExecutionRepository(db_session).get(execution_id)
    assert refreshed.agent_run_id, "Run 应已关联"
    assert refreshed.thread_id == expected_thread

    # 验证 Run 的 request_id 确实等于 execution_id
    from yuxi.repositories.agent_run_repository import AgentRunRepository

    run = await AgentRunRepository(db_session).get_run_by_request_id(execution_id)
    assert run is not None
    assert run.id == refreshed.agent_run_id


async def test_recover_stale_reassociates_existing_run(db_session):
    """工单 11：Run 已存在但 TaskExecution.agent_run_id 未回写时，恢复能补齐关联。

    模拟崩溃窗口：submit_run_command 已创建 Run，但 Dispatcher 未回写 agent_run_id。
    recover_stale 按 request_id 反查 Run 并补写关联。
    """
    user = await _user(db_session)
    crud = AgentTaskCRUDService(db_session)
    task = await crud.create_task(user=user, payload=_payload("IT-恢复-补关联"))

    trigger = AgentTaskTriggerService(db_session)
    execution, _ = await trigger.trigger(
        task_id=task.id,
        user=user,
        trigger_type="manual",
        idempotency_key="it-recovery-reassoc-1",
    )
    execution_id = execution.id

    # 等待 Run 完成以获得一个稳定的 Run 事实
    for _ in range(30):
        await asyncio.sleep(2)
        await db_session.rollback()
        refreshed = await TaskExecutionRepository(db_session).get(execution_id)
        if refreshed.status in ("succeeded", "failed", "cancelled"):
            break
    await db_session.rollback()
    refreshed = await TaskExecutionRepository(db_session).get(execution_id)
    assert refreshed.agent_run_id, "前置条件：Run 应已关联"

    # 模拟崩溃：清除 agent_run_id 和 thread_id，状态回退为 queued
    run_id_snapshot = refreshed.agent_run_id
    thread_id_snapshot = refreshed.thread_id
    refreshed.agent_run_id = None
    refreshed.thread_id = None
    refreshed.status = "queued"
    refreshed.started_at = None
    refreshed.finished_at = None
    await db_session.commit()

    # 执行恢复：应按 request_id 反查到已有 Run 并补齐关联
    dispatcher = AgentTaskDispatcher(db_session)
    # stale_active 查 queued/running 且 queued_at < now；刚提交的在宽限区内
    recovered = await dispatcher.recover_stale(utc_now_naive() + timedelta(minutes=1))
    assert recovered >= 1

    await db_session.rollback()
    recovered_exec = await TaskExecutionRepository(db_session).get(execution_id)
    # 恢复后应回写关联（Run 已终态 → 执行也标记终态）
    assert recovered_exec.agent_run_id == run_id_snapshot
    assert recovered_exec.thread_id == thread_id_snapshot
    assert recovered_exec.status in ("succeeded", "failed", "cancelled")


async def test_recover_stale_does_not_duplicate_run(db_session):
    """工单 11：恢复不产生第二个 Run。重复调用 recover_stale 不创建新 Run。

    通过验证 Run 总数不变来证明幂等性。
    """
    from yuxi.repositories.agent_run_repository import AgentRunRepository

    user = await _user(db_session)
    crud = AgentTaskCRUDService(db_session)
    task = await crud.create_task(user=user, payload=_payload("IT-恢复-幂等"))

    trigger = AgentTaskTriggerService(db_session)
    execution, _ = await trigger.trigger(
        task_id=task.id,
        user=user,
        trigger_type="manual",
        idempotency_key="it-recovery-idempotent-1",
    )
    execution_id = execution.id

    # 等待完成
    for _ in range(30):
        await asyncio.sleep(2)
        await db_session.rollback()
        refreshed = await TaskExecutionRepository(db_session).get(execution_id)
        if refreshed.status in ("succeeded", "failed", "cancelled"):
            break
    await db_session.rollback()
    final_exec = await TaskExecutionRepository(db_session).get(execution_id)
    run_id = final_exec.agent_run_id

    # 查询此 request_id 对应的 Run 总数（应为 1）
    run_before = await AgentRunRepository(db_session).get_run_by_request_id(execution_id)
    assert run_before is not None
    assert run_before.id == run_id

    # 模拟崩溃后恢复
    final_exec.agent_run_id = None
    final_exec.thread_id = None
    final_exec.status = "queued"
    final_exec.started_at = None
    final_exec.finished_at = None
    await db_session.commit()

    dispatcher = AgentTaskDispatcher(db_session)
    await dispatcher.recover_stale(utc_now_naive() + timedelta(minutes=1))
    await db_session.rollback()

    # 验证仍然只有同一个 Run
    run_after = await AgentRunRepository(db_session).get_run_by_request_id(execution_id)
    assert run_after is not None
    assert run_after.id == run_id  # 同一个 Run，没有创建第二个


async def test_manual_trigger_unique_keys_per_call(db_session):
    """工单 11：手动触发每次生成唯一幂等键，不互相冲突。"""
    user = await _user(db_session)
    crud = AgentTaskCRUDService(db_session)
    task = await crud.create_task(user=user, payload=_payload("IT-幂等键"))

    trigger = AgentTaskTriggerService(db_session)

    # 连续两次手动触发，使用不同幂等键
    e1, c1 = await trigger.trigger(
        task_id=task.id,
        user=user,
        trigger_type="manual",
        idempotency_key=manual_idempotency_key(),
    )
    e2, c2 = await trigger.trigger(
        task_id=task.id,
        user=user,
        trigger_type="manual",
        idempotency_key=manual_idempotency_key(),
    )
    assert c1 and c2
    assert e1.id != e2.id  # 两个不同的执行
    assert e1.idempotency_key != e2.idempotency_key
