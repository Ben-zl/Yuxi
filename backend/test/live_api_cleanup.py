from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from pathlib import PurePosixPath

import asyncpg
import httpx
from yuxi.workspace.paths import normalize_workdir_path, user_workdir_host_dir
from yuxi.agents.backends.sandbox.provider import sandbox_id_for_thread
from yuxi.config import get_user_data_dir
from yuxi.storage.postgres.models_business import AGENT_RUN_TERMINAL_STATUSES

TEST_RESOURCE_PREFIX = "YUXI_TEST_"
TEST_CONVERSATION_TITLE_PREFIX = f"{TEST_RESOURCE_PREFIX}CONVERSATION_"
PYTEST_RESOURCE_PREFIXES = ("pytest", "py_test")
LEGACY_TEST_CONVERSATION_TITLE_PATTERNS = (
    re.compile(
        r"^(?:agent-async-e2e|agent-steer-e2e|attachment-state-e2e|attachment-workdir-e2e|"
        r"chat-router-test|deterministic-e2e|ocr-config-e2e|personal-skill-e2e|"
        r"pytest-channel|pytest-queue|read-file-e2e|skill-artifact-admin|skill-artifact-user|snapshot|"
        r"viewer|viewer-security-test)-[0-9a-f]{8}$"
    ),
)
E2E_THREAD_TEST_MARKERS = frozenset(
    {
        "agent-async-e2e",
        "agent-sync-e2e",
        "memory-recall-e2e",
        "agent-steer-e2e",
        "attachment-state-e2e",
        "ocr-config-e2e",
        "personal-skill-e2e",
        "read-file-e2e",
        "subagent-stream-e2e",
        "viewer-fs-e2e",
    }
)
E2E_AGENT_SLUG_PREFIXES = (
    "ci-deterministic-",
    "e2e-agent-call-",
    "e2e-async-agent-",
    "e2e-linked-agent-",
    "e2e-main-",
    "e2e-personal-skill-",
    "e2e-read-file-",
    "e2e-steer-agent-",
    "e2e-subagent-",
    "e2e-sync-agent-",
    "pytest-personal-agent-",
)
SAFE_THREAD_ID = re.compile(r"^[A-Za-z0-9_-]+$")
SAFE_STATIC_TEST_UID = re.compile(r"^e2e[-_][A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
LEGACY_SNAPSHOT_TEST_UID = re.compile(r"^snap_[0-9a-f]{8}$")
STRICT_STATIC_TEST_UID_PATTERNS = (
    re.compile(r"^codexe2e[0-9]{10}$"),
    re.compile(r"^codex-e2e-admin$"),
    re.compile(r"^team(?:loop|reg|final)_[0-9]{10}_[0-9]{1,5}$"),
    re.compile(r"^task-it-user$"),
)
STRICT_ORPHAN_TEST_WORKSPACE_UID_PATTERNS = (
    re.compile(r"^pytest-(?:user|lock-user)-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$"),
    re.compile(r"^pytest-project-recreate-[0-9a-f]{32}$"),
    re.compile(r"^snapshot-[0-9a-f]{8,10}$"),
)
SNAPSHOT_TEST_DEPARTMENT_NAME = re.compile(r"^snapshot-[0-9a-f]{8}$")


@dataclass(frozen=True, slots=True)
class CleanupConversationResource:
    """描述一个待清理的测试 Conversation 及其 Project Workdir。"""

    conversation_id: int
    project_id: str
    thread_id: str
    uid: str
    status: str
    workdir_path: str | None


def make_test_conversation_title(label: str) -> str:
    """生成带统一前缀且适合展示的测试 Conversation 标题。"""

    normalized = re.sub(r"[^A-Za-z0-9_-]+", "-", str(label)).strip("-_") or "case"
    return f"{TEST_CONVERSATION_TITLE_PREFIX}{normalized[:120]}_{uuid.uuid4().hex[:12]}"


def make_test_resource_id(label: str) -> str:
    """生成用于测试请求等资源的统一可检索标识。"""

    normalized = re.sub(r"[^A-Za-z0-9_-]+", "-", str(label)).strip("-_") or "resource"
    return f"{TEST_RESOURCE_PREFIX}{normalized[:40]}_{uuid.uuid4().hex}"


def make_test_conversation_metadata(test_name: str, *, e2e: bool = False, **extra: object) -> dict[str, object]:
    """生成带测试资源标记的 Conversation metadata。"""

    metadata: dict[str, object] = {"_yuxi_test": True, "test": test_name}
    if e2e:
        metadata["_yuxi_e2e"] = True
    metadata.update(extra)
    return metadata


async def _list_provisioned_sandbox_ids(
    client: httpx.AsyncClient,
    headers: dict[str, str],
) -> set[str]:
    """从 provisioner 管理 API 回读当前沙盒标识。"""

    response = await client.get("/api/sandboxes", headers=headers)
    if response.status_code != 200:
        raise RuntimeError(f"Failed to list provisioned sandboxes for cleanup: {response.text}")

    payload = response.json()
    sandboxes = payload.get("sandboxes") if isinstance(payload, dict) else None
    if not isinstance(sandboxes, list):
        raise RuntimeError("Provisioner cleanup response is missing a sandboxes list")

    sandbox_ids: set[str] = set()
    for sandbox in sandboxes:
        sandbox_id = sandbox.get("sandbox_id") if isinstance(sandbox, dict) else None
        if not isinstance(sandbox_id, str) or not sandbox_id:
            raise RuntimeError("Provisioner cleanup entry is missing sandbox_id")
        sandbox_ids.add(sandbox_id)
    return sandbox_ids


async def cleanup_provisioned_sandboxes(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    sandbox_ids: set[str],
) -> None:
    """只删除已由测试数据库事实证明归属本次测试的 Sandbox。"""

    if not sandbox_ids:
        return
    provisioned_ids = await _list_provisioned_sandbox_ids(client, headers)
    target_ids = sandbox_ids & provisioned_ids
    failures: list[str] = []
    for sandbox_id in sorted(target_ids):
        delete_response = await client.delete(f"/api/sandboxes/{sandbox_id}", headers=headers)
        if delete_response.status_code not in {200, 404}:
            failures.append(f"Failed to delete provisioned sandbox {sandbox_id}: {delete_response.text}")

    remaining_ids = target_ids & await _list_provisioned_sandbox_ids(client, headers)
    if remaining_ids:
        failures.append(f"Provisioner cleanup left sandboxes behind: {', '.join(sorted(remaining_ids))}")

    if failures:
        raise RuntimeError("; ".join(failures))


async def list_test_sandbox_ids(owner_uid: str, *, include_all_owned: bool = False) -> set[str]:
    """从测试 Run 的持久化 runtime scope 推导其唯一 Sandbox 标识。"""

    resources = await list_test_conversation_resources(owner_uid, include_all_owned=include_all_owned)
    if not resources:
        return set()
    conn = await asyncpg.connect(_postgres_dsn())
    try:
        rows = await conn.fetch(
            "SELECT DISTINCT uid, runtime_scope_id FROM agent_runs "
            "WHERE conversation_thread_id = ANY($1::text[]) "
            "AND runtime_scope_id IS NOT NULL AND runtime_scope_id <> ''",
            sorted(resources),
        )
    finally:
        await conn.close()
    return {sandbox_id_for_thread(str(row["runtime_scope_id"]), uid=str(row["uid"])) for row in rows}


def _postgres_dsn() -> str:
    """返回测试环境 PostgreSQL DSN（去掉 SQLAlchemy 驱动前缀）。"""
    return os.getenv("POSTGRES_URL", "postgresql+asyncpg://postgres:postgres@postgres:5432/yuxi").replace(
        "+asyncpg", ""
    )


def _is_pytest_resource(name: object) -> bool:
    """判断资源名称是否属于 pytest 约定的测试数据。"""

    return isinstance(name, str) and name.casefold().startswith(PYTEST_RESOURCE_PREFIXES)


def _has_prefix(value: object, prefixes: tuple[str, ...]) -> bool:
    """判断字符串是否以任一约定前缀开头。"""

    return isinstance(value, str) and value.startswith(prefixes)


def _parse_metadata(value: object) -> dict[str, object]:
    """把数据库或 HTTP 返回的 metadata 统一解析为字典。"""

    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def is_test_conversation_title(title: object) -> bool:
    """识别统一前缀及当前仓库历史测试标题。"""

    if not isinstance(title, str):
        return False
    if title.startswith(TEST_CONVERSATION_TITLE_PREFIX):
        return True
    return any(pattern.fullmatch(title) for pattern in LEGACY_TEST_CONVERSATION_TITLE_PATTERNS)


def _is_test_thread(thread: object) -> bool:
    """识别测试线程的显式标记、统一标题或测试智能体前缀。

    智能体前缀用于兼容旧 agent-invocation 自动创建的会话，并与 agent 清理
    共享同一 E2E_AGENT_SLUG_PREFIXES 信任边界；统一标题只在专用测试账号的
    清理范围内使用。
    """

    if not isinstance(thread, dict):
        return False

    metadata = _parse_metadata(thread.get("metadata") or thread.get("extra_metadata"))
    if metadata.get("_yuxi_test") is True:
        return True
    if metadata.get("_yuxi_e2e") is True and (
        metadata.get("test") in E2E_THREAD_TEST_MARKERS
        or _has_prefix(metadata.get("marker"), ("YUXI_SUBAGENT_STREAM_E2E_",))
    ):
        return True
    if is_test_conversation_title(thread.get("title")):
        return True
    return _has_prefix(thread.get("agent_id") or thread.get("agent_slug") or "", E2E_AGENT_SLUG_PREFIXES)


def _is_e2e_thread(thread: object) -> bool:
    """兼容旧测试调用方的 E2E 线程识别入口。"""

    return _is_test_thread(thread)


def _is_e2e_agent(agent: object, owner_uid: str) -> bool:
    """判断智能体是否是当前清理用户创建的 E2E 临时智能体。"""

    if not isinstance(agent, dict):
        return False
    slug = agent.get("slug") or agent.get("agent_id") or agent.get("id")
    return _has_prefix(slug, E2E_AGENT_SLUG_PREFIXES) and str(agent.get("created_by") or "") == owner_uid


def _resolve_e2e_thread_storage(thread_id: str) -> Path:
    """校验并返回测试线程的独立沙盒目录，不触碰用户共享工作区。"""

    if not SAFE_THREAD_ID.fullmatch(thread_id):
        raise RuntimeError(f"E2E conversation cleanup received an unsafe thread id: {thread_id!r}")
    if thread_id == "shared":
        raise RuntimeError("E2E conversation cleanup refuses to target the shared workspace")

    threads_root = get_user_data_dir().resolve()
    raw_thread_root = threads_root / thread_id
    if raw_thread_root.is_symlink():
        raise RuntimeError(f"E2E conversation cleanup refuses to remove symlink: {raw_thread_root}")

    thread_root = raw_thread_root.resolve()
    if thread_root.parent != threads_root:
        raise RuntimeError(f"E2E conversation cleanup path escaped thread root: {thread_root}")
    return thread_root


def remove_e2e_thread_storage(thread_id: str) -> None:
    """删除测试线程的独立沙盒目录。"""

    thread_root = _resolve_e2e_thread_storage(thread_id)
    if thread_root.is_dir():
        shutil.rmtree(thread_root)


def _resolve_test_workdir(uid: str, workdir_path: str) -> Path | None:
    """校验并返回 UserWorkspace 内的 Project Workdir。"""
    try:
        normalized = normalize_workdir_path(workdir_path)
    except ValueError as exc:
        raise RuntimeError(f"Test conversation cleanup refuses invalid Workdir: {workdir_path!r}") from exc
    parts = PurePosixPath(normalized).parts
    if len(parts) < 2 or parts[0] != "projects":
        raise RuntimeError(f"Test conversation cleanup refuses non-project Workdir: {workdir_path!r}")

    try:
        workdir = user_workdir_host_dir(uid, normalized)
    except FileNotFoundError:
        return None
    except ValueError as exc:
        if str(exc) == "workdir_path does not reference an existing directory":
            return None
        raise RuntimeError(f"Test conversation cleanup refuses invalid Workdir: {workdir_path!r}") from exc
    if not workdir.exists() and not workdir.is_symlink():
        return None
    if workdir.is_symlink():
        raise RuntimeError(f"Test conversation cleanup refuses symlink Workdir: {workdir}")
    return workdir


def remove_test_workdir(uid: str, workdir_path: str) -> None:
    """在 UserWorkspace 边界内删除测试 Conversation 的 Project Workdir。"""

    workdir = _resolve_test_workdir(uid, workdir_path)
    if workdir is None:
        return

    shutil.rmtree(workdir)
    if workdir.exists() or workdir.is_symlink():
        raise RuntimeError(f"Test conversation cleanup left Workdir behind: {workdir}")


def _require_static_test_uid(uid: str) -> str:
    """只允许清理显式 E2E 前缀的专用测试身份。"""
    value = str(uid or "")
    if not _is_static_test_uid(value):
        raise RuntimeError(f"Static test cleanup requires an explicit test UID: {value!r}")
    return value


def _is_static_test_uid(uid: str) -> bool:
    """识别当前 E2E UID 及历史 snapshot 测试 UID。"""
    return (
        SAFE_STATIC_TEST_UID.fullmatch(uid) is not None
        or LEGACY_SNAPSHOT_TEST_UID.fullmatch(uid) is not None
        or any(pattern.fullmatch(uid) is not None for pattern in STRICT_STATIC_TEST_UID_PATTERNS)
    )


def is_static_test_uid(uid: str) -> bool:
    """公开测试身份判断，供会话 provisioning 与 final cleanup 共用。"""
    return _is_static_test_uid(uid)


async def ensure_static_test_admin_identity(uid: str, password: str) -> None:
    """幂等创建严格命名的临时 E2E 超级管理员。"""
    uid = _require_static_test_uid(uid)
    if not password:
        raise RuntimeError("Static E2E admin password cannot be empty")

    from yuxi.utils.auth_utils import AuthUtils

    conn = await asyncpg.connect(_postgres_dsn())
    try:
        async with conn.transaction():
            department_id = await conn.fetchval("SELECT id FROM departments ORDER BY id LIMIT 1")
            await conn.execute(
                """
                INSERT INTO users (
                    username, uid, password_hash, role, department_id,
                    login_failed_count, is_deleted, deleted_at, created_at
                )
                VALUES ($1, $1, $2, 'superadmin', $3, 0, 0, NULL, NOW())
                ON CONFLICT (uid) DO UPDATE SET
                    username = EXCLUDED.username,
                    password_hash = EXCLUDED.password_hash,
                    role = 'superadmin',
                    department_id = COALESCE(users.department_id, EXCLUDED.department_id),
                    login_failed_count = 0,
                    last_failed_login = NULL,
                    login_locked_until = NULL,
                    is_deleted = 0,
                    deleted_at = NULL,
                    created_at = COALESCE(users.created_at, EXCLUDED.created_at)
                """,
                uid,
                AuthUtils.hash_password(password),
                department_id,
            )
    finally:
        await conn.close()


def _is_orphan_test_workspace_uid(uid: str) -> bool:
    """识别无用户行但由 integration fixture 创建的严格 Workspace 根。"""
    return _is_static_test_uid(uid) or any(
        pattern.fullmatch(uid) is not None for pattern in STRICT_ORPHAN_TEST_WORKSPACE_UID_PATTERNS
    )


def remove_test_user_workspace_root(uid: str) -> None:
    """删除已确认无 Project 的严格测试 Workspace 根，拒绝任何 symlink。"""
    uid = str(uid or "")
    if not _is_orphan_test_workspace_uid(uid):
        raise RuntimeError(f"Static test cleanup requires an explicit test UID: {uid!r}")
    data_root = get_user_data_dir()
    shared_root = data_root / "shared"
    if data_root.is_symlink() or shared_root.is_symlink():
        raise RuntimeError("Static test cleanup refuses symlink workspace root")

    target = shared_root / uid
    if target.is_symlink():
        raise RuntimeError(f"Static test cleanup refuses symlink UID root: {target}")
    if not target.exists():
        return
    if not target.is_dir():
        raise RuntimeError(f"Static test cleanup UID root is not a directory: {target}")

    resolved_shared = shared_root.resolve()
    resolved_target = target.resolve()
    if resolved_target.parent != resolved_shared:
        raise RuntimeError(f"Static test cleanup path escaped shared root: {resolved_target}")
    for current_root, directory_names, file_names in os.walk(target, followlinks=False):
        current = Path(current_root)
        for name in [*directory_names, *file_names]:
            candidate = current / name
            if candidate.is_symlink():
                raise RuntimeError(f"Static test cleanup refuses symlink: {candidate}")

    shutil.rmtree(target)
    if target.exists() or target.is_symlink():
        raise RuntimeError(f"Static test cleanup left UID root behind: {target}")


async def delete_e2e_run_rows(thread_ids: set[str]) -> None:
    """删除 E2E 测试线程对应的 agent_runs 审计行。

    线程删除 API 只软删对话，run 行作为审计事实不会级联；测试 run 若不
    清理会永久残留并污染运行历史，因此按已识别（带 _yuxi_e2e 标记）的
    线程 id 直接删除。外键链 agent_run_requests/messages/tool_calls/
    message_feedbacks 均无级联，按叶子到根的顺序删除；attempt 由级联
    外键一并删除。
    """
    if not thread_ids:
        return
    thread_ids_list = sorted(thread_ids)
    run_ids_sql = "SELECT id FROM agent_runs WHERE conversation_thread_id = ANY($1::text[])"
    message_ids_sql = f"SELECT id FROM messages WHERE run_id IN ({run_ids_sql})"
    conn = await asyncpg.connect(_postgres_dsn())
    try:
        async with conn.transaction():
            await conn.execute(
                f"DELETE FROM tool_calls WHERE message_id IN ({message_ids_sql})",
                thread_ids_list,
            )
            await conn.execute(
                f"DELETE FROM message_feedbacks WHERE message_id IN ({message_ids_sql})",
                thread_ids_list,
            )
            await conn.execute(
                "DELETE FROM agent_run_requests "
                f"WHERE conversation_thread_id = ANY($1::text[]) OR dispatched_run_id IN ({run_ids_sql})",
                thread_ids_list,
            )
            await conn.execute(
                f"DELETE FROM messages WHERE run_id IN ({run_ids_sql})",
                thread_ids_list,
            )
            await conn.execute(
                "DELETE FROM agent_runs WHERE conversation_thread_id = ANY($1::text[])",
                thread_ids_list,
            )
    finally:
        await conn.close()


async def list_test_conversation_resources(
    owner_uid: str,
    *,
    include_all_owned: bool = False,
) -> dict[str, CleanupConversationResource]:
    """读取当前测试用户的测试 Conversation、状态和真实 Workdir。"""

    conn = await asyncpg.connect(_postgres_dsn())
    try:
        request_rows = await conn.fetch(
            "SELECT DISTINCT conversation_thread_id "
            "FROM agent_run_requests "
            "WHERE uid = $1 AND ("
            "left(request_id, char_length($2)) = $2 "
            "OR left(request_id, char_length($3)) = $3"
            ")",
            owner_uid,
            TEST_RESOURCE_PREFIX,
            "agent-call-queue-",
        )
        request_thread_ids = {str(row["conversation_thread_id"] or "") for row in request_rows}
        rows = await conn.fetch(
            "SELECT c.id, c.project_id, c.thread_id, c.uid, c.status, c.title, "
            "p.workdir_path, p.directory_mode, p.selection_status, c.extra_metadata, c.agent_id "
            "FROM conversations c JOIN projects p ON p.id = c.project_id AND p.uid = c.uid "
            "WHERE c.uid = $1",
            owner_uid,
        )
        marked_parent_ids: list[int] = []
        resources: dict[str, CleanupConversationResource] = {}
        for row in rows:
            thread_id = str(row["thread_id"] or "")
            if not include_all_owned and (
                not _is_test_thread(
                    {
                        "title": row["title"],
                        "metadata": row["extra_metadata"],
                        "agent_id": row["agent_id"],
                    }
                )
                and thread_id not in request_thread_ids
            ):
                continue
            marked_parent_ids.append(int(row["id"]))
            resources[thread_id] = CleanupConversationResource(
                conversation_id=int(row["id"]),
                project_id=str(row["project_id"]),
                thread_id=thread_id,
                uid=str(row["uid"] or owner_uid),
                status=str(row["status"] or ""),
                workdir_path=(
                    str(row["workdir_path"])
                    if row["directory_mode"] == "managed"
                    and row["selection_status"] == "implicit"
                    and row["workdir_path"]
                    else None
                ),
            )

        if marked_parent_ids:
            child_rows = await conn.fetch(
                """
                SELECT child.id, child.project_id, child.thread_id, child.uid, child.status,
                       project.workdir_path, project.directory_mode, project.selection_status
                FROM subagent_threads st
                JOIN conversations child ON child.id = st.child_conversation_id
                JOIN projects project ON project.id = child.project_id AND project.uid = child.uid
                WHERE st.parent_conversation_id = ANY($1::int[])
                """,
                marked_parent_ids,
            )
            for row in child_rows:
                child_id = str(row["thread_id"] or "")
                resources[child_id] = CleanupConversationResource(
                    conversation_id=int(row["id"]),
                    project_id=str(row["project_id"]),
                    thread_id=child_id,
                    uid=str(row["uid"] or owner_uid),
                    status=str(row["status"] or ""),
                    workdir_path=(
                        str(row["workdir_path"])
                        if row["directory_mode"] == "managed"
                        and row["selection_status"] == "implicit"
                        and row["workdir_path"]
                        else None
                    ),
                )
        return resources
    finally:
        await conn.close()


async def _list_e2e_thread_statuses(owner_uid: str) -> dict[str, str]:
    """兼容旧调用方，返回测试线程状态。"""

    resources = await list_test_conversation_resources(owner_uid)
    return {
        thread_id: resource.status for thread_id, resource in resources.items() if SAFE_THREAD_ID.fullmatch(thread_id)
    }


async def validate_test_workdirs_exclusive(
    workdirs: dict[tuple[str, str], set[str]],
    target_project_ids: set[str],
) -> None:
    """确认待删 Workdir 未被目标外的 Project 共享。"""

    if not workdirs:
        return
    conn = await asyncpg.connect(_postgres_dsn())
    try:
        await _validate_test_workdirs_exclusive(conn, workdirs, target_project_ids)
    finally:
        await conn.close()


async def _validate_test_workdirs_exclusive(
    conn: asyncpg.Connection,
    workdirs: dict[tuple[str, str], set[str]],
    target_project_ids: set[str],
) -> None:
    """使用调用方事务确认 Project Workdir 没有目标外 Owner。"""

    rows_by_uid: dict[str, list[asyncpg.Record]] = {}
    for uid, _workdir_path in workdirs:
        if uid not in rows_by_uid:
            rows_by_uid[uid] = await conn.fetch(
                "SELECT id, workdir_path FROM projects WHERE uid = $1",
                uid,
            )

    for (uid, workdir_path), project_ids in workdirs.items():
        try:
            target_path = PurePosixPath(normalize_workdir_path(workdir_path))
        except ValueError as exc:
            raise RuntimeError(f"Test conversation cleanup refuses invalid Workdir: {workdir_path!r}") from exc
        owners: set[str] = set()
        for row in rows_by_uid[uid]:
            candidate_value = str(row["workdir_path"] or "")
            try:
                candidate_path = PurePosixPath(normalize_workdir_path(candidate_value))
            except ValueError as exc:
                raise RuntimeError(
                    "Test conversation cleanup cannot verify an existing Workdir owner: "
                    f"{row['id']}={candidate_value!r}"
                ) from exc
            overlaps = (
                candidate_path == target_path
                or candidate_path in target_path.parents
                or target_path in candidate_path.parents
            )
            if overlaps:
                owners.add(str(row["id"] or ""))
        unexpected = owners - target_project_ids
        if unexpected:
            raise RuntimeError(
                f"Test conversation cleanup refuses shared or overlapping Workdir {workdir_path!r}; "
                f"other projects: {', '.join(sorted(unexpected))}"
            )
        if not project_ids <= target_project_ids:
            raise RuntimeError(f"Test conversation cleanup has an untracked Workdir owner: {workdir_path!r}")


async def validate_test_runs_terminal(thread_ids: set[str]) -> None:
    """阻止清理流程删除仍由 worker 执行的测试 Run。"""

    if not thread_ids:
        return
    conn = await asyncpg.connect(_postgres_dsn())
    try:
        rows = await conn.fetch(
            "SELECT id, status FROM agent_runs "
            "WHERE conversation_thread_id = ANY($1::text[]) AND status <> ALL($2::text[])",
            sorted(thread_ids),
            list(AGENT_RUN_TERMINAL_STATUSES),
        )
        if rows:
            details = ", ".join(f"{row['id']}={row['status']}" for row in rows)
            raise RuntimeError(f"test Run is not terminal: {details}")
    finally:
        await conn.close()


async def list_test_queued_request_ids(thread_ids: set[str]) -> list[str]:
    """读取待清理 Conversation 尚未派发的请求。"""

    if not thread_ids:
        return []
    conn = await asyncpg.connect(_postgres_dsn())
    try:
        rows = await conn.fetch(
            "SELECT request_id FROM agent_run_requests "
            "WHERE conversation_thread_id = ANY($1::text[]) AND status = 'queued' "
            "ORDER BY created_at, id",
            sorted(thread_ids),
        )
        return [str(row["request_id"]) for row in rows]
    finally:
        await conn.close()


async def delete_test_conversation_rows(thread_ids: set[str]) -> None:
    """物理删除已完成测试清理的 Conversation 及其历史关联行。"""

    if not thread_ids:
        return
    thread_ids_list = sorted(thread_ids)
    conn = await asyncpg.connect(_postgres_dsn())
    try:
        async with conn.transaction():
            await _delete_test_conversation_rows(conn, thread_ids_list)

        await _assert_test_conversations_deleted(conn, thread_ids_list)
    finally:
        await conn.close()


async def delete_test_conversation_resources(
    workdirs: dict[tuple[str, str], set[str]],
    thread_ids: set[str],
    workdir_project_ids: set[str],
) -> None:
    """先提交测试 Conversation 删除，再在 Project Owner 锁内清理对应文件。"""

    if not thread_ids:
        return
    thread_ids_list = sorted(thread_ids)
    conn = await asyncpg.connect(_postgres_dsn())
    try:
        async with conn.transaction():
            await conn.execute("LOCK TABLE projects IN SHARE MODE")
            await conn.execute("LOCK TABLE conversations IN SHARE MODE")
            await _validate_test_workdirs_exclusive(conn, workdirs, workdir_project_ids)
            await _delete_test_conversation_rows(conn, thread_ids_list)

        await _assert_test_conversations_deleted(conn, thread_ids_list)
        try:
            async with conn.transaction():
                # 与 linked Project 创建共享同一路径锁；拿锁后再回读 Owner，
                # 保证创建要么先提交并被看见，要么在删除后重新校验目录。
                for uid in sorted({uid for uid, _workdir_path in workdirs}):
                    await conn.execute(
                        "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
                        f"project-workdir:{uid}",
                    )
                await conn.execute("LOCK TABLE conversations IN SHARE MODE")
                await _validate_test_workdirs_exclusive(conn, workdirs, workdir_project_ids)
                for uid, workdir_path in workdirs:
                    remove_test_workdir(uid, workdir_path)
                for thread_id in thread_ids:
                    remove_e2e_thread_storage(thread_id)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"Test conversation rows were deleted, but filesystem cleanup failed: {exc}") from exc
    finally:
        await conn.close()


async def _delete_test_conversation_rows(conn: asyncpg.Connection, thread_ids_list: list[str]) -> None:
    """使用调用方事务删除测试 Conversation 的完整历史。"""

    conversation_rows = await conn.fetch(
        "SELECT c.id, c.project_id, p.selection_status "
        "FROM conversations c JOIN projects p ON p.id = c.project_id AND p.uid = c.uid "
        "WHERE c.thread_id = ANY($1::text[])",
        thread_ids_list,
    )
    conversation_ids = [int(row["id"]) for row in conversation_rows]
    implicit_project_ids = [
        str(row["project_id"])
        for row in conversation_rows
        if row["project_id"] and row["selection_status"] == "implicit"
    ]
    run_rows = await conn.fetch(
        "SELECT id, uid, manifest FROM agent_runs "
        "WHERE conversation_thread_id = ANY($1::text[]) OR conversation_id = ANY($2::int[])",
        thread_ids_list,
        conversation_ids,
    )
    run_ids = [str(row["id"]) for row in run_rows]
    snapshot_ids_by_uid: dict[str, set[str]] = {}
    for row in run_rows:
        runtime_snapshot = _parse_metadata(row["manifest"]).get("runtime_snapshot")
        snapshot_id = runtime_snapshot.get("id") if isinstance(runtime_snapshot, dict) else None
        if isinstance(snapshot_id, str) and snapshot_id:
            snapshot_ids_by_uid.setdefault(str(row["uid"]), set()).add(snapshot_id)
    message_rows = await conn.fetch(
        "SELECT id FROM messages WHERE conversation_id = ANY($1::int[]) OR run_id = ANY($2::text[])",
        conversation_ids,
        run_ids,
    )
    message_ids = [int(row["id"]) for row in message_rows]
    request_rows = await conn.fetch(
        "SELECT uid, input_payload FROM agent_run_requests "
        "WHERE conversation_thread_id = ANY($1::text[]) "
        "OR input_message_id = ANY($2::int[]) "
        "OR dispatched_run_id = ANY($3::text[])",
        thread_ids_list,
        message_ids,
        run_ids,
    )
    for row in request_rows:
        payload = _parse_metadata(row["input_payload"])
        manifest = _parse_metadata(payload.get("_run_manifest"))
        runtime_snapshot = manifest.get("runtime_snapshot")
        snapshot_id = runtime_snapshot.get("id") if isinstance(runtime_snapshot, dict) else None
        if isinstance(snapshot_id, str) and snapshot_id:
            snapshot_ids_by_uid.setdefault(str(row["uid"]), set()).add(snapshot_id)

    await conn.execute(
        "DELETE FROM agent_run_requests "
        "WHERE conversation_thread_id = ANY($1::text[]) "
        "OR input_message_id = ANY($2::int[]) "
        "OR dispatched_run_id = ANY($3::text[])",
        thread_ids_list,
        message_ids,
        run_ids,
    )
    await conn.execute("DELETE FROM tool_calls WHERE message_id = ANY($1::int[])", message_ids)
    await conn.execute(
        "DELETE FROM message_feedbacks WHERE message_id = ANY($1::int[])",
        message_ids,
    )
    await conn.execute("DELETE FROM messages WHERE id = ANY($1::int[])", message_ids)
    await conn.execute("DELETE FROM agent_runs WHERE id = ANY($1::text[])", run_ids)
    for uid, snapshot_ids in snapshot_ids_by_uid.items():
        await conn.execute(
            "DELETE FROM run_resource_snapshots WHERE uid = $1 AND id = ANY($2::text[])",
            uid,
            sorted(snapshot_ids),
        )
    await conn.execute(
        "DELETE FROM agentscope_team_worker_bindings WHERE subagent_thread_relation_id IN "
        "(SELECT id FROM subagent_threads WHERE parent_conversation_id = ANY($1::int[]) "
        "OR child_conversation_id = ANY($1::int[]))",
        conversation_ids,
    )
    await conn.execute(
        "DELETE FROM subagent_threads "
        "WHERE parent_conversation_id = ANY($1::int[]) "
        "OR child_conversation_id = ANY($1::int[])",
        conversation_ids,
    )
    await conn.execute(
        "DELETE FROM conversation_stats WHERE conversation_id = ANY($1::int[])",
        conversation_ids,
    )
    await conn.execute("DELETE FROM conversations WHERE id = ANY($1::int[])", conversation_ids)
    await conn.execute(
        "DELETE FROM projects WHERE id = ANY($1::text[]) "
        "AND NOT EXISTS (SELECT 1 FROM conversations WHERE conversations.project_id = projects.id)",
        implicit_project_ids,
    )


async def delete_orphaned_test_projects(owner_uid: str) -> None:
    """删除当前用户无 Conversation 引用且显式标记的测试 Project。"""

    conn = await asyncpg.connect(_postgres_dsn())
    try:
        await conn.execute(
            "DELETE FROM projects WHERE uid = $1 "
            "AND left(idempotency_key, char_length($2)) = $2 "
            "AND NOT EXISTS (SELECT 1 FROM conversations WHERE conversations.project_id = projects.id)",
            owner_uid,
            TEST_RESOURCE_PREFIX,
        )
    finally:
        await conn.close()


async def _assert_test_user_has_no_projects(owner_uid: str) -> None:
    """回读确认专用测试 UID 已无任何 Project Owner。"""
    conn = await asyncpg.connect(_postgres_dsn())
    try:
        rows = await conn.fetch("SELECT id FROM projects WHERE uid = $1 ORDER BY id", owner_uid)
        if rows:
            raise RuntimeError(
                f"Static test cleanup left Projects behind for {owner_uid}: "
                + ", ".join(str(row["id"]) for row in rows)
            )
    finally:
        await conn.close()


async def _static_test_user_exists(owner_uid: str) -> bool:
    """从数据库判断测试身份是否仍拥有自己的 Workspace 根。"""
    conn = await asyncpg.connect(_postgres_dsn())
    try:
        return bool(await conn.fetchval("SELECT 1 FROM users WHERE uid = $1", owner_uid))
    finally:
        await conn.close()


async def list_static_test_user_uids() -> list[str]:
    """列出数据库中的专用 E2E 用户，供全局会话清理回收失败残留。"""
    conn = await asyncpg.connect(_postgres_dsn())
    try:
        rows = await conn.fetch("SELECT uid FROM users ORDER BY uid")
        return [str(row["uid"]) for row in rows if _is_static_test_uid(str(row["uid"] or ""))]
    finally:
        await conn.close()


def list_static_test_workspace_uids() -> list[str]:
    """从共享目录枚举严格命名的 E2E UID 根，包括已无用户行的孤儿。"""
    data_root = get_user_data_dir()
    shared_root = data_root / "shared"
    if data_root.is_symlink() or shared_root.is_symlink():
        raise RuntimeError("Static test cleanup refuses symlink workspace root")
    if not shared_root.exists():
        return []
    if not shared_root.is_dir():
        raise RuntimeError(f"Static test cleanup shared root is not a directory: {shared_root}")
    return sorted(
        candidate.name for candidate in shared_root.iterdir() if _is_orphan_test_workspace_uid(candidate.name)
    )


async def cleanup_orphaned_static_test_user_workspace_roots() -> None:
    """只删除已无 User 和 Project 的 E2E Workspace 根。"""
    for uid in list_static_test_workspace_uids():
        if await _static_test_user_exists(uid):
            continue
        await _assert_test_user_has_no_projects(uid)
        remove_test_user_workspace_root(uid)


async def cleanup_orphaned_static_test_departments() -> None:
    """删除无用户和 API Key 引用的严格 snapshot 测试部门。"""
    conn = await asyncpg.connect(_postgres_dsn())
    try:
        async with conn.transaction():
            rows = await conn.fetch("SELECT id, name FROM departments ORDER BY id FOR UPDATE")
            for row in rows:
                if not SNAPSHOT_TEST_DEPARTMENT_NAME.fullmatch(str(row["name"] or "")):
                    continue
                await conn.execute(
                    "DELETE FROM departments AS department "
                    "WHERE department.id = $1 "
                    "AND NOT EXISTS (SELECT 1 FROM users WHERE department_id = department.id) "
                    "AND NOT EXISTS (SELECT 1 FROM api_keys WHERE department_id = department.id)",
                    int(row["id"]),
                )
    finally:
        await conn.close()


async def cleanup_test_agentscope_thread_resources(owner_uid: str, thread_ids: set[str]) -> None:
    """在删除 Yuxi 映射前销毁测试线程的 AgentScope Session 与 Workspace。"""
    if not thread_ids:
        return

    from yuxi.services.conversation_service import _delete_agentscope_thread_resources
    from yuxi.storage.postgres.manager import pg_manager

    pg_manager.initialize()
    async with pg_manager.get_async_session_context() as db:
        for thread_id in sorted(thread_ids):
            await _delete_agentscope_thread_resources(
                db,
                uid=owner_uid,
                thread_id=thread_id,
            )


async def _delete_static_test_user_record(owner_uid: str) -> None:
    """物理删除专用 E2E 用户及全部用户级持久化依赖。"""
    owner_uid = _require_static_test_uid(owner_uid)
    conn = await asyncpg.connect(_postgres_dsn())
    try:
        async with conn.transaction():
            user_id = await conn.fetchval(
                "SELECT id FROM users WHERE uid = $1 FOR UPDATE",
                owner_uid,
            )
            run_ids = [
                str(row["id"])
                for row in await conn.fetch(
                    "SELECT id FROM agent_runs WHERE uid = $1",
                    owner_uid,
                )
            ]
            message_ids = [
                int(row["id"])
                for row in await conn.fetch(
                    "SELECT id FROM messages WHERE run_id = ANY($1::text[])",
                    run_ids,
                )
            ]

            await conn.execute(
                "DELETE FROM agent_run_requests WHERE uid = $1 OR dispatched_run_id = ANY($2::text[])",
                owner_uid,
                run_ids,
            )
            await conn.execute("DELETE FROM tool_calls WHERE message_id = ANY($1::int[])", message_ids)
            await conn.execute(
                "DELETE FROM message_feedbacks WHERE uid = $1 OR message_id = ANY($2::int[])",
                owner_uid,
                message_ids,
            )
            await conn.execute("DELETE FROM messages WHERE id = ANY($1::int[])", message_ids)
            await conn.execute("DELETE FROM agent_runs WHERE id = ANY($1::text[])", run_ids)

            await conn.execute(
                "DELETE FROM agentscope_team_worker_bindings WHERE uid = $1",
                owner_uid,
            )
            await conn.execute("DELETE FROM subagent_threads WHERE uid = $1", owner_uid)
            await conn.execute("DELETE FROM agentscope_thread_sessions WHERE uid = $1", owner_uid)
            await conn.execute(
                "DELETE FROM task_executions WHERE triggered_by_uid = $1 OR execution_principal_uid = $1",
                owner_uid,
            )
            await conn.execute("DELETE FROM agent_tasks WHERE owner_uid = $1", owner_uid)
            await conn.execute(
                "DELETE FROM agentscope_channel_bindings WHERE owner_uid = $1",
                owner_uid,
            )
            await conn.execute(
                "DELETE FROM agents WHERE created_by = $1 AND is_default IS NOT TRUE",
                owner_uid,
            )
            # thread_artifacts 属运行时扩展表，部分环境未建；缺表时跳过该段清理
            thread_artifacts_exists = await conn.fetchval(
                "SELECT to_regclass('thread_artifacts') IS NOT NULL"
            )
            if thread_artifacts_exists:
                await conn.execute("DELETE FROM thread_artifacts WHERE uid = $1", owner_uid)
            await conn.execute("DELETE FROM run_resource_snapshots WHERE uid = $1", owner_uid)
            await conn.execute("DELETE FROM agent_memory_scopes WHERE uid = $1", owner_uid)
            await conn.execute("DELETE FROM agent_envs WHERE uid = $1", owner_uid)
            await conn.execute("DELETE FROM user_config WHERE uid = $1", owner_uid)

            if user_id is not None:
                await conn.execute(
                    "DELETE FROM cli_auth_sessions "
                    "WHERE approved_user_id = $1 "
                    "OR api_key_id IN (SELECT id FROM api_keys WHERE user_id = $1)",
                    user_id,
                )
                await conn.execute("DELETE FROM api_keys WHERE user_id = $1", user_id)
                await conn.execute("DELETE FROM operation_logs WHERE user_id = $1", user_id)
            await conn.execute(
                "DELETE FROM department_memberships WHERE user_id IN (SELECT id FROM users WHERE uid = $1)",
                owner_uid,
            )
            await conn.execute(
                "DELETE FROM auth_sessions WHERE user_id IN (SELECT id FROM users WHERE uid = $1)",
                owner_uid,
            )
            await conn.execute("DELETE FROM users WHERE uid = $1", owner_uid)

            remaining = await conn.fetchrow(
                "SELECT "
                "(SELECT count(*) FROM users WHERE uid = $1) AS users, "
                "(SELECT count(*) FROM agent_runs WHERE uid = $1) AS runs, "
                "(SELECT count(*) FROM agent_run_requests WHERE uid = $1) AS requests, "
                "(SELECT count(*) FROM run_resource_snapshots WHERE uid = $1) AS snapshots, "
                "(SELECT count(*) FROM agent_memory_scopes WHERE uid = $1) AS memory_scopes, "
                "(SELECT count(*) FROM agentscope_thread_sessions WHERE uid = $1) AS thread_sessions, "
                "(SELECT count(*) FROM user_config WHERE uid = $1) AS user_configs, "
                "(SELECT count(*) FROM agent_envs WHERE uid = $1) AS agent_envs",
                owner_uid,
            )
            leftovers = {name: int(count) for name, count in dict(remaining).items() if int(count)}
            if leftovers:
                raise RuntimeError(f"Static test user cleanup left database rows for {owner_uid}: {leftovers}")
    finally:
        await conn.close()


async def cleanup_static_test_user_resources(owner_uid: str) -> None:
    """清理专用 E2E UID 的完整业务、执行与 Workspace 事实。"""
    owner_uid = _require_static_test_uid(owner_uid)
    resources = await list_test_conversation_resources(owner_uid, include_all_owned=True)
    thread_ids = set(resources)
    workdir_targets: dict[tuple[str, str], set[str]] = {}
    for resource in resources.values():
        if not SAFE_THREAD_ID.fullmatch(resource.thread_id):
            raise RuntimeError(f"Static test cleanup received an unsafe thread id: {resource.thread_id!r}")
        _resolve_e2e_thread_storage(resource.thread_id)
        if resource.workdir_path:
            _resolve_test_workdir(resource.uid, resource.workdir_path)
            workdir_targets.setdefault((resource.uid, resource.workdir_path), set()).add(resource.project_id)

    workdir_project_ids = {project_id for project_ids in workdir_targets.values() for project_id in project_ids}
    await validate_test_workdirs_exclusive(workdir_targets, workdir_project_ids)
    await validate_test_runs_terminal(thread_ids)
    await cleanup_test_agentscope_thread_resources(owner_uid, thread_ids)
    await delete_test_conversation_resources(workdir_targets, thread_ids, workdir_project_ids)
    await delete_orphaned_test_projects(owner_uid)
    await _assert_test_user_has_no_projects(owner_uid)
    await _delete_static_test_user_record(owner_uid)
    remove_test_user_workspace_root(owner_uid)


async def _assert_test_conversations_deleted(conn: asyncpg.Connection, thread_ids_list: list[str]) -> None:
    """回读确认目标 Conversation 已物理删除。"""

    remaining = await conn.fetch(
        "SELECT thread_id FROM conversations WHERE thread_id = ANY($1::text[])",
        thread_ids_list,
    )
    if remaining:
        raise RuntimeError(
            "Test conversation cleanup left conversations behind: "
            + ", ".join(sorted(str(row["thread_id"]) for row in remaining))
        )


async def cleanup_test_chat_resources(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    *,
    owner_uid: str,
) -> None:
    """删除测试对话、消息/run 历史、Project Workdir 和临时智能体。"""

    page_size = 500
    offset = 0
    threads: list[dict] = []
    seen_thread_ids: set[str] = set()
    while True:
        threads_response = await client.get(
            "/api/chat/threads",
            params={"limit": page_size, "offset": offset},
            headers=headers,
        )
        if threads_response.status_code != 200:
            raise RuntimeError(f"Failed to list E2E conversations for cleanup: {threads_response.text}")

        page = threads_response.json()
        if not isinstance(page, list):
            raise RuntimeError("E2E conversation cleanup response must be a list")
        threads.extend(
            thread
            for thread in page
            if isinstance(thread, dict)
            and str(thread.get("id") or thread.get("thread_id") or "") not in seen_thread_ids
        )
        seen_thread_ids.update(
            str(thread.get("id") or thread.get("thread_id"))
            for thread in page
            if isinstance(thread, dict) and (thread.get("id") or thread.get("thread_id"))
        )

        non_pinned_count = sum(not bool(thread.get("is_pinned")) for thread in page if isinstance(thread, dict))
        if len(page) < page_size or non_pinned_count == 0:
            break
        offset += non_pinned_count

    active_test_thread_ids = {
        str(thread.get("id") or thread.get("thread_id") or "") for thread in threads if _is_test_thread(thread)
    }
    if "" in active_test_thread_ids:
        raise RuntimeError("Test conversation cleanup entry is missing thread id")

    try:
        resources = await list_test_conversation_resources(owner_uid)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Failed to list persisted test conversation resources: {exc}") from exc

    missing_resources = active_test_thread_ids - resources.keys()
    if missing_resources:
        raise RuntimeError(
            "Test conversation cleanup could not verify persisted ownership for: "
            + ", ".join(sorted(missing_resources))
        )

    target_thread_ids = set(resources)
    workdir_targets: dict[tuple[str, str], set[str]] = {}
    for resource in resources.values():
        if not SAFE_THREAD_ID.fullmatch(resource.thread_id):
            raise RuntimeError(f"Test conversation cleanup received an unsafe thread id: {resource.thread_id!r}")
        _resolve_e2e_thread_storage(resource.thread_id)
        if resource.workdir_path:
            _resolve_test_workdir(resource.uid, resource.workdir_path)
            workdir_targets.setdefault((resource.uid, resource.workdir_path), set()).add(resource.project_id)

    workdir_project_ids = {project_id for project_ids in workdir_targets.values() for project_id in project_ids}
    await validate_test_workdirs_exclusive(workdir_targets, workdir_project_ids)
    await validate_test_runs_terminal(target_thread_ids)

    for request_id in await list_test_queued_request_ids(target_thread_ids):
        cancel_response = await client.post(f"/api/agent/requests/{request_id}/cancel", headers=headers)
        if cancel_response.status_code not in {200, 404}:
            raise RuntimeError(f"Failed to cancel queued test request {request_id}: {cancel_response.text}")

    remaining_queued_request_ids = await list_test_queued_request_ids(target_thread_ids)
    if remaining_queued_request_ids:
        raise RuntimeError(
            "Test conversation cleanup left queued requests behind: " + ", ".join(remaining_queued_request_ids)
        )
    await validate_test_runs_terminal(target_thread_ids)

    for resource in resources.values():
        if resource.status in {"deleted", ""}:
            continue
        delete_response = await client.delete(f"/api/chat/thread/{resource.thread_id}", headers=headers)
        if delete_response.status_code not in {200, 404}:
            raise RuntimeError(
                f"Failed to delete persisted test conversation {resource.thread_id}: {delete_response.text}"
            )

    await delete_test_conversation_resources(workdir_targets, target_thread_ids, workdir_project_ids)
    await delete_orphaned_test_projects(owner_uid)

    failures: list[str] = []
    agents_response = await client.get(
        "/api/agent",
        params={"include_subagents": "true"},
        headers=headers,
    )
    if agents_response.status_code != 200:
        failures.append(f"Failed to list E2E agents for cleanup: {agents_response.text}")
    else:
        payload = agents_response.json()
        agents = payload.get("agents") if isinstance(payload, dict) else None
        if not isinstance(agents, list):
            failures.append("Test agent cleanup response is missing an agents list")
        else:
            for agent in agents:
                if not _is_e2e_agent(agent, owner_uid):
                    continue
                agent_slug = agent.get("slug") or agent.get("agent_id") or agent.get("id")
                if not agent_slug:
                    failures.append("Test agent cleanup entry is missing agent slug")
                    continue
                delete_response = await client.delete(f"/api/agent/{agent_slug}", headers=headers)
                if delete_response.status_code not in {200, 404}:
                    failures.append(f"Failed to delete test agent {agent_slug}: {delete_response.text}")

    if failures:
        raise RuntimeError("; ".join(failures))


async def cleanup_e2e_chat_resources(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    *,
    owner_uid: str,
) -> None:
    """兼容旧 E2E fixture 的测试聊天清理入口。"""

    await cleanup_test_chat_resources(
        client,
        headers,
        owner_uid=owner_uid,
    )


async def cleanup_pytest_knowledge_resources(
    client: httpx.AsyncClient,
    headers: dict[str, str],
) -> None:
    """通过公开 API 删除 pytest 前缀的评估资源和知识库。"""

    list_response = await client.get("/api/knowledge/databases", headers=headers)
    if list_response.status_code != 200:
        raise RuntimeError(f"Failed to list knowledge databases for cleanup: {list_response.text}")

    payload = list_response.json()
    if payload.get("message"):
        raise RuntimeError(f"Failed to list knowledge databases for cleanup: {payload['message']}")

    databases = payload.get("databases")
    if not isinstance(databases, list):
        raise RuntimeError("Knowledge database cleanup response is missing a databases list")

    failures: list[str] = []
    for database in databases:
        kb_id = database.get("kb_id") if isinstance(database, dict) else None
        if not kb_id:
            failures.append("Knowledge database cleanup entry is missing kb_id")
            continue

        resource_specs = (
            (f"/api/evaluation/databases/{kb_id}/runs", "run_id", f"/api/evaluation/databases/{kb_id}/runs"),
            (f"/api/evaluation/databases/{kb_id}/datasets", "dataset_id", "/api/evaluation/datasets"),
        )
        for list_path, id_field, delete_prefix in resource_specs:
            response = await client.get(list_path, headers=headers)
            if response.status_code != 200:
                failures.append(f"Failed to list evaluation resources for {kb_id}: {response.text}")
                continue

            resources = response.json().get("data")
            if not isinstance(resources, list):
                failures.append(f"Evaluation cleanup response for {kb_id} is missing a data list")
                continue

            for resource in resources:
                if not isinstance(resource, dict) or not _is_pytest_resource(resource.get("name")):
                    continue
                resource_id = resource.get(id_field)
                if not resource_id:
                    failures.append(f"Evaluation cleanup resource for {kb_id} is missing {id_field}")
                    continue

                delete_response = await client.delete(f"{delete_prefix}/{resource_id}", headers=headers)
                if delete_response.status_code not in {200, 404}:
                    failures.append(f"Failed to delete evaluation resource {resource_id}: {delete_response.text}")

    for database in databases:
        if not isinstance(database, dict) or not _is_pytest_resource(database.get("name")):
            continue
        kb_id = database.get("kb_id")
        if not kb_id:
            continue

        delete_response = await client.delete(f"/api/knowledge/databases/{kb_id}", headers=headers)
        if delete_response.status_code not in {200, 404}:
            failures.append(f"Failed to delete knowledge database {kb_id}: {delete_response.text}")

    if failures:
        raise RuntimeError("; ".join(failures))
