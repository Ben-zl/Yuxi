"""ReMe 长期记忆 scope、目录与并发租约测试。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from yuxi.agentscope.memory import (
    MemoryScopeBusyError,
    ReMeRegistry,
    ScopedReMeMiddleware,
    memory_scope_identity,
    memory_workspace_path,
)
from yuxi.agentscope.memory_management import (
    clear_memory_workspace,
    delete_memory_card,
    list_memory_cards,
)

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


async def test_registry_single_flight_and_exclusive_busy(tmp_path):
    """并发创建只构造一个 core，活动 reply 阻止破坏性维护。"""
    core = SimpleNamespace(close=AsyncMock(), list_tools=AsyncMock(return_value=[]))
    factory = AsyncMock(return_value=core)
    registry = ReMeRegistry(base_dir=tmp_path, core_factory=factory, max_entries=10)

    lease_a, lease_b = (
        await registry.acquire_reply(
            uid="u",
            agent_slug="a",
            fingerprint="model-v1",
            chat_model=object(),
            embedding_model=object(),
        ),
        await registry.acquire_reply(
            uid="u",
            agent_slug="a",
            fingerprint="model-v1",
            chat_model=object(),
            embedding_model=object(),
        ),
    )

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


def test_clear_memory_workspace_rejects_broken_symlink(tmp_path):
    """即使目标链接已断开，也不能把符号链接误判为不存在。"""
    base = tmp_path / "reme"
    scope = base / ("a" * 64) / ("b" * 64)
    scope.parent.mkdir(parents=True)
    scope.symlink_to(tmp_path / "missing", target_is_directory=True)

    with pytest.raises(ValueError, match="真实目录"):
        clear_memory_workspace(scope, base_dir=base)


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
