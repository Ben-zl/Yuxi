"""ReMe 长期记忆 scope、目录与并发租约测试。"""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from yuxi.agentscope.memory import (
    MemoryScopeBusyError,
    ReMeRegistry,
    ScopedReMeMiddleware,
    memory_scope_identity,
    memory_workspace_path,
    validate_memory_workspace,
)
from yuxi.agentscope.memory_management import (
    clear_memory_workspace,
    delete_memory_card,
    list_memory_cards,
)
from yuxi.agentscope.memory_service import AgentMemoryService
from yuxi.agentscope import memory_service as memory_service_module

pytestmark = pytest.mark.unit


def test_memory_scope_identity_is_stable_and_hides_raw_scope(tmp_path):
    """scope 标识和路径必须确定性生成，且不暴露原始用户或 Agent 标识。"""
    identity = memory_scope_identity("user@example.com", "general-agent")
    path = memory_workspace_path(tmp_path, "user@example.com", "general-agent")

    assert identity == "61b1be417d9093afecaf3a9b77fa5363eaf8ce812a8c5b882f37262d80eeb3e9"
    assert path.parent.parent == tmp_path
    assert "user@example.com" not in str(path)
    assert "general-agent" not in str(path)
    assert len(path.parent.name) == 64
    assert len(path.name) == 64


async def test_memory_service_lists_real_cards_from_exact_user_agent_scope(tmp_path, monkeypatch):
    """同一 Agent 的不同用户只能读取各自 scope 中的真实 Markdown 卡片。"""

    def write_card(uid: str, marker: str) -> None:
        card_dir = memory_workspace_path(tmp_path, uid, "shared-agent") / "daily" / "2026-09-01"
        card_dir.mkdir(parents=True)
        (card_dir / "fact.md").write_text(
            f"---\nname: {marker}\ndescription: {marker} private memory\n---\n{marker}\n",
            encoding="utf-8",
        )

    write_card("user-a", "USER_A_ONLY")
    write_card("user-b", "USER_B_ONLY")

    @asynccontextmanager
    async def fake_session_context():
        yield object()

    class FakeScopeRepository:
        def __init__(self, _db):
            pass

        async def get(self, uid, agent_slug):
            return SimpleNamespace(dream_status=None, to_dict=lambda: {})

    monkeypatch.setattr(memory_service_module.pg_manager, "get_async_session_context", fake_session_context)
    monkeypatch.setattr(memory_service_module, "AgentMemoryScopeRepository", FakeScopeRepository)
    monkeypatch.setattr(
        memory_service_module.UserConfig,
        "load",
        AsyncMock(return_value=SimpleNamespace(schema=SimpleNamespace(enable_memory=True))),
    )
    service = AgentMemoryService(
        registry=ReMeRegistry(base_dir=tmp_path),
        base_dir=tmp_path,
    )

    user_a = await service.list_memories(
        "user-a",
        "shared-agent",
        kind="all",
        category="all",
        page=1,
        page_size=20,
    )
    user_b = await service.list_memories(
        "user-b",
        "shared-agent",
        kind="all",
        category="all",
        page=1,
        page_size=20,
    )

    assert [item["title"] for item in user_a["items"]] == ["USER_A_ONLY"]
    assert [item["title"] for item in user_b["items"]] == ["USER_B_ONLY"]


async def test_registry_single_flight_and_exclusive_busy(tmp_path):
    """并发创建只构造一个 core，活动 reply 阻止破坏性维护。"""
    core = SimpleNamespace(close=AsyncMock(), list_tools=AsyncMock(return_value=[]))
    factory_started = asyncio.Event()
    allow_factory = asyncio.Event()

    async def create_core(**_kwargs):
        factory_started.set()
        await allow_factory.wait()
        return core

    factory = AsyncMock(side_effect=create_core)
    registry = ReMeRegistry(base_dir=tmp_path, core_factory=factory, max_entries=10)

    task_a = asyncio.create_task(
        registry.acquire_reply(
            uid="u",
            agent_slug="a",
            fingerprint="model-v1",
            chat_model=object(),
            embedding_model=object(),
        ),
    )
    await factory_started.wait()
    task_b = asyncio.create_task(
        registry.acquire_reply(
            uid="u",
            agent_slug="a",
            fingerprint="model-v1",
            chat_model=object(),
            embedding_model=object(),
        ),
    )
    await asyncio.sleep(0)
    allow_factory.set()
    lease_a, lease_b = await asyncio.gather(task_a, task_b)

    assert lease_a.core is core
    assert lease_b.core is core
    factory.assert_awaited_once()
    with pytest.raises(MemoryScopeBusyError):
        async with registry.acquire_exclusive("u", "a"):
            pass

    await lease_a.release()
    await lease_b.release()
    async with registry.acquire_exclusive("u", "a") as exclusive:
        assert exclusive is core

    core.close.assert_awaited_once()


def test_memory_workspace_rejects_symlinked_scope_levels(tmp_path):
    """uid 或 Agent 哈希目录是符号链接时不得读取或写入外部目录。"""
    base = tmp_path / "reme"
    outside = tmp_path / "outside"
    base.mkdir()
    outside.mkdir()
    uid_dir = memory_workspace_path(base, "u", "a").parent
    uid_dir.symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="符号链接"):
        validate_memory_workspace(base, "u", "a", create=True)

    uid_dir.unlink()
    uid_dir.mkdir()
    scope = memory_workspace_path(base, "u", "a")
    scope.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="符号链接"):
        validate_memory_workspace(base, "u", "a")


def test_memory_card_management_rejects_scope_symlink(tmp_path):
    """即使外部目录包含合法卡片，管理函数也不能跟随 scope 符号链接。"""
    outside = tmp_path / "outside"
    daily = outside / "daily" / "2026-08-31"
    daily.mkdir(parents=True)
    (daily / "secret.md").write_text("secret", encoding="utf-8")
    scope = tmp_path / "uid" / "agent"
    scope.parent.mkdir()
    scope.symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="符号链接"):
        list_memory_cards(scope)


def test_memory_listing_skips_malformed_and_escaped_files(tmp_path):
    """列表应忽略非法日期、损坏编码和指向 scope 外部的卡片。"""
    valid = tmp_path / "daily" / "2026-09-01"
    invalid_date = tmp_path / "daily" / "2026-02-30"
    digest = tmp_path / "digest" / "wiki"
    valid.mkdir(parents=True)
    invalid_date.mkdir(parents=True)
    digest.mkdir(parents=True)
    (valid / "valid.md").write_text("Valid memory.", encoding="utf-8")
    (valid / "invalid-utf8.md").write_bytes(b"\xff\xfe")
    (invalid_date / "invalid-date.md").write_text("Must be hidden.", encoding="utf-8")
    outside = tmp_path.parent / "outside-memory.md"
    outside.write_text("Outside secret.", encoding="utf-8")
    (digest / "escaped.md").symlink_to(outside)

    listing = list_memory_cards(tmp_path)

    assert [item["title"] for item in listing["items"]] == ["valid"]


async def test_registry_exclusive_blocks_reply_and_other_maintenance(tmp_path):
    """维护租约整个持有期间必须阻止 reply 和第二个维护操作。"""
    core = SimpleNamespace(close=AsyncMock(), list_tools=AsyncMock(return_value=[]))
    registry = ReMeRegistry(base_dir=tmp_path, core_factory=AsyncMock(return_value=core))

    async with registry.acquire_exclusive("u", "a"):
        assert await registry.is_busy("u", "a") is True
        with pytest.raises(MemoryScopeBusyError):
            await registry.acquire_reply(
                uid="u",
                agent_slug="a",
                fingerprint="model-v1",
                chat_model=object(),
                embedding_model=object(),
            )
        with pytest.raises(MemoryScopeBusyError):
            async with registry.acquire_exclusive("u", "a"):
                pass

    assert await registry.is_busy("u", "a") is False


async def test_memory_listing_rejects_active_reply_with_busy(tmp_path):
    """管理读取必须在活动 reply 期间返回 busy，而不是扫描变化中的文件。"""
    core = SimpleNamespace(close=AsyncMock())
    registry = ReMeRegistry(base_dir=tmp_path, core_factory=AsyncMock(return_value=core))
    lease = await registry.acquire_reply(
        uid="u",
        agent_slug="a",
        fingerprint="v1",
        chat_model=object(),
        embedding_model=object(),
    )
    service = AgentMemoryService(registry=registry, base_dir=tmp_path)

    with pytest.raises(MemoryScopeBusyError):
        await service.list_memories("u", "a", kind="all", category="all", page=1, page_size=20)

    await lease.release()


async def test_registry_inspection_blocks_new_reply_without_closing_core(tmp_path):
    """一致性读取期间新 reply 应 fail-open，且读取本身不能关闭缓存 core。"""
    core = SimpleNamespace(close=AsyncMock())
    registry = ReMeRegistry(base_dir=tmp_path, core_factory=AsyncMock(return_value=core))
    lease = await registry.acquire_reply(
        uid="u",
        agent_slug="a",
        fingerprint="v1",
        chat_model=object(),
        embedding_model=object(),
    )
    await lease.release()

    async with registry.acquire_inspection("u", "a"):
        with pytest.raises(MemoryScopeBusyError):
            await registry.acquire_reply(
                uid="u",
                agent_slug="a",
                fingerprint="v1",
                chat_model=object(),
                embedding_model=object(),
            )
        core.close.assert_not_awaited()

    next_lease = await registry.acquire_reply(
        uid="u",
        agent_slug="a",
        fingerprint="v1",
        chat_model=object(),
        embedding_model=object(),
    )
    assert next_lease.core is core
    await next_lease.release()


async def test_registry_multi_scope_exclusive_is_all_or_nothing(tmp_path):
    """批量维护遇到任一活动 reply 时不能占用或弹出其他 scope。"""
    core_a = SimpleNamespace(close=AsyncMock())
    core_b = SimpleNamespace(close=AsyncMock())
    registry = ReMeRegistry(
        base_dir=tmp_path,
        core_factory=AsyncMock(side_effect=[core_a, core_b]),
    )
    lease_a = await registry.acquire_reply(
        uid="u",
        agent_slug="a",
        fingerprint="v1",
        chat_model=object(),
        embedding_model=object(),
    )
    lease_b = await registry.acquire_reply(
        uid="u",
        agent_slug="b",
        fingerprint="v1",
        chat_model=object(),
        embedding_model=object(),
    )
    await lease_b.release()

    with pytest.raises(MemoryScopeBusyError):
        async with registry.acquire_exclusive_many([("u", "a"), ("u", "b")]):
            pass

    assert await registry.is_busy("u", "b") is False
    lease_b_again = await registry.acquire_reply(
        uid="u",
        agent_slug="b",
        fingerprint="v1",
        chat_model=object(),
        embedding_model=object(),
    )
    assert lease_b_again.core is core_b
    await lease_b_again.release()
    await lease_a.release()


async def test_registry_waits_for_active_reply_before_fingerprint_rotation(tmp_path):
    """模型配置变化时等待旧 reply 结束，再关闭并替换 core。"""
    old_core = SimpleNamespace(close=AsyncMock())
    new_core = SimpleNamespace(close=AsyncMock())
    factory = AsyncMock(side_effect=[old_core, new_core])
    registry = ReMeRegistry(base_dir=tmp_path, core_factory=factory)
    old_lease = await registry.acquire_reply(
        uid="u",
        agent_slug="a",
        fingerprint="model-v1",
        chat_model=object(),
        embedding_model=object(),
    )
    waiting = asyncio.create_task(
        registry.acquire_reply(
            uid="u",
            agent_slug="a",
            fingerprint="model-v2",
            chat_model=object(),
            embedding_model=object(),
        ),
    )
    await asyncio.sleep(0)

    assert waiting.done() is False
    await old_lease.release()
    new_lease = await waiting

    assert new_lease.core is new_core
    old_core.close.assert_awaited_once()
    await new_lease.release()


async def test_registry_factory_failure_can_be_retried_without_busy_leak(tmp_path):
    """首次 core 创建失败不能遗留占用状态，下一次 reply 可以重新创建。"""
    core = SimpleNamespace(close=AsyncMock())
    factory = AsyncMock(side_effect=[RuntimeError("startup failed"), core])
    registry = ReMeRegistry(base_dir=tmp_path, core_factory=factory)

    with pytest.raises(RuntimeError, match="startup failed"):
        await registry.acquire_reply(
            uid="u",
            agent_slug="a",
            fingerprint="v1",
            chat_model=object(),
            embedding_model=object(),
        )

    assert await registry.is_busy("u", "a") is False
    lease = await registry.acquire_reply(
        uid="u",
        agent_slug="a",
        fingerprint="v1",
        chat_model=object(),
        embedding_model=object(),
    )
    assert lease.core is core
    await lease.release()


async def test_registry_capacity_never_evicts_active_reply(tmp_path):
    """超过 LRU 容量时只能回收空闲 core，活动 reply 必须保持可用。"""
    core_a = SimpleNamespace(close=AsyncMock())
    core_b = SimpleNamespace(close=AsyncMock())
    core_c = SimpleNamespace(close=AsyncMock())
    registry = ReMeRegistry(
        base_dir=tmp_path,
        core_factory=AsyncMock(side_effect=[core_a, core_b, core_c]),
        max_entries=1,
    )
    active = await registry.acquire_reply(
        uid="u",
        agent_slug="active",
        fingerprint="v1",
        chat_model=object(),
        embedding_model=object(),
    )
    idle = await registry.acquire_reply(
        uid="u",
        agent_slug="idle",
        fingerprint="v1",
        chat_model=object(),
        embedding_model=object(),
    )
    await idle.release()
    third = await registry.acquire_reply(
        uid="u",
        agent_slug="third",
        fingerprint="v1",
        chat_model=object(),
        embedding_model=object(),
    )

    core_a.close.assert_not_awaited()
    core_b.close.assert_awaited_once()
    assert active.core is core_a
    assert third.core is core_c
    await active.release()
    await third.release()


async def test_middleware_does_not_hold_reply_lease_during_tool_discovery(tmp_path):
    """工具发现不能把尚未开始的 reply 永久标记为活动。"""
    core = SimpleNamespace(
        close=AsyncMock(),
        list_tools=AsyncMock(return_value=[]),
        on_system_prompt=AsyncMock(return_value="prompt"),
    )
    registry = ReMeRegistry(base_dir=tmp_path, core_factory=AsyncMock(return_value=core))
    middleware = ScopedReMeMiddleware(
        registry=registry,
        uid="u",
        agent_slug="a",
        fingerprint="model-v1",
        chat_model=object(),
        embedding_model=object(),
    )

    assert await middleware.list_tools() == []
    assert await registry.is_busy("u", "a") is False


async def test_middleware_tool_discovery_failure_is_fail_open(tmp_path):
    """ReMe 工具发现失败时仍应允许普通聊天继续装配。"""
    core = SimpleNamespace(
        close=AsyncMock(),
        list_tools=AsyncMock(side_effect=RuntimeError("index unavailable")),
    )
    middleware = ScopedReMeMiddleware(
        registry=ReMeRegistry(
            base_dir=tmp_path,
            core_factory=AsyncMock(return_value=core),
        ),
        uid="u",
        agent_slug="a",
        fingerprint="model-v1",
        chat_model=object(),
        embedding_model=object(),
    )

    assert await middleware.list_tools() == []


async def test_middleware_does_not_repeat_reply_after_partial_output(tmp_path):
    """ReMe 写回异常发生在主回复产出后时，不得再次执行模型主链路。"""
    next_calls = 0

    async def next_handler(**_kwargs):
        nonlocal next_calls
        next_calls += 1
        yield "reply"

    class FailingAfterReplyCore:
        async def on_reply(self, _agent, input_kwargs, handler):
            async for item in handler(**input_kwargs):
                yield item
            raise RuntimeError("write-back failed")

        async def close(self):
            return None

    registry = ReMeRegistry(
        base_dir=tmp_path,
        core_factory=AsyncMock(return_value=FailingAfterReplyCore()),
    )
    middleware = ScopedReMeMiddleware(
        registry=registry,
        uid="u",
        agent_slug="a",
        fingerprint="model-v1",
        chat_model=object(),
        embedding_model=object(),
    )

    result = [item async for item in middleware.on_reply(object(), {"inputs": None}, next_handler)]

    assert result == ["reply"]
    assert next_calls == 1
    assert await registry.is_busy("u", "a") is False


async def test_memory_cards_only_expose_daily_and_digest_and_delete_reindexes(tmp_path):
    """管理面只暴露卡片，删除 Daily 时同步移除索引和孤立 transcript。"""
    daily = tmp_path / "daily" / "2026-08-31"
    digest = tmp_path / "digest" / "personal"
    transcript = tmp_path / "session" / "dialog"
    daily.mkdir(parents=True)
    digest.mkdir(parents=True)
    transcript.mkdir(parents=True)
    (daily / "preference.md").write_text(
        "---\nname: Preference\ndescription: Likes tea\nsession_id: session-1\n---\nUser likes tea.\n",
        encoding="utf-8",
    )
    (tmp_path / "daily" / "2026-08-31.md").write_text("index", encoding="utf-8")
    (digest / "profile.md").write_text(
        "---\nname: Profile\ndescription: Stable profile\n---\nDigest body.\n",
        encoding="utf-8",
    )
    (transcript / "session-1.jsonl").write_text("{}\n", encoding="utf-8")
    (tmp_path / "metadata").mkdir()
    (tmp_path / "metadata" / "state.md").write_text("hidden", encoding="utf-8")

    listing = list_memory_cards(tmp_path, kind="all", category="all", page=1, page_size=20)

    assert listing["pagination"]["total"] == 2
    daily_item = next(item for item in listing["items"] if item["kind"] == "daily")
    assert daily_item["summary"] == "Likes tea"
    maintain_index = AsyncMock()

    await delete_memory_card(tmp_path, daily_item["memory_id"], maintain_index=maintain_index)

    assert not (daily / "preference.md").exists()
    assert not (tmp_path / "daily" / "2026-08-31.md").exists()
    assert not (transcript / "session-1.jsonl").exists()
    assert (digest / "profile.md").exists()
    maintain_index.assert_awaited_once_with("2026-08-31")


async def test_memory_delete_restores_staged_files_when_index_maintenance_fails(tmp_path):
    """索引维护失败时恢复卡片和日期索引，并再次维护恢复后的索引。"""
    daily = tmp_path / "daily" / "2026-08-31"
    daily.mkdir(parents=True)
    card = daily / "preference.md"
    day_index = tmp_path / "daily" / "2026-08-31.md"
    card.write_text("---\nsession_id: session-1\n---\nLikes tea.\n", encoding="utf-8")
    day_index.write_text("index", encoding="utf-8")
    memory_id = list_memory_cards(tmp_path)["items"][0]["memory_id"]
    maintain_index = AsyncMock(side_effect=[RuntimeError("index failed"), None])

    with pytest.raises(RuntimeError, match="index failed"):
        await delete_memory_card(tmp_path, memory_id, maintain_index=maintain_index)

    assert card.exists()
    assert day_index.exists()
    assert maintain_index.await_count == 2


async def test_daily_delete_keeps_transcript_referenced_by_another_card(tmp_path):
    """同一 session 仍被其他 Daily card 引用时不得删除共享 transcript。"""
    daily = tmp_path / "daily" / "2026-08-31"
    transcript = tmp_path / "session" / "dialog" / "shared-session.jsonl"
    daily.mkdir(parents=True)
    transcript.parent.mkdir(parents=True)
    for name in ("first", "second"):
        (daily / f"{name}.md").write_text(
            "---\nsession_id: shared-session\n---\nShared fact.\n",
            encoding="utf-8",
        )
    transcript.write_text("{}\n", encoding="utf-8")
    first_id = next(item["memory_id"] for item in list_memory_cards(tmp_path)["items"] if item["title"] == "first")

    await delete_memory_card(tmp_path, first_id, maintain_index=AsyncMock())

    assert not (daily / "first.md").exists()
    assert (daily / "second.md").exists()
    assert transcript.exists()


async def test_digest_delete_is_independent_from_daily_artifacts(tmp_path):
    """删除 Digest 只删除目标摘要，不应连带 Daily 索引或 transcript。"""
    digest = tmp_path / "digest" / "procedure"
    daily_index = tmp_path / "daily" / "2026-08-31.md"
    transcript = tmp_path / "session" / "dialog" / "session-1.jsonl"
    digest.mkdir(parents=True)
    daily_index.parent.mkdir(parents=True)
    transcript.parent.mkdir(parents=True)
    (digest / "workflow.md").write_text("Reusable workflow.", encoding="utf-8")
    daily_index.write_text("daily index", encoding="utf-8")
    transcript.write_text("{}\n", encoding="utf-8")
    memory_id = list_memory_cards(tmp_path, kind="digest")["items"][0]["memory_id"]
    maintain_index = AsyncMock()

    await delete_memory_card(tmp_path, memory_id, maintain_index=maintain_index)

    assert not (digest / "workflow.md").exists()
    assert daily_index.exists()
    assert transcript.exists()
    maintain_index.assert_awaited_once_with(None)


async def test_daily_delete_rejects_session_id_path_traversal(tmp_path):
    """恶意 session_id 不能借删除 Daily card 触达 transcript 目录外文件。"""
    daily = tmp_path / "daily" / "2026-08-31"
    daily.mkdir(parents=True)
    card = daily / "fact.md"
    card.write_text("---\nsession_id: ../../outside\n---\nFact.\n", encoding="utf-8")
    outside = tmp_path / "outside.jsonl"
    outside.write_text("must remain", encoding="utf-8")
    memory_id = list_memory_cards(tmp_path)["items"][0]["memory_id"]

    await delete_memory_card(tmp_path, memory_id, maintain_index=AsyncMock())

    assert outside.read_text(encoding="utf-8") == "must remain"


def test_clear_memory_workspace_requires_exact_two_level_scope(tmp_path):
    """清空只能删除 Memory 根目录下的 uid-hash/agent-hash 两级 scope。"""
    base = tmp_path / "reme"
    scope = base / ("a" * 64) / ("b" * 64)
    scope.mkdir(parents=True)

    assert clear_memory_workspace(scope, base_dir=base) is True
    assert not scope.exists()

    unsafe = base / "single-level"
    unsafe.mkdir()
    with pytest.raises(ValueError, match="不安全"):
        clear_memory_workspace(unsafe, base_dir=base)


def test_clear_memory_workspace_rejects_non_hash_scope_segments(tmp_path):
    """即使是两级目录，也只能删除确定性 SHA-256 scope 路径。"""
    base = tmp_path / "reme"
    unsafe = base / "user-readable" / "agent-readable"
    unsafe.mkdir(parents=True)

    with pytest.raises(ValueError, match="不安全"):
        clear_memory_workspace(unsafe, base_dir=base)

    assert unsafe.is_dir()


def test_clear_memory_workspace_rejects_broken_symlink(tmp_path):
    """即使目标链接已断开，也不能把符号链接误判为不存在。"""
    base = tmp_path / "reme"
    scope = base / ("a" * 64) / ("b" * 64)
    scope.parent.mkdir(parents=True)
    scope.symlink_to(tmp_path / "missing", target_is_directory=True)

    with pytest.raises(ValueError, match="符号链接"):
        clear_memory_workspace(scope, base_dir=base)


def test_clear_memory_workspace_rejects_symlinked_uid_directory(tmp_path):
    """uid 层链接到根目录内其他 scope 时也不能通过两级路径校验。"""
    base = tmp_path / "reme"
    target = base / ("b" * 64) / ("c" * 64)
    target.mkdir(parents=True)
    linked_uid = base / ("a" * 64)
    linked_uid.symlink_to(target.parent, target_is_directory=True)
    linked_scope = linked_uid / target.name

    with pytest.raises(ValueError, match="符号链接"):
        clear_memory_workspace(linked_scope, base_dir=base)

    assert target.is_dir()


async def test_registry_close_all_attempts_every_core_when_one_close_fails(tmp_path):
    """服务关闭时单个 ReMe 异常不能阻止其他 scope 释放资源。"""
    failing = SimpleNamespace(close=AsyncMock(side_effect=RuntimeError("close failed")))
    healthy = SimpleNamespace(close=AsyncMock())
    registry = ReMeRegistry(
        base_dir=tmp_path,
        core_factory=AsyncMock(side_effect=[failing, healthy]),
    )
    for slug in ("a", "b"):
        lease = await registry.acquire_reply(
            uid="u",
            agent_slug=slug,
            fingerprint="v1",
            chat_model=object(),
            embedding_model=object(),
        )
        await lease.release()

    await registry.close_all()

    failing.close.assert_awaited_once()
    healthy.close.assert_awaited_once()


async def test_registry_core_close_failure_does_not_fail_reply_or_leak_lease(tmp_path):
    """轮换旧 core 的关闭异常不得阻断聊天或把新 scope 永久标记为 busy。"""
    old_core = SimpleNamespace(close=AsyncMock(side_effect=RuntimeError("close failed")))
    new_core = SimpleNamespace(close=AsyncMock())
    registry = ReMeRegistry(
        base_dir=tmp_path,
        core_factory=AsyncMock(side_effect=[old_core, new_core]),
    )
    old_lease = await registry.acquire_reply(
        uid="u",
        agent_slug="a",
        fingerprint="v1",
        chat_model=object(),
        embedding_model=object(),
    )
    await old_lease.release()

    new_lease = await registry.acquire_reply(
        uid="u",
        agent_slug="a",
        fingerprint="v2",
        chat_model=object(),
        embedding_model=object(),
    )

    assert new_lease.core is new_core
    assert await registry.is_busy("u", "a") is True
    await new_lease.release()
    assert await registry.is_busy("u", "a") is False


async def test_registry_exclusive_close_failure_keeps_core_for_safe_retry(tmp_path):
    """维护前关闭失败时保留 core，重试必须再次关闭后才能进入删除区段。"""
    core = SimpleNamespace(close=AsyncMock(side_effect=[RuntimeError("close failed"), None]))
    registry = ReMeRegistry(base_dir=tmp_path, core_factory=AsyncMock(return_value=core))
    lease = await registry.acquire_reply(
        uid="u",
        agent_slug="a",
        fingerprint="v1",
        chat_model=object(),
        embedding_model=object(),
    )
    await lease.release()

    with pytest.raises(RuntimeError, match="close failed"):
        async with registry.acquire_exclusive("u", "a"):
            pytest.fail("core 未成功关闭时不应进入维护区段")

    assert await registry.is_busy("u", "a") is False
    async with registry.acquire_exclusive("u", "a") as retained:
        assert retained is core

    assert core.close.await_count == 2


async def test_registry_multi_scope_close_failure_never_enters_partial_cleanup(tmp_path):
    """批量关闭中途失败时不得执行清理体，失败 core 保留给后续安全重试。"""
    closed = SimpleNamespace(close=AsyncMock())
    retryable = SimpleNamespace(close=AsyncMock(side_effect=[RuntimeError("close failed"), None]))
    registry = ReMeRegistry(
        base_dir=tmp_path,
        core_factory=AsyncMock(side_effect=[closed, retryable]),
    )
    for slug in ("a", "b"):
        lease = await registry.acquire_reply(
            uid="u",
            agent_slug=slug,
            fingerprint="v1",
            chat_model=object(),
            embedding_model=object(),
        )
        await lease.release()

    entered_cleanup = False
    with pytest.raises(RuntimeError, match="close failed"):
        async with registry.acquire_exclusive_many([("u", "a"), ("u", "b")]):
            entered_cleanup = True

    assert entered_cleanup is False
    async with registry.acquire_exclusive_many([("u", "a"), ("u", "b")]) as retained:
        assert retained == {("u", "b"): retryable}

    closed.close.assert_awaited_once()
    assert retryable.close.await_count == 2


async def test_middleware_cancellation_releases_reply_lease(tmp_path):
    """聊天取消必须透传 CancelledError，并在 finally 中释放 reply lease。"""

    class CancellingCore:
        async def on_reply(self, _agent, _input_kwargs, _handler):
            if False:
                yield None
            raise asyncio.CancelledError

        async def close(self):
            return None

    async def next_handler(**_kwargs):
        yield "unused"

    registry = ReMeRegistry(
        base_dir=tmp_path,
        core_factory=AsyncMock(return_value=CancellingCore()),
    )
    middleware = ScopedReMeMiddleware(
        registry=registry,
        uid="u",
        agent_slug="a",
        fingerprint="v1",
        chat_model=object(),
        embedding_model=object(),
    )

    with pytest.raises(asyncio.CancelledError):
        await anext(middleware.on_reply(object(), {}, next_handler))

    assert await registry.is_busy("u", "a") is False


def test_memory_listing_hides_archived_duplicates_and_exposes_canonical_sources(tmp_path):
    """管理列表默认隐藏归档来源，并展示 canonical 聚合的来源数量。"""
    daily = tmp_path / "daily" / "2026-09-01"
    digest = tmp_path / "digest" / "personal"
    daily.mkdir(parents=True)
    digest.mkdir(parents=True)
    (daily / "canonical.md").write_text(
        "---\n"
        "name: 星砂岛 PC 性能监控数据获取\n"
        "description: 跨会话归并后的稳定主题\n"
        "yuxi_memory_status: canonical\n"
        "yuxi_source_paths:\n"
        "  - daily/2026-09-01/source-a.md\n"
        "  - daily/2026-09-01/source-b.md\n"
        "---\n"
        "用户反复请求获取同一批性能监控数据。\n",
        encoding="utf-8",
    )
    (daily / "source-a.md").write_text(
        "---\n"
        "name: 原始请求 A\n"
        "session_id: session-a\n"
        "yuxi_memory_status: archived_duplicate\n"
        "yuxi_canonical_path: daily/2026-09-01/canonical.md\n"
        "---\n"
        "原始会话内容。\n",
        encoding="utf-8",
    )
    (digest / "stable.md").write_text("Stable digest.", encoding="utf-8")

    listing = list_memory_cards(tmp_path)

    assert listing["pagination"]["total"] == 2
    assert {item["title"] for item in listing["items"]} == {
        "星砂岛 PC 性能监控数据获取",
        "stable",
    }
    canonical = next(item for item in listing["items"] if item["kind"] == "daily")
    assert canonical["source_count"] == 2


async def test_deleting_canonical_keeps_archived_sources_and_transcripts(tmp_path):
    """删除 canonical 只移除聚合卡片，不删除来源 Daily 或会话 transcript。"""
    daily = tmp_path / "daily" / "2026-09-01"
    transcript = tmp_path / "session" / "dialog" / "source-session.jsonl"
    daily.mkdir(parents=True)
    transcript.parent.mkdir(parents=True)
    canonical = daily / "canonical.md"
    canonical.write_text(
        "---\n"
        "name: Canonical\n"
        "yuxi_memory_status: canonical\n"
        "yuxi_source_paths:\n"
        "  - daily/2026-09-01/source.md\n"
        "---\n"
        "Merged memory.\n",
        encoding="utf-8",
    )
    source = daily / "source.md"
    source.write_text(
        "---\nsession_id: source-session\nyuxi_memory_status: archived_duplicate\n---\nOriginal memory.\n",
        encoding="utf-8",
    )
    transcript.write_text("{}\n", encoding="utf-8")
    memory_id = list_memory_cards(tmp_path)["items"][0]["memory_id"]

    await delete_memory_card(tmp_path, memory_id, maintain_index=AsyncMock())

    assert not canonical.exists()
    assert source.exists()
    assert transcript.exists()
