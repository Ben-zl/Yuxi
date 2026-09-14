from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from test import live_api_cleanup as cleanup

from test.live_api_cleanup import (
    TEST_CONVERSATION_TITLE_PREFIX,
    CleanupConversationResource,
    cleanup_e2e_chat_resources,
    cleanup_provisioned_sandboxes,
    cleanup_pytest_knowledge_resources,
    is_test_conversation_title,
    make_test_conversation_metadata,
    make_test_conversation_title,
    make_test_resource_id,
    remove_e2e_thread_storage,
    remove_test_workdir,
)

pytestmark = pytest.mark.asyncio


async def test_e2e_cleanup_preserves_primary_failure_and_runs_remaining_steps():
    """清理失败不得覆盖主体异常，且后续清理步骤仍须执行。"""
    from test.e2e.agentscope_e2e_fixtures import run_e2e_cleanup_steps

    calls: list[str] = []

    async def failed_cleanup() -> None:
        calls.append("failed")
        raise RuntimeError("cleanup failed")

    async def successful_cleanup() -> None:
        calls.append("successful")

    async def scenario() -> None:
        primary_error: BaseException | None = None
        try:
            raise ValueError("primary failure")
        except BaseException as exc:
            primary_error = exc
            raise
        finally:
            await run_e2e_cleanup_steps(
                [
                    ("failed cleanup", failed_cleanup),
                    ("successful cleanup", successful_cleanup),
                ],
                primary_error=primary_error,
            )

    with pytest.raises(ValueError, match="primary failure") as exc_info:
        await scenario()

    assert calls == ["failed", "successful"]
    assert exc_info.value.__notes__ == [
        "E2E cleanup also failed: failed cleanup (RuntimeError)",
    ]


async def test_e2e_cleanup_failure_fails_test_when_body_succeeds():
    """主体成功时，清理失败必须以异常组结束测试。"""
    from test.e2e.agentscope_e2e_fixtures import run_e2e_cleanup_steps

    async def failed_cleanup() -> None:
        raise RuntimeError("cleanup failed")

    with pytest.raises(ExceptionGroup, match="E2E cleanup failed") as exc_info:
        await run_e2e_cleanup_steps(
            [("failed cleanup", failed_cleanup)],
            primary_error=None,
        )

    assert len(exc_info.value.exceptions) == 1
    assert exc_info.value.exceptions[0].__notes__ == [
        "E2E cleanup step: failed cleanup",
    ]


async def test_e2e_cleanup_cancellation_preserves_primary_failure_and_runs_remaining_steps():
    """清理取消不得覆盖主体异常，也不得阻断后续清理步骤。"""
    from test.e2e.agentscope_e2e_fixtures import run_e2e_cleanup_steps

    calls: list[str] = []

    async def cancelled_cleanup() -> None:
        calls.append("cancelled")
        raise asyncio.CancelledError

    async def successful_cleanup() -> None:
        calls.append("successful")

    async def scenario() -> None:
        primary_error: BaseException | None = None
        try:
            raise ValueError("primary failure")
        except BaseException as exc:
            primary_error = exc
            raise
        finally:
            await run_e2e_cleanup_steps(
                [
                    ("cancelled cleanup", cancelled_cleanup),
                    ("successful cleanup", successful_cleanup),
                ],
                primary_error=primary_error,
            )

    with pytest.raises(ValueError, match="primary failure") as exc_info:
        await scenario()

    assert calls == ["cancelled", "successful"]
    assert exc_info.value.__notes__ == [
        "E2E cleanup also failed: cancelled cleanup (CancelledError)",
    ]


async def test_e2e_cleanup_cancellation_fails_test_when_body_succeeds():
    """主体成功时，清理取消必须以 BaseExceptionGroup 结束测试。"""
    from test.e2e.agentscope_e2e_fixtures import run_e2e_cleanup_steps

    async def cancelled_cleanup() -> None:
        raise asyncio.CancelledError

    with pytest.raises(BaseExceptionGroup, match="E2E cleanup failed") as exc_info:
        await run_e2e_cleanup_steps(
            [("cancelled cleanup", cancelled_cleanup)],
            primary_error=None,
        )

    assert len(exc_info.value.exceptions) == 1
    assert isinstance(exc_info.value.exceptions[0], asyncio.CancelledError)
    assert exc_info.value.exceptions[0].__notes__ == [
        "E2E cleanup step: cancelled cleanup",
    ]


async def test_e2e_cleanup_nested_cancellation_group_does_not_block_later_steps():
    """嵌套 fence 产生的 BaseExceptionGroup 也必须允许外层继续清理。"""
    from test.e2e.agentscope_e2e_fixtures import run_e2e_cleanup_steps

    calls: list[str] = []

    async def nested_cleanup() -> None:
        async def cancelled() -> None:
            raise asyncio.CancelledError

        await run_e2e_cleanup_steps(
            [("nested cancelled", cancelled)],
            primary_error=None,
        )

    async def later_cleanup() -> None:
        calls.append("later")

    with pytest.raises(BaseExceptionGroup, match="E2E cleanup failed"):
        await run_e2e_cleanup_steps(
            [
                ("nested cleanup", nested_cleanup),
                ("later cleanup", later_cleanup),
            ],
            primary_error=None,
        )

    assert calls == ["later"]


async def test_memory_e2e_user_creation_returns_cleanup_identity_before_login():
    """Memory E2E 登录失败前必须已经保留可物理清理的用户身份。"""
    from test.e2e import test_agentscope_memory_e2e as memory_e2e

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth/me":
            return httpx.Response(200, json={"role": "superadmin"})
        if request.url.path == "/api/departments":
            return httpx.Response(200, json=[{"id": 7}])
        if request.url.path == "/api/auth/users":
            return httpx.Response(200, json={"id": 11, "uid": "e2e_memory_deadbeef"})
        if request.url.path == "/api/auth/token":
            return httpx.Response(503, text="login unavailable")
        raise AssertionError(f"unexpected request: {request.method} {request.url.path}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test") as client:
        user, password = await memory_e2e._create_user(client, {"Authorization": "Bearer admin"})
        assert user == {"id": 11, "uid": "e2e_memory_deadbeef"}
        assert password.startswith("Pw!")
        with pytest.raises(AssertionError, match="login unavailable"):
            await memory_e2e._login_user(client, user["uid"], password)


async def test_static_fixture_user_cleanup_uses_owned_resource_cleanup(monkeypatch):
    """静态 E2E UID 必须只通过 canonical 资源清理器删除。"""
    from test.e2e import agentscope_e2e_fixtures as fixtures

    resource = CleanupConversationResource(
        conversation_id=1,
        project_id="project-1",
        thread_id="thread-1",
        uid="e2e-static",
        status="completed",
        workdir_path="projects/00000000-0000-0000-0000-000000000001",
    )
    calls = []

    async def cleanup_resources(uid: str):
        calls.append(uid)

    monkeypatch.setattr(cleanup, "cleanup_static_test_user_resources", cleanup_resources, raising=False)
    monkeypatch.setattr(
        cleanup,
        "delete_test_conversation_rows",
        AsyncMock(side_effect=AssertionError("row-only cleanup must not be used")),
    )

    class FakeDB:
        commit = AsyncMock()
        execute = AsyncMock(side_effect=AssertionError("row-only cleanup must not be used"))
        expire_all = Mock()

    db = FakeDB()
    await fixtures.cleanup_test_users(db, resource.uid)

    assert calls == [resource.uid]
    db.execute.assert_not_awaited()
    db.expire_all.assert_called_once_with()


async def test_static_user_cleanup_destroys_agentscope_runtime_before_rows(monkeypatch):
    """物理删除映射前必须先让 AgentScope 销毁对应 Workspace。"""
    resource = CleanupConversationResource(
        conversation_id=1,
        project_id="project-1",
        thread_id="thread-1",
        uid="e2e-static",
        status="completed",
        workdir_path=None,
    )
    calls: list[str] = []

    async def list_resources(_uid: str, *, include_all_owned: bool):
        assert include_all_owned is True
        return {resource.thread_id: resource}

    async def destroy_runtime(uid: str, thread_ids: set[str]):
        assert uid == resource.uid
        assert thread_ids == {resource.thread_id}
        calls.append("runtime")

    async def no_op(*_args, **_kwargs):
        return None

    async def delete_rows(*_args, **_kwargs):
        calls.append("rows")

    monkeypatch.setattr(cleanup, "list_test_conversation_resources", list_resources)
    monkeypatch.setattr(cleanup, "cleanup_test_agentscope_thread_resources", destroy_runtime, raising=False)
    monkeypatch.setattr(cleanup, "validate_test_workdirs_exclusive", no_op)
    monkeypatch.setattr(cleanup, "validate_test_runs_terminal", no_op)
    monkeypatch.setattr(cleanup, "delete_test_conversation_resources", delete_rows)
    monkeypatch.setattr(cleanup, "delete_orphaned_test_projects", no_op)
    monkeypatch.setattr(cleanup, "_assert_test_user_has_no_projects", no_op)
    monkeypatch.setattr(cleanup, "_delete_static_test_user_record", no_op)
    monkeypatch.setattr(cleanup, "remove_test_user_workspace_root", Mock())

    await cleanup.cleanup_static_test_user_resources(resource.uid)

    assert calls == ["runtime", "rows"]


async def test_session_cleanup_preserves_active_cleanup_identity():
    """会话级物理清理不得删除当前用于认证的固定 E2E 管理员。"""
    from test.e2e.conftest import _static_test_user_cleanup_targets

    assert _static_test_user_cleanup_targets(
        ["e2e-goal-verifier", "e2e-memory-deadbeef", "e2e-snapshot-deadbeef"],
        cleanup_uid="e2e-goal-verifier",
    ) == ["e2e-memory-deadbeef", "e2e-snapshot-deadbeef"]


async def test_session_cleanup_deletes_active_cleanup_identity_only_at_final_teardown():
    """认证管理员在 setup 保留，并在 final teardown 成为最后清理目标。"""
    from test.e2e.conftest import _final_static_test_user_cleanup_target

    discovered = ["e2e-goal-verifier", "e2e-memory-deadbeef"]
    assert (
        _final_static_test_user_cleanup_target(
            discovered,
            cleanup_uid="e2e-goal-verifier",
            final_teardown=False,
        )
        is None
    )
    assert (
        _final_static_test_user_cleanup_target(
            discovered,
            cleanup_uid="e2e-goal-verifier",
            final_teardown=True,
        )
        == "e2e-goal-verifier"
    )


async def test_session_cleanup_fence_attempts_each_other_user_after_prior_failure(monkeypatch):
    """前序类别和首个用户失败都不能阻断其他静态用户清理。"""
    from test.e2e import conftest as e2e_conftest

    calls: list[str] = []

    async def fail_chat(*_args, **_kwargs):
        calls.append("chat")
        raise RuntimeError("chat cleanup failed")

    async def list_uids():
        return ["e2e-goal-verifier", "e2e-a", "e2e-b"]

    async def cleanup_user(uid: str):
        calls.append(uid)
        if uid == "e2e-a":
            raise RuntimeError("first user cleanup failed")

    async def no_op(*_args, **_kwargs):
        return None

    monkeypatch.setattr(e2e_conftest, "cleanup_test_chat_resources", fail_chat)
    monkeypatch.setattr(e2e_conftest, "list_static_test_user_uids", list_uids)
    monkeypatch.setattr(e2e_conftest, "cleanup_static_test_user_resources", cleanup_user)
    monkeypatch.setattr(e2e_conftest, "cleanup_orphaned_static_test_departments", no_op)
    monkeypatch.setattr(e2e_conftest, "cleanup_orphaned_static_test_user_workspace_roots", no_op)
    monkeypatch.setattr(e2e_conftest, "list_test_sandbox_ids", AsyncMock(return_value=set()))
    monkeypatch.setattr(e2e_conftest, "cleanup_pytest_knowledge_resources", no_op)
    monkeypatch.setattr(e2e_conftest, "LITE_MODE", True)

    with pytest.raises(ExceptionGroup, match="E2E cleanup failed"):
        await e2e_conftest._cleanup_e2e_resources(
            client=object(),
            headers={},
            cleanup_uid="e2e-goal-verifier",
            final_teardown=True,
            primary_error=None,
        )

    assert calls == ["chat", "e2e-a", "e2e-b"]


async def test_session_cleanup_preserves_sandbox_identity_before_deleting_run_rows(monkeypatch):
    """Run 行删除后不能再推导 Sandbox ID，必须先捕获归属再清理。"""
    from test.e2e import conftest as e2e_conftest

    run_scope_ids = {"sandbox-from-run"}
    removed_ids: list[set[str]] = []

    async def list_ids(_uid, *, include_all_owned=False):
        return set(run_scope_ids)

    async def delete_chat(*_args, **_kwargs):
        run_scope_ids.clear()

    async def delete_sandboxes(_client, _headers, sandbox_ids):
        removed_ids.append(set(sandbox_ids))

    async def no_op(*_args, **_kwargs):
        return None

    class ProvisionerClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(e2e_conftest, "list_test_sandbox_ids", list_ids)
    monkeypatch.setattr(e2e_conftest, "cleanup_test_chat_resources", delete_chat)
    monkeypatch.setattr(e2e_conftest, "list_static_test_user_uids", AsyncMock(return_value=[]))
    monkeypatch.setattr(e2e_conftest, "cleanup_orphaned_static_test_departments", no_op)
    monkeypatch.setattr(e2e_conftest, "cleanup_orphaned_static_test_user_workspace_roots", no_op)
    monkeypatch.setattr(e2e_conftest, "cleanup_provisioned_sandboxes", delete_sandboxes)
    monkeypatch.setattr(e2e_conftest.httpx, "AsyncClient", lambda **_kwargs: ProvisionerClient())
    monkeypatch.setattr(e2e_conftest, "SANDBOX_PROVISIONER_TOKEN", "test-token")
    monkeypatch.setattr(e2e_conftest, "LITE_MODE", True)

    await e2e_conftest._cleanup_e2e_resources(
        client=object(),
        headers={},
        cleanup_uid="e2e-goal-verifier",
        final_teardown=True,
        primary_error=None,
    )

    assert removed_ids == [{"sandbox-from-run"}]


async def test_session_cleanup_captures_unmarked_static_user_sandbox(monkeypatch):
    """静态 UID 的未标记线程也要在 Run 删除前捕获 Sandbox。"""
    from test.e2e import conftest as e2e_conftest

    owned = {"e2e-proto": {"sandbox-from-unmarked-thread"}}
    removed_ids: list[set[str]] = []

    async def list_ids(uid, *, include_all_owned=False):
        return set(owned.get(uid, set())) if include_all_owned else set()

    async def delete_static_user(uid):
        owned.pop(uid, None)

    async def delete_sandboxes(_client, _headers, sandbox_ids):
        removed_ids.append(set(sandbox_ids))

    async def no_op(*_args, **_kwargs):
        return None

    class ProvisionerClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(e2e_conftest, "list_test_sandbox_ids", list_ids)
    monkeypatch.setattr(e2e_conftest, "list_static_test_user_uids", AsyncMock(return_value=["e2e-proto"]))
    monkeypatch.setattr(e2e_conftest, "cleanup_static_test_user_resources", delete_static_user)
    monkeypatch.setattr(e2e_conftest, "cleanup_test_chat_resources", no_op)
    monkeypatch.setattr(e2e_conftest, "cleanup_orphaned_static_test_departments", no_op)
    monkeypatch.setattr(e2e_conftest, "cleanup_orphaned_static_test_user_workspace_roots", no_op)
    monkeypatch.setattr(e2e_conftest, "cleanup_provisioned_sandboxes", delete_sandboxes)
    monkeypatch.setattr(e2e_conftest.httpx, "AsyncClient", lambda **_kwargs: ProvisionerClient())
    monkeypatch.setattr(e2e_conftest, "SANDBOX_PROVISIONER_TOKEN", "test-token")
    monkeypatch.setattr(e2e_conftest, "LITE_MODE", True)

    await e2e_conftest._cleanup_e2e_resources(
        client=object(),
        headers={},
        cleanup_uid="e2e-goal-verifier",
        final_teardown=True,
        primary_error=None,
    )

    assert removed_ids == [{"sandbox-from-unmarked-thread"}]
    assert not owned


async def test_session_setup_login_failure_deletes_provisioned_static_identity(monkeypatch):
    """provisioning 后认证失败必须通过数据库路径删除临时管理员。"""
    from test.e2e import conftest as e2e_conftest

    calls: list[str] = []

    async def provision(uid: str, password: str):
        assert uid == "e2e-goal-verifier"
        assert password == "test-password"
        calls.append("provision")

    async def fail_authenticated_cleanup(*_args, **_kwargs):
        calls.append("login")
        raise RuntimeError("login failed")

    async def cleanup_user(uid: str):
        calls.append(f"delete:{uid}")

    monkeypatch.setattr(e2e_conftest, "CLEANUP_USERNAME", "e2e-goal-verifier")
    monkeypatch.setattr(e2e_conftest, "CLEANUP_PASSWORD", "test-password")
    monkeypatch.setattr(e2e_conftest, "ensure_static_test_admin_identity", provision)
    monkeypatch.setattr(e2e_conftest, "_run_authenticated_e2e_cleanup", fail_authenticated_cleanup, raising=False)
    monkeypatch.setattr(e2e_conftest, "cleanup_static_test_user_resources", cleanup_user)

    with pytest.raises(RuntimeError, match="login failed"):
        await e2e_conftest._run_e2e_cleanup_session(
            final_teardown=False,
            primary_error=None,
        )

    assert calls == ["provision", "login", "delete:e2e-goal-verifier"]


async def test_session_teardown_provisioning_failure_preserves_primary_error(monkeypatch):
    """主体失败时 provisioning 异常只能作为 cleanup note，不能覆盖主体。"""
    from test.e2e import conftest as e2e_conftest

    async def fail_provision(_uid: str, _password: str):
        raise RuntimeError("provision failed")

    async def authenticated_cleanup(**_kwargs):
        return None

    monkeypatch.setattr(e2e_conftest, "CLEANUP_USERNAME", "e2e-goal-verifier")
    monkeypatch.setattr(e2e_conftest, "CLEANUP_PASSWORD", "test-password")
    monkeypatch.setattr(e2e_conftest, "ensure_static_test_admin_identity", fail_provision)
    monkeypatch.setattr(e2e_conftest, "_run_authenticated_e2e_cleanup", authenticated_cleanup)
    monkeypatch.setattr(e2e_conftest, "cleanup_static_test_user_resources", AsyncMock())
    primary = ValueError("body failed")

    await e2e_conftest._run_e2e_cleanup_session(
        final_teardown=True,
        primary_error=primary,
    )

    assert primary.__notes__ == ["E2E session cleanup also failed: RuntimeError"]


async def test_session_cleanup_attempts_authenticated_cleanup_after_provisioning_failure(monkeypatch):
    """provisioning 失败不得短路仍可能成功的认证清理。"""
    from test.e2e import conftest as e2e_conftest

    calls: list[str] = []

    async def fail_provision(_uid: str, _password: str):
        calls.append("provision")
        raise RuntimeError("provision failed")

    async def authenticated_cleanup(**_kwargs):
        calls.append("authenticated")

    async def cleanup_user(uid: str):
        calls.append(f"delete:{uid}")

    monkeypatch.setattr(e2e_conftest, "CLEANUP_USERNAME", "e2e-goal-verifier")
    monkeypatch.setattr(e2e_conftest, "CLEANUP_PASSWORD", "test-password")
    monkeypatch.setattr(e2e_conftest, "ensure_static_test_admin_identity", fail_provision)
    monkeypatch.setattr(e2e_conftest, "_run_authenticated_e2e_cleanup", authenticated_cleanup)
    monkeypatch.setattr(e2e_conftest, "cleanup_static_test_user_resources", cleanup_user)

    with pytest.raises(RuntimeError, match="provision failed"):
        await e2e_conftest._run_e2e_cleanup_session(
            final_teardown=True,
            primary_error=None,
        )

    assert calls == ["provision", "authenticated", "delete:e2e-goal-verifier"]


async def test_e2e_runtime_fixture_closes_postgres_when_redis_close_fails(monkeypatch):
    """Redis teardown 失败不得阻止 PostgreSQL engine 关闭。"""
    from test.e2e import conftest as e2e_conftest
    from yuxi.storage.postgres.manager import pg_manager
    from yuxi.storage.redis import manager as redis_manager

    monkeypatch.setattr(pg_manager, "async_engine", None)
    monkeypatch.setattr(pg_manager, "_initialized", False)
    close_postgres = AsyncMock()
    monkeypatch.setattr(pg_manager, "close", close_postgres)
    monkeypatch.setattr(
        redis_manager,
        "close_async_redis_client",
        AsyncMock(side_effect=RuntimeError("redis close failed")),
    )

    fixture = e2e_conftest.isolate_e2e_async_runtime.__wrapped__()
    await anext(fixture)
    monkeypatch.setattr(pg_manager, "async_engine", object())

    with pytest.raises(BaseException):
        await anext(fixture)

    close_postgres.assert_awaited_once()


async def test_agent_task_fixture_closes_postgres_when_schema_setup_fails(monkeypatch):
    """schema ensure 失败也必须进入最外层关闭 fence。"""
    from test.integration.api import test_agent_task_center as task_center
    from yuxi.storage.redis import manager as redis_manager

    monkeypatch.setattr(task_center.pg_manager, "initialize", Mock())
    monkeypatch.setattr(
        task_center.pg_manager,
        "ensure_business_schema",
        AsyncMock(side_effect=RuntimeError("schema failed")),
    )
    close = AsyncMock()
    monkeypatch.setattr(task_center.pg_manager, "close", close)
    monkeypatch.setattr(redis_manager, "close_async_redis_client", AsyncMock())
    monkeypatch.setattr(cleanup, "cleanup_static_test_user_resources", AsyncMock())

    fixture = task_center.db_session.__wrapped__()
    with pytest.raises(RuntimeError, match="schema failed"):
        await anext(fixture)

    close.assert_awaited_once()


async def test_remove_static_test_user_workspace_root_rejects_non_test_uid(tmp_path, monkeypatch):
    """共享 Workspace 根清理不能接受普通用户 UID。"""
    monkeypatch.setattr(cleanup, "get_user_data_dir", lambda: tmp_path)
    (tmp_path / "shared" / "normal-user" / "workspace").mkdir(parents=True)

    with pytest.raises(RuntimeError, match="test UID"):
        cleanup.remove_test_user_workspace_root("normal-user")

    assert (tmp_path / "shared" / "normal-user").is_dir()


async def test_remove_static_test_user_workspace_root_rejects_symlink(tmp_path, monkeypatch):
    """测试 UID 根或任意子项为 symlink 时不得递归删除。"""
    monkeypatch.setattr(cleanup, "get_user_data_dir", lambda: tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "shared" / "e2e-static"
    root.mkdir(parents=True)
    (root / "workspace-link").symlink_to(outside, target_is_directory=True)

    with pytest.raises(RuntimeError, match="symlink"):
        cleanup.remove_test_user_workspace_root("e2e-static")

    assert outside.is_dir()
    assert root.is_dir()


async def test_remove_static_test_user_workspace_root_removes_exact_uid_root(tmp_path, monkeypatch):
    """数据库已确认无 Project 后，只删除精确测试 UID 的共享根。"""
    monkeypatch.setattr(cleanup, "get_user_data_dir", lambda: tmp_path)
    target = tmp_path / "shared" / "e2e-static"
    other = tmp_path / "shared" / "e2e-other"
    (target / "workspace" / "projects").mkdir(parents=True)
    (other / "workspace").mkdir(parents=True)

    cleanup.remove_test_user_workspace_root("e2e-static")

    assert not target.exists()
    assert other.is_dir()


async def test_remove_static_test_user_workspace_root_accepts_exact_legacy_snapshot_uid(tmp_path, monkeypatch):
    """历史 snapshot E2E UID 可回收，但相似普通目录不能被匹配。"""
    monkeypatch.setattr(cleanup, "get_user_data_dir", lambda: tmp_path)
    target = tmp_path / "shared" / "snap_deadbeef"
    other = tmp_path / "shared" / "snap_deadbeef-not-test"
    (target / "workspace").mkdir(parents=True)
    (other / "workspace").mkdir(parents=True)

    cleanup.remove_test_user_workspace_root("snap_deadbeef")

    assert not target.exists()
    assert other.is_dir()


@pytest.mark.parametrize(
    "uid",
    [
        "codexe2e1788369721",
        "codex-e2e-admin",
        "teamloop_1788109111_6216",
        "teamreg_1788109314_28157",
        "teamfinal_1788109705_10195",
        "task-it-user",
    ],
)
async def test_static_test_uid_accepts_known_strict_fixture_identities(uid):
    """真实 E2E 与 integration 固定身份必须可由 canonical 清理器回收。"""
    assert cleanup._is_static_test_uid(uid)


@pytest.mark.parametrize(
    "uid",
    [
        "codexe2e",
        "codexe2e-user",
        "codex-e2e-admin-extra",
        "teamloop_1788109111",
        "teamreg_user_28157",
        "task-it-user-extra",
    ],
)
async def test_static_test_uid_rejects_similar_non_fixture_identities(uid):
    """相似普通身份不得因扩大测试清理范围而被误识别。"""
    assert not cleanup._is_static_test_uid(uid)


async def test_list_static_test_workspace_uids_includes_strict_orphan_fixture_roots(tmp_path, monkeypatch):
    """无用户行的 pytest 与 snapshot Workspace 也必须进入孤儿回收集合。"""
    shared = tmp_path / "shared"
    accepted = {
        "pytest-user-0394cec2-5de5-43e5-81a1-8cf07aa0bee5",
        "pytest-lock-user-173353c6-698d-4a93-888f-2c31319024e1",
        "pytest-project-recreate-440dd53e054d48bc9321a41dd1ec66a1",
        "snapshot-f4637949e2",
    }
    rejected = {
        "pytest-user-local",
        "pytest-project-recreate-not-hex",
        "snapshot-production",
        "normal-user",
    }
    for uid in accepted | rejected:
        (shared / uid / "workspace").mkdir(parents=True)
    monkeypatch.setattr(cleanup, "get_user_data_dir", lambda: tmp_path)

    assert set(cleanup.list_static_test_workspace_uids()) == accepted


async def test_cleanup_orphaned_static_workspace_roots_removes_strict_fixture_roots(tmp_path, monkeypatch):
    """孤儿清理必须能删除没有对应 User 的严格 integration Workspace。"""
    targets = {
        "pytest-user-0394cec2-5de5-43e5-81a1-8cf07aa0bee5",
        "snapshot-f4637949e2",
    }
    for uid in targets:
        (tmp_path / "shared" / uid / "workspace").mkdir(parents=True)
    monkeypatch.setattr(cleanup, "get_user_data_dir", lambda: tmp_path)
    monkeypatch.setattr(cleanup, "_assert_test_user_has_no_projects", AsyncMock())
    monkeypatch.setattr(cleanup, "_static_test_user_exists", AsyncMock(return_value=False))

    await cleanup.cleanup_orphaned_static_test_user_workspace_roots()

    assert all(not (tmp_path / "shared" / uid).exists() for uid in targets)


async def test_cleanup_orphaned_workspace_preserves_existing_static_user(tmp_path, monkeypatch):
    """仍有 User 行的测试身份即使无 Project 也不是孤儿。"""
    uid = "e2e-goal-verifier"
    target = tmp_path / "shared" / uid / "workspace"
    target.mkdir(parents=True)
    (target / "own-note.txt").write_text("keep", encoding="utf-8")
    monkeypatch.setattr(cleanup, "get_user_data_dir", lambda: tmp_path)
    monkeypatch.setattr(cleanup, "_assert_test_user_has_no_projects", AsyncMock())

    class Connection:
        async def fetchval(self, query: str, actual_uid: str):
            assert actual_uid == uid
            return 1 if "FROM users" in query else None

        async def close(self):
            return None

    monkeypatch.setattr(cleanup.asyncpg, "connect", AsyncMock(return_value=Connection()))

    await cleanup.cleanup_orphaned_static_test_user_workspace_roots()

    assert (target / "own-note.txt").read_text(encoding="utf-8") == "keep"


async def test_cleanup_orphaned_static_workspace_roots_requires_no_projects(tmp_path, monkeypatch):
    """用户行已删除的 E2E 根也必须先通过数据库 Project 空集确认。"""
    target = tmp_path / "shared" / "e2e-a-orphan"
    blocked = tmp_path / "shared" / "e2e-blocked"
    normal = tmp_path / "shared" / "normal-user"
    for path in (target, blocked, normal):
        (path / "workspace").mkdir(parents=True)
    monkeypatch.setattr(cleanup, "get_user_data_dir", lambda: tmp_path)

    async def assert_no_projects(uid: str):
        if uid == "e2e-blocked":
            raise RuntimeError("Projects remain")

    monkeypatch.setattr(cleanup, "_assert_test_user_has_no_projects", assert_no_projects)
    monkeypatch.setattr(cleanup, "_static_test_user_exists", AsyncMock(return_value=False))

    with pytest.raises(RuntimeError, match="Projects remain"):
        await cleanup.cleanup_orphaned_static_test_user_workspace_roots()

    assert not target.exists()
    assert blocked.is_dir()
    assert normal.is_dir()


async def test_cleanup_orphaned_static_workspace_roots_rejects_test_symlink(tmp_path, monkeypatch):
    """孤儿扫描发现测试 UID symlink 时必须失败并保留目标。"""
    shared = tmp_path / "shared"
    outside = tmp_path / "outside"
    shared.mkdir()
    outside.mkdir()
    (shared / "e2e-orphan").symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(cleanup, "get_user_data_dir", lambda: tmp_path)
    monkeypatch.setattr(cleanup, "_assert_test_user_has_no_projects", AsyncMock())
    monkeypatch.setattr(cleanup, "_static_test_user_exists", AsyncMock(return_value=False))

    with pytest.raises(RuntimeError, match="symlink"):
        await cleanup.cleanup_orphaned_static_test_user_workspace_roots()

    assert outside.is_dir()


async def test_cleanup_deletes_only_explicitly_owned_sandboxes_through_provisioner_api():
    """共享 provisioner 中未登记为本次测试所有的 Sandbox 必须保留。"""

    deleted_paths: list[str] = []
    sandbox_ids = {"sandbox-one", "sandbox-two", "shared-production"}
    authorization_headers: list[str | None] = []

    def handle_request(request: httpx.Request) -> httpx.Response:
        authorization_headers.append(request.headers.get("Authorization"))
        if request.method == "DELETE":
            deleted_paths.append(request.url.path)
            sandbox_ids.discard(request.url.path.rsplit("/", 1)[-1])
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(
            200,
            json={
                "sandboxes": [{"sandbox_id": sandbox_id} for sandbox_id in sorted(sandbox_ids)],
                "count": len(sandbox_ids),
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handle_request), base_url="http://provisioner"
    ) as client:
        await cleanup_provisioned_sandboxes(
            client,
            {"Authorization": "Bearer test-token"},
            {"sandbox-one", "sandbox-two"},
        )

    assert deleted_paths == ["/api/sandboxes/sandbox-one", "/api/sandboxes/sandbox-two"]
    assert sandbox_ids == {"shared-production"}
    assert authorization_headers == ["Bearer test-token"] * 4


async def test_cleanup_rejects_delete_that_does_not_remove_sandbox():
    """DELETE 即使返回 200，回读仍存在时也不得伪装清理成功。"""

    def handle_request(request: httpx.Request) -> httpx.Response:
        if request.method == "DELETE":
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(200, json={"sandboxes": [{"sandbox_id": "sandbox-stale"}], "count": 1})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handle_request), base_url="http://provisioner"
    ) as client:
        with pytest.raises(RuntimeError, match="left sandboxes behind: sandbox-stale"):
            await cleanup_provisioned_sandboxes(
                client,
                {"Authorization": "Bearer test-token"},
                {"sandbox-stale"},
            )


async def test_cleanup_rejects_invalid_provisioner_list_payload():
    """provisioner 未返回真实沙盒列表时必须拒绝伪装成清理成功。"""

    def handle_request(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"count": 0})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handle_request), base_url="http://provisioner"
    ) as client:
        with pytest.raises(RuntimeError, match="missing a sandboxes list"):
            await cleanup_provisioned_sandboxes(
                client,
                {"Authorization": "Bearer test-token"},
                {"sandbox-stale"},
            )


async def _patch_chat_cleanup_database(
    monkeypatch: pytest.MonkeyPatch,
    resources: dict[str, CleanupConversationResource],
) -> list[set[str]]:
    """打桩清理器的持久化发现、校验与物理删除。"""

    collected: list[set[str]] = []

    async def fake_list_resources(_owner_uid: str) -> dict[str, CleanupConversationResource]:
        return resources

    async def fake_validate(*_args, **_kwargs) -> None:
        return None

    async def fake_list_queued(_thread_ids: set[str]) -> list[str]:
        return []

    async def fake_delete_resources(workdirs, thread_ids: set[str], _project_ids: set[str]) -> None:
        for uid, workdir_path in workdirs:
            remove_test_workdir(uid, workdir_path)
        for thread_id in thread_ids:
            remove_e2e_thread_storage(thread_id)
        collected.append(set(thread_ids))

    monkeypatch.setattr("test.live_api_cleanup.list_test_conversation_resources", fake_list_resources)
    monkeypatch.setattr("test.live_api_cleanup.validate_test_workdirs_exclusive", fake_validate)
    monkeypatch.setattr("test.live_api_cleanup.validate_test_runs_terminal", fake_validate)
    monkeypatch.setattr("test.live_api_cleanup.list_test_queued_request_ids", fake_list_queued)
    monkeypatch.setattr("test.live_api_cleanup.delete_test_conversation_resources", fake_delete_resources)
    monkeypatch.setattr("test.live_api_cleanup.delete_orphaned_test_projects", fake_validate)
    return collected


async def test_cleanup_deletes_pytest_evaluation_resources_and_knowledge_databases():
    """只删除 pytest 前缀资源，并使用知识库真实返回的 kb_id。"""

    deleted_paths: list[str] = []
    responses: dict[str, dict[str, Any]] = {
        "/api/knowledge/databases": {
            "databases": [
                {"kb_id": "kb_test", "name": "Pytest knowledge base"},
                {"kb_id": "kb_legacy", "name": "py_test_legacy"},
                {"kb_id": "kb_prod", "name": "Production knowledge base"},
            ]
        },
        "/api/evaluation/databases/kb_test/runs": {"data": [{"run_id": "run_test", "name": "PYTEST evaluation"}]},
        "/api/evaluation/databases/kb_test/datasets": {"data": [{"dataset_id": "dataset_test", "name": "pytest plan"}]},
        "/api/evaluation/databases/kb_legacy/runs": {"data": []},
        "/api/evaluation/databases/kb_legacy/datasets": {"data": []},
        "/api/evaluation/databases/kb_prod/runs": {"data": [{"run_id": "run_prod", "name": "Production evaluation"}]},
        "/api/evaluation/databases/kb_prod/datasets": {
            "data": [
                {"dataset_id": "dataset_shared_test", "name": "Pytest shared plan"},
                {"dataset_id": "dataset_prod", "name": "Production plan"},
            ]
        },
    }

    def handle_request(request: httpx.Request) -> httpx.Response:
        """返回清理 API 的最小真实 HTTP 响应。"""

        if request.method == "DELETE":
            deleted_paths.append(request.url.path)
            return httpx.Response(200, json={})
        return httpx.Response(200, json=responses[request.url.path])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle_request), base_url="http://test") as client:
        await cleanup_pytest_knowledge_resources(client, {"Authorization": "test"})

    assert set(deleted_paths) == {
        "/api/evaluation/databases/kb_test/runs/run_test",
        "/api/evaluation/datasets/dataset_test",
        "/api/evaluation/datasets/dataset_shared_test",
        "/api/knowledge/databases/kb_test",
        "/api/knowledge/databases/kb_legacy",
    }


async def test_cleanup_rejects_knowledge_list_error_payload():
    """知识库列表以 200 返回内部错误时，清理必须显式失败。"""

    def handle_request(request: httpx.Request) -> httpx.Response:
        """模拟知识库列表路由当前的 200 错误响应。"""

        return httpx.Response(200, json={"message": "获取数据库列表失败", "databases": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle_request), base_url="http://test") as client:
        with pytest.raises(RuntimeError, match="获取数据库列表失败"):
            await cleanup_pytest_knowledge_resources(client, {"Authorization": "test"})


async def test_cleanup_deletes_e2e_threads_before_temporary_agents(tmp_path, monkeypatch):
    """只删除 E2E 标记的对话和智能体，并允许资源已经不存在。"""

    deleted_paths: list[str] = []
    deleted_row_threads = await _patch_chat_cleanup_database(
        monkeypatch,
        {
            thread_id: CleanupConversationResource(
                conversation_id=index,
                project_id=f"project-{index}",
                thread_id=thread_id,
                uid="test-user",
                status="active",
                workdir_path=None,
            )
            for index, thread_id in enumerate(("thread-viewer", "thread-marked"), start=1)
        },
    )
    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path / "threads"))
    (tmp_path / "threads" / "thread-viewer").mkdir(parents=True)
    (tmp_path / "threads" / "thread-marked").mkdir(parents=True)
    responses: dict[str, object] = {
        "/api/chat/threads": [
            {
                "id": "thread-viewer",
                "title": "viewer-fs-e2e-deadbeef",
                "agent_id": "default-chatbot",
                "metadata": {"_yuxi_e2e": True, "test": "viewer-fs-e2e"},
            },
            {
                "id": "thread-user",
                "title": "用户自己的对话",
                "agent_id": "default-chatbot",
                "metadata": {},
            },
            {
                "id": "thread-marked",
                "title": "未使用固定前缀",
                "agent_id": "e2e-main-deadbeef",
                "metadata": {"_yuxi_e2e": True, "marker": "YUXI_SUBAGENT_STREAM_E2E_deadbeef"},
            },
        ],
        "/api/agent": {
            "agents": [
                {"slug": "e2e-main-deadbeef", "created_by": "test-user"},
                {"slug": "e2e-main-other-user", "created_by": "other-user"},
                {"slug": "default-chatbot", "created_by": "test-user"},
            ]
        },
    }

    def handle_request(request: httpx.Request) -> httpx.Response:
        """返回对话与智能体清理 API 的最小响应。"""

        if request.method == "DELETE":
            deleted_paths.append(request.url.path)
            return httpx.Response(200, json={})
        return httpx.Response(200, json=responses[request.url.path])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle_request), base_url="http://test") as client:
        await cleanup_e2e_chat_resources(
            client,
            {"Authorization": "test"},
            owner_uid="test-user",
        )

    assert deleted_paths == [
        "/api/chat/thread/thread-viewer",
        "/api/chat/thread/thread-marked",
        "/api/agent/e2e-main-deadbeef",
    ]
    assert deleted_row_threads == [{"thread-viewer", "thread-marked"}]
    assert not (tmp_path / "threads" / "thread-viewer").exists()
    assert not (tmp_path / "threads" / "thread-marked").exists()


async def test_cleanup_only_exempts_projects_whose_managed_workdir_is_deleted(tmp_path, monkeypatch):
    """目标 linked Project 共享路径时仍必须被 Workdir 冲突校验视为保留 Owner。"""

    workdir_path = "projects/project-managed"
    managed_dir = tmp_path / workdir_path
    managed_dir.mkdir(parents=True)
    monkeypatch.setattr("test.live_api_cleanup.user_workdir_host_dir", lambda _uid, _path: managed_dir)
    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path / "threads"))
    resources = {
        "thread-managed": CleanupConversationResource(
            1,
            "project-managed",
            "thread-managed",
            "test-user",
            "active",
            workdir_path,
        ),
        "thread-linked": CleanupConversationResource(
            2,
            "project-linked",
            "thread-linked",
            "test-user",
            "active",
            None,
        ),
    }
    await _patch_chat_cleanup_database(monkeypatch, resources)
    validated_project_ids: list[set[str]] = []

    async def capture_validation(_workdirs, project_ids: set[str]):
        validated_project_ids.append(set(project_ids))

    monkeypatch.setattr("test.live_api_cleanup.validate_test_workdirs_exclusive", capture_validation)

    def handle_request(request: httpx.Request) -> httpx.Response:
        if request.method == "DELETE":
            return httpx.Response(200, json={})
        if request.url.path == "/api/chat/threads":
            return httpx.Response(
                200,
                json=[{"id": thread_id, "metadata": {"_yuxi_test": True}} for thread_id in resources],
            )
        if request.url.path == "/api/agent":
            return httpx.Response(200, json={"agents": []})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle_request), base_url="http://test") as client:
        await cleanup_e2e_chat_resources(client, {"Authorization": "test"}, owner_uid="test-user")

    assert validated_project_ids == [{"project-managed"}]


async def test_cleanup_paginates_active_threads(tmp_path, monkeypatch):
    """活动线程超过单页上限时仍需清理后续页面的 E2E 对话。"""

    deleted_paths: list[str] = []
    deleted_row_threads = await _patch_chat_cleanup_database(
        monkeypatch,
        {
            "thread-page-2": CleanupConversationResource(
                conversation_id=1,
                project_id="project-page-2",
                thread_id="thread-page-2",
                uid="test-user",
                status="active",
                workdir_path=None,
            )
        },
    )
    offsets: list[str] = []
    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path / "threads"))

    def handle_request(request: httpx.Request) -> httpx.Response:
        """模拟分两页返回线程的清理 API。"""

        if request.method == "DELETE":
            deleted_paths.append(request.url.path)
            return httpx.Response(200, json={})
        if request.url.path == "/api/chat/threads":
            offset = request.url.params.get("offset") or "0"
            offsets.append(offset)
            if offset == "0":
                return httpx.Response(
                    200,
                    json=[{"id": f"thread-{index}", "is_pinned": False} for index in range(500)],
                )
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "thread-page-2",
                        "title": "任意标题",
                        "metadata": {"_yuxi_e2e": True, "test": "viewer-fs-e2e"},
                    }
                ],
            )
        if request.url.path == "/api/agent":
            return httpx.Response(200, json={"agents": []})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle_request), base_url="http://test") as client:
        await cleanup_e2e_chat_resources(
            client,
            {"Authorization": "test"},
            owner_uid="test-user",
        )

    assert offsets == ["0", "500"]
    assert deleted_paths == ["/api/chat/thread/thread-page-2"]
    assert deleted_row_threads == [{"thread-page-2"}]


async def test_cleanup_removes_deleted_and_subagent_thread_storage(tmp_path, monkeypatch):
    """已软删除和 subagent 状态的线程也必须回收本地沙盒目录。"""

    deleted_paths: list[str] = []
    deleted_row_threads = await _patch_chat_cleanup_database(
        monkeypatch,
        {
            "thread-deleted": CleanupConversationResource(
                conversation_id=1,
                project_id="project-deleted",
                thread_id="thread-deleted",
                uid="test-user",
                status="deleted",
                workdir_path=None,
            ),
            "thread-child": CleanupConversationResource(
                conversation_id=2,
                project_id="project-child",
                thread_id="thread-child",
                uid="test-user",
                status="subagent",
                workdir_path=None,
            ),
        },
    )
    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path / "threads"))
    for thread_id in ("thread-deleted", "thread-child"):
        (tmp_path / "threads" / thread_id).mkdir(parents=True)

    def handle_request(request: httpx.Request) -> httpx.Response:
        """模拟无 active 线程但存在持久化线程的清理 API。"""

        if request.method == "DELETE":
            deleted_paths.append(request.url.path)
            return httpx.Response(200, json={})
        if request.url.path == "/api/chat/threads":
            return httpx.Response(200, json=[])
        if request.url.path == "/api/agent":
            return httpx.Response(200, json={"agents": []})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle_request), base_url="http://test") as client:
        await cleanup_e2e_chat_resources(
            client,
            {"Authorization": "test"},
            owner_uid="test-user",
        )

    assert deleted_paths == ["/api/chat/thread/thread-child"]
    assert deleted_row_threads == [{"thread-child", "thread-deleted"}]
    assert not (tmp_path / "threads" / "thread-deleted").exists()
    assert not (tmp_path / "threads" / "thread-child").exists()


async def test_remove_e2e_thread_storage_rejects_symlink(tmp_path, monkeypatch):
    """沙盒目录是符号链接时必须拒绝删除，避免解析后误删用户目录。"""

    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path / "threads"))
    threads_root = tmp_path / "threads"
    threads_root.mkdir()
    user_dir = threads_root / "user-data"
    user_dir.mkdir()
    (threads_root / "thread-e2e").symlink_to(user_dir, target_is_directory=True)

    with pytest.raises(RuntimeError, match="symlink"):
        remove_e2e_thread_storage("thread-e2e")

    assert user_dir.exists()


async def test_test_resource_names_use_one_visible_prefix():
    title = make_test_conversation_title("viewer 文件系统")
    metadata = make_test_conversation_metadata("viewer-filesystem")

    assert title.startswith(TEST_CONVERSATION_TITLE_PREFIX)
    assert metadata["_yuxi_test"] is True
    assert make_test_resource_id("agent-call").startswith("YUXI_TEST_")


async def test_legacy_title_matching_is_exact_and_does_not_capture_user_titles():
    """历史兼容只接受仓库曾生成的固定格式，不按宽泛前缀误删。"""

    assert is_test_conversation_title("viewer-deadbeef")
    assert is_test_conversation_title("pytest-channel-0123abcd")
    assert is_test_conversation_title("pytest-queue-0123abcd")
    assert is_test_conversation_title("snapshot-deadbeef")
    assert not is_test_conversation_title("viewer-notes")
    assert not is_test_conversation_title("viewer-deadbeef-personal")


async def test_cleanup_discovery_failure_has_no_destructive_side_effect(tmp_path, monkeypatch):
    """数据库无法确认归属时，不得先软删会话或删除临时智能体。"""

    destructive_paths: list[str] = []
    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path / "threads"))

    async def fail_discovery(_owner_uid: str):
        raise OSError("postgres unavailable")

    monkeypatch.setattr("test.live_api_cleanup.list_test_conversation_resources", fail_discovery)

    def handle_request(request: httpx.Request) -> httpx.Response:
        if request.method == "DELETE":
            destructive_paths.append(request.url.path)
            return httpx.Response(200, json={})
        if request.url.path == "/api/chat/threads":
            return httpx.Response(
                200,
                json=[{"id": "thread-marked", "metadata": {"_yuxi_test": True}}],
            )
        if request.url.path == "/api/agent":
            raise AssertionError("agent cleanup must not run after discovery failure")
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle_request), base_url="http://test") as client:
        with pytest.raises(RuntimeError, match="Failed to list persisted"):
            await cleanup_e2e_chat_resources(client, {"Authorization": "test"}, owner_uid="test-user")

    assert destructive_paths == []


async def test_cleanup_guard_failure_has_no_destructive_side_effect(tmp_path, monkeypatch):
    """Run/Workdir guard 拒绝时，对话、文件、历史和智能体均保持不变。"""

    destructive_paths: list[str] = []
    legacy_dir = tmp_path / "threads" / "thread-marked"
    legacy_dir.mkdir(parents=True)
    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path / "threads"))
    resources = {
        "thread-marked": CleanupConversationResource(
            1,
            "project-marked",
            "thread-marked",
            "test-user",
            "active",
            None,
        )
    }

    async def fake_list_resources(_owner_uid: str):
        return resources

    async def fake_workdir_guard(*_args):
        return None

    async def fail_run_guard(_thread_ids: set[str]):
        raise RuntimeError("test Run is not terminal")

    monkeypatch.setattr("test.live_api_cleanup.list_test_conversation_resources", fake_list_resources)
    monkeypatch.setattr("test.live_api_cleanup.validate_test_workdirs_exclusive", fake_workdir_guard)
    monkeypatch.setattr("test.live_api_cleanup.validate_test_runs_terminal", fail_run_guard)

    def handle_request(request: httpx.Request) -> httpx.Response:
        if request.method == "DELETE":
            destructive_paths.append(request.url.path)
            return httpx.Response(200, json={})
        if request.url.path == "/api/chat/threads":
            return httpx.Response(200, json=[{"id": "thread-marked", "metadata": {"_yuxi_test": True}}])
        if request.url.path == "/api/agent":
            raise AssertionError("agent cleanup must not run after guard failure")
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle_request), base_url="http://test") as client:
        with pytest.raises(RuntimeError, match="not terminal"):
            await cleanup_e2e_chat_resources(client, {"Authorization": "test"}, owner_uid="test-user")

    assert destructive_paths == []
    assert legacy_dir.exists()


async def test_cleanup_stops_when_cancelled_request_remains_queued(tmp_path, monkeypatch):
    """取消 API 未真正收敛 queued 请求时，不得继续删除会话、文件或历史。"""

    destructive_paths: list[str] = []
    legacy_dir = tmp_path / "threads" / "thread-marked"
    legacy_dir.mkdir(parents=True)
    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path / "threads"))

    async def fake_list_resources(_owner_uid: str):
        return {
            "thread-marked": CleanupConversationResource(
                1,
                "project-marked",
                "thread-marked",
                "test-user",
                "active",
                None,
            )
        }

    async def fake_validate(*_args):
        return None

    async def still_queued(_thread_ids: set[str]) -> list[str]:
        return ["YUXI_TEST_queued_request"]

    monkeypatch.setattr("test.live_api_cleanup.list_test_conversation_resources", fake_list_resources)
    monkeypatch.setattr("test.live_api_cleanup.validate_test_workdirs_exclusive", fake_validate)
    monkeypatch.setattr("test.live_api_cleanup.validate_test_runs_terminal", fake_validate)
    monkeypatch.setattr("test.live_api_cleanup.list_test_queued_request_ids", still_queued)

    def handle_request(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/chat/threads":
            return httpx.Response(200, json=[{"id": "thread-marked", "metadata": {"_yuxi_test": True}}])
        if request.method == "POST" and request.url.path.endswith("/cancel"):
            return httpx.Response(200, json={"status": "cancelled"})
        if request.method == "DELETE":
            destructive_paths.append(request.url.path)
            return httpx.Response(200, json={})
        if request.url.path == "/api/agent":
            raise AssertionError("agent cleanup must not run while a request remains queued")
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle_request), base_url="http://test") as client:
        with pytest.raises(RuntimeError, match="left queued requests behind"):
            await cleanup_e2e_chat_resources(client, {"Authorization": "test"}, owner_uid="test-user")

    assert destructive_paths == []
    assert legacy_dir.exists()


async def test_remove_test_workdir_stays_inside_project_boundary(tmp_path, monkeypatch):
    workdir_path = "projects/11111111-1111-4111-8111-111111111111"
    project = tmp_path / workdir_path
    project.mkdir(parents=True)
    (project / "artifact.txt").write_text("test", encoding="utf-8")
    monkeypatch.setattr("test.live_api_cleanup.user_workdir_host_dir", lambda _uid, _path: project)

    remove_test_workdir("test-user", workdir_path)

    assert not project.exists()


async def test_remove_test_workdir_rejects_non_project_path(tmp_path, monkeypatch):
    outside = tmp_path / "agents"
    outside.mkdir()
    monkeypatch.setattr("test.live_api_cleanup.user_workdir_host_dir", lambda _uid, _path: outside)

    with pytest.raises(RuntimeError, match="non-project Workdir"):
        remove_test_workdir("test-user", "agents/skills")

    assert outside.exists()


async def test_remove_test_workdir_rejects_symlink(tmp_path, monkeypatch):
    """Project Workdir 是符号链接时必须拒绝，不能跟随到用户目录。"""

    target = tmp_path / "user-files"
    target.mkdir()
    workdir_path = "projects/22222222-2222-4222-8222-222222222222"
    symlink = tmp_path / workdir_path
    symlink.parent.mkdir()
    symlink.symlink_to(target, target_is_directory=True)
    monkeypatch.setattr("test.live_api_cleanup.user_workdir_host_dir", lambda _uid, _path: symlink)

    with pytest.raises(RuntimeError, match="symlink Workdir"):
        remove_test_workdir("test-user", workdir_path)

    assert target.exists()


async def test_remove_test_workdir_is_idempotent_when_directory_is_gone(tmp_path, monkeypatch):
    workdir_path = "projects/33333333-3333-4333-8333-333333333333"
    missing = tmp_path / workdir_path
    monkeypatch.setattr("test.live_api_cleanup.user_workdir_host_dir", lambda _uid, _path: missing)

    remove_test_workdir("test-user", workdir_path)

    assert not missing.exists()


async def test_is_e2e_thread_recognizes_marker_or_e2e_agent_prefix():
    from test.live_api_cleanup import _is_e2e_thread

    marked = {"id": "t1", "agent_id": "default-chatbot", "metadata": {"_yuxi_e2e": True, "test": "viewer-fs-e2e"}}
    agent_prefix = {"id": "invocation_x", "agent_id": "e2e-agent-call-deadbeef"}
    deterministic_agent = {"id": "invocation_y", "agent_id": "ci-deterministic-deadbeef"}
    linked_agent = {"id": "invocation_z", "agent_id": "e2e-linked-agent-deadbeef"}
    unified = {"id": "t3", "title": f"{TEST_CONVERSATION_TITLE_PREFIX}viewer_deadbeef", "metadata": {}}
    explicit = {"id": "t4", "metadata": {"_yuxi_test": True}}
    plain = {"id": "t2", "agent_id": "default-chatbot"}

    assert _is_e2e_thread(marked)
    assert _is_e2e_thread(agent_prefix)
    assert _is_e2e_thread(deterministic_agent)
    assert _is_e2e_thread(linked_agent)
    assert _is_e2e_thread(unified)
    assert _is_e2e_thread(explicit)
    assert not _is_e2e_thread(plain)
    assert not _is_e2e_thread("not-a-dict")
