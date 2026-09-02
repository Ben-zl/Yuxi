"""Yuxi 对 ReMe 长期记忆的 scope、生命周期与运行时适配。"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Any

import frontmatter
from agentscope.middleware import MiddlewareBase
from agentscope.credential import CredentialFactory
from agentscope.message import AssistantMsg, HintBlock, Msg
from agentscope.tool import FunctionTool

from yuxi.agentscope.memory_compaction import (
    compact_daily_memories,
    detect_memory_topic_keys,
    sanitize_daily_runtime_state,
    strip_transient_memory_state,
)
from yuxi.utils import logger
from yuxi.utils.datetime_utils import shanghai_now


AUTO_MEMORY_EXTRACTION_HINT = """提取长期记忆时只记录可跨对话复用的稳定事实、明确结论和稳定标识。
禁止记录 pending、派发、等待回报、执行中、临时重试、一次性时间戳或承诺交付等运行中状态。
对测试项目代号只原样记录用户明确给出的代号，不得推断其环境性质、生产属性或其他未明确信息。
如果对话只有运行状态而没有稳定事实，请不要创建长期记忆。"""


def _memory_extraction_messages(messages: list[Msg]) -> list[dict[str, Any]]:
    """构造 ReMe 提取输入，用户原文不变，assistant 仅保留稳定事实。"""
    result = []
    for message in messages:
        payload = message.model_dump(mode="json")
        if message.role == "assistant":
            content = []
            for block in payload.get("content", []):
                if block.get("type") != "text":
                    content.append(block)
                    continue
                text = strip_transient_memory_state(str(block.get("text") or ""))
                if text:
                    content.append({**block, "text": text})
            payload["content"] = content
            if not content:
                continue
        result.append(payload)
    return result


class MemoryScopeBusyError(RuntimeError):
    """记忆 scope 正在执行对话或维护，当前操作不能安全继续。"""


def _scope_hash(value: str) -> str:
    """把业务标识转换为不泄露原值的稳定目录名。"""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def memory_scope_identity(uid: str, agent_slug: str) -> str:
    """返回用户与 Agent 组合 scope 的确定性标识。"""
    return _scope_hash(f"{uid}\0{agent_slug}")


def memory_workspace_path(base_dir: str | Path, uid: str, agent_slug: str) -> Path:
    """返回 scope 的 ReMe Workspace，路径中不包含原始业务标识。"""
    return Path(base_dir) / _scope_hash(uid) / _scope_hash(agent_slug)


def validate_memory_workspace(
    base_dir: str | Path,
    uid: str,
    agent_slug: str,
    *,
    create: bool = False,
) -> Path:
    """校验并可选创建 scope 目录，逐层拒绝符号链接和路径逃逸。"""
    base = Path(base_dir)
    if base.is_symlink() or not base.is_dir():
        raise ValueError("Memory 根目录必须是真实目录")
    resolved_base = base.resolve()
    current = resolved_base
    names = (_scope_hash(uid), _scope_hash(agent_slug))

    for index, name in enumerate(names):
        candidate = current / name
        if candidate.is_symlink():
            raise ValueError("Memory Workspace 路径不能包含符号链接")
        if candidate.exists():
            if not candidate.is_dir():
                raise ValueError("Memory Workspace 路径必须是真实目录")
        elif create:
            candidate.mkdir()
        else:
            return current.joinpath(*names[index:])

        resolved = candidate.resolve()
        try:
            resolved.relative_to(resolved_base)
        except ValueError as exc:
            raise ValueError("Memory Workspace 不在配置根目录下") from exc
        current = resolved
    return current


def ensure_memory_base_dir(base_dir: str | Path) -> Path:
    """创建并校验长期记忆根目录，拒绝符号链接和普通文件。"""
    path = Path(base_dir)
    path.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or not path.is_dir():
        raise ValueError("Memory 根目录必须是真实目录")
    return path.resolve()


@dataclass
class _RegistryEntry:
    core: Any
    fingerprint: str
    active_replies: int
    last_used_at: float


class ReplyLease:
    """一次 reply 对共享 ReMe core 的引用计数租约。"""

    def __init__(self, registry: ReMeRegistry, key: tuple[str, str], core: Any) -> None:
        self._registry = registry
        self._key = key
        self.core = core
        self._released = False

    async def release(self) -> None:
        """释放租约；重复调用保持幂等。"""
        if self._released:
            return
        self._released = True
        await self._registry._release_reply(self._key)


CoreFactory = Callable[..., Awaitable[Any] | Any]


class YuxiReMeCore:
    """通过 ReMe 公共 Job API 完成召回治理和逐轮自动写回。"""

    def __init__(self, *, app: Any, workspace_dir: str | Path, top_k: int = 5) -> None:
        self.app = app
        self.workspace_dir = Path(workspace_dir).resolve()
        self.top_k = top_k
        self._retrieval_tasks: dict[str | None, asyncio.Task] = {}

    async def close(self) -> None:
        """关闭当前 scope 内嵌的 ReMe 应用。"""
        await self.app.close()

    async def _run_job(self, name: str, **kwargs: Any) -> Any:
        """执行 ReMe 公共 Job，并统一处理 success=false。"""
        response = await self.app.run_job(name, **kwargs)
        if getattr(response, "success", True) is False:
            raise RuntimeError(str(getattr(response, "answer", f"ReMe {name} failed")))
        return response

    async def _govern_after_auto_memory(self) -> None:
        """治理本轮新 Daily，并在治理完成后刷新完整索引。"""
        memory_date = shanghai_now().date().isoformat()
        sanitize_daily_runtime_state(self.workspace_dir, date=memory_date)
        await compact_daily_memories(
            self.workspace_dir,
            date=memory_date,
            maintain_index=lambda _daily_date: None,
        )
        await self._run_job("reindex")

    def _candidate_metadata(self, relative_path: str) -> tuple[str | None, str | None]:
        """读取候选的 Yuxi 状态和主题键，拒绝链接及路径逃逸。"""
        parts = PurePosixPath(relative_path).parts
        is_daily_card = len(parts) == 3 and parts[0] == "daily" and parts[2].endswith(".md")
        if is_daily_card:
            try:
                date.fromisoformat(parts[1])
            except ValueError:
                is_daily_card = False
        is_digest_card = (
            len(parts) >= 3
            and parts[0] == "digest"
            and parts[1] in {"personal", "procedure", "wiki"}
            and parts[-1].endswith(".md")
        )
        if not is_daily_card and not is_digest_card:
            return "archived_duplicate", None
        path = self.workspace_dir / relative_path
        try:
            if path.is_symlink() or not path.is_file():
                return "archived_duplicate", None
            path.resolve().relative_to(self.workspace_dir)
            metadata = frontmatter.loads(path.read_text(encoding="utf-8")).metadata
        except (OSError, UnicodeError, ValueError):
            return "archived_duplicate", None
        status = str(metadata.get("yuxi_memory_status") or "").strip() or None
        topic_key = str(metadata.get("yuxi_topic_key") or "").strip() or None
        return status, topic_key

    @staticmethod
    def _state_rank(text: str) -> int:
        """把候选中的运行状态归一为 completed > failed > running > pending。"""
        value = text.lower()
        if any(marker in value for marker in ("已完成", "completed", "succeeded", "成功完成")):
            return 3
        if any(marker in value for marker in ("失败", "failed", "error", "异常")):
            return 2
        if any(marker in value for marker in ("running", "执行中", "处理中")):
            return 1
        return 0

    async def search(self, query: str, *, limit: int | None = None) -> list[str]:
        """召回并过滤归档来源，同主题只保留最稳定、最新鲜的候选。"""
        requested = self.top_k if limit is None else max(1, min(limit, self.top_k))
        response = await self._run_job("search", query=query, limit=max(requested * 3, requested))
        results = getattr(response, "metadata", {}).get("results", [])
        by_path: dict[str, dict[str, Any]] = {}
        for candidate in results:
            path = str(candidate.get("path") or "")
            text = str(candidate.get("text") or "").strip()
            if not path or not text or path in by_path:
                continue
            status, topic_key = self._candidate_metadata(path)
            if status == "archived_duplicate":
                continue
            kind_rank = 3 if path.startswith("digest/") else 2 if status == "canonical" else 1
            score = float((candidate.get("scores") or {}).get("score") or 0.0)
            detected_keys = detect_memory_topic_keys(text)
            by_path[path] = {
                "path": path,
                "text": text,
                "topic_key": topic_key or (sorted(detected_keys)[0] if len(detected_keys) == 1 else path),
                "rank": (kind_rank, self._state_rank(text), score),
            }

        by_topic: dict[str, dict[str, Any]] = {}
        for candidate in by_path.values():
            key = candidate["topic_key"]
            current = by_topic.get(key)
            if current is None or candidate["rank"] > current["rank"]:
                by_topic[key] = candidate
        ranked = sorted(by_topic.values(), key=lambda item: item["rank"], reverse=True)
        return [item["text"] for item in ranked[:requested]]

    async def list_tools(self) -> list:
        """只注册一个经过 Yuxi 过滤的 memory_search 工具。"""

        async def memory_search(query: str, limit: int = 5) -> str:
            """搜索当前用户在此智能体范围内的长期记忆。"""
            memories = await self.search(query, limit=limit)
            return "\n\n".join(memories) if memories else "未找到相关长期记忆。"

        return [
            FunctionTool(
                memory_search,
                name="memory_search",
                description="搜索当前用户在此智能体不同对话间共享的长期记忆",
                is_read_only=True,
            ),
        ]

    async def on_system_prompt(self, _agent: Any, current_prompt: str) -> str:
        """提示模型仅在需要时使用唯一的长期记忆搜索工具。"""
        return f"{current_prompt}\n\n需要跨对话事实时可调用 memory_search；不得把记忆中的 pending 状态当作当前事实。"

    @staticmethod
    def _session_id(agent: Any) -> str | None:
        """从 AgentScope 公开 state 中读取当前 Session。"""
        value = getattr(getattr(agent, "state", None), "session_id", None)
        return str(value) if value else None

    @staticmethod
    def _query_text(inputs: Any) -> str:
        """从 AgentScope 输入中提取本轮检索文本。"""
        if isinstance(inputs, str):
            return inputs.strip()
        if isinstance(inputs, Msg):
            return inputs.get_text_content().strip()
        if isinstance(inputs, (list, tuple)):
            return "\n".join(filter(None, (YuxiReMeCore._query_text(item) for item in inputs))).strip()
        return ""

    async def on_reply(self, agent: Any, input_kwargs: dict, next_handler: Callable[..., AsyncGenerator]):
        """并行启动召回，主回复结束后把本轮增量写入 ReMe。"""
        session_id = self._session_id(agent)
        query_text = self._query_text(input_kwargs.get("inputs"))
        stale = self._retrieval_tasks.pop(session_id, None)
        if stale is not None and not stale.done():
            stale.cancel()
        if query_text:
            self._retrieval_tasks[session_id] = asyncio.create_task(self.search(query_text))
        context = getattr(getattr(agent, "state", None), "context", [])
        pre_ids = {message.id for message in context if isinstance(message, Msg)}
        try:
            async for item in next_handler(**input_kwargs):
                yield item
        finally:
            task = self._retrieval_tasks.pop(session_id, None)
            if task is not None and not task.done():
                task.cancel()
            if task is not None:
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
            increment = [
                message
                for message in context
                if isinstance(message, Msg)
                and message.id not in pre_ids
                and getattr(message, "name", None) != "yuxi_reme_memory"
            ]
            has_assistant = any(message.role == "assistant" and message.get_text_content() for message in increment)
            if query_text and session_id and has_assistant:
                try:
                    await self._run_job(
                        "auto_memory",
                        messages=_memory_extraction_messages(increment),
                        session_id=session_id,
                        memory_hint=AUTO_MEMORY_EXTRACTION_HINT,
                    )
                    await self._govern_after_auto_memory()
                except Exception as exc:  # noqa: BLE001 - 记忆写回不得阻断聊天
                    logger.warning("ReMe auto_memory failed for session_id=%s: %s", session_id, exc)

    async def on_reasoning(self, agent: Any, input_kwargs: dict, next_handler: Callable[..., AsyncGenerator]):
        """在推理安全点注入已完成的治理后召回结果。"""
        task = self._retrieval_tasks.get(self._session_id(agent))
        if task is not None and task.done():
            self._retrieval_tasks.pop(self._session_id(agent), None)
            try:
                memories = task.result()
            except (asyncio.CancelledError, Exception) as exc:  # noqa: BLE001
                logger.warning("ReMe search failed: %s", exc)
                memories = []
            if memories:
                content = "长期记忆（可能过时，请结合当前对话核验）：\n" + "\n".join(
                    f"- {memory}" for memory in memories
                )
                agent.state.context.append(
                    AssistantMsg(name="yuxi_reme_memory", content=[HintBlock(hint=content)]),
                )
        async for event in next_handler(**input_kwargs):
            yield event


async def _default_core_factory(*, workspace_dir: Path, chat_model: Any, embedding_model: Any) -> Any:
    """使用 ReMe 公共应用 API 构造带候选治理的 scope core。"""
    app = await _create_reme_app(
        workspace_dir=workspace_dir,
        chat_model=chat_model,
        embedding_model=embedding_model,
    )
    return YuxiReMeCore(app=app, workspace_dir=workspace_dir, top_k=5)


class ReMeRegistry:
    """按用户和 Agent 共享 ReMe core，并协调 reply 与破坏性维护。"""

    def __init__(
        self,
        *,
        base_dir: str | Path,
        core_factory: CoreFactory = _default_core_factory,
        max_entries: int = 100,
        idle_ttl_seconds: float = 1800,
    ) -> None:
        self.base_dir = Path(base_dir)
        self._core_factory = core_factory
        self._max_entries = max_entries
        self._idle_ttl_seconds = idle_ttl_seconds
        self._entries: dict[tuple[str, str], _RegistryEntry] = {}
        self._lock = asyncio.Lock()
        self._condition = asyncio.Condition(self._lock)
        self._exclusive_keys: set[tuple[str, str]] = set()

    async def _get_or_create_locked(
        self,
        *,
        key: tuple[str, str],
        fingerprint: str,
        chat_model: Any,
        embedding_model: Any,
    ) -> tuple[_RegistryEntry, list[Any]]:
        """在 Registry 锁内取得当前 core，并返回需要异步关闭的旧 core。"""
        close_after: list[Any] = []
        entry = self._entries.get(key)
        replaced_core = None
        if entry is not None and entry.fingerprint != fingerprint:
            if entry.active_replies:
                raise RuntimeError("活动 reply 未等待完成就尝试轮换 Memory core")
            replaced_core = entry.core
            entry = None

        if entry is None:
            workspace = validate_memory_workspace(self.base_dir, *key, create=True)
            core = self._core_factory(
                workspace_dir=workspace,
                chat_model=chat_model,
                embedding_model=embedding_model,
            )
            if inspect.isawaitable(core):
                core = await core
            entry = _RegistryEntry(
                core=core,
                fingerprint=fingerprint,
                active_replies=0,
                last_used_at=time.monotonic(),
            )
            self._entries[key] = entry
            if replaced_core is not None:
                close_after.append(replaced_core)
        return entry, close_after

    async def get_core(
        self,
        *,
        uid: str,
        agent_slug: str,
        fingerprint: str,
        chat_model: Any,
        embedding_model: Any,
    ) -> Any:
        """为工具发现取得 core，但不把尚未开始的 reply 标记为活动。"""
        key = (uid, agent_slug)
        async with self._condition:
            while True:
                if key in self._exclusive_keys:
                    raise MemoryScopeBusyError("memory_scope_busy")
                current = self._entries.get(key)
                if current is None or current.fingerprint == fingerprint or current.active_replies == 0:
                    break
                await self._condition.wait()
            entry, close_after = await self._get_or_create_locked(
                key=key,
                fingerprint=fingerprint,
                chat_model=chat_model,
                embedding_model=embedding_model,
            )
            entry.last_used_at = time.monotonic()
            close_after.extend(self._evict_locked(exclude=key))

        await self._close_cores(close_after, suppress_errors=True)
        return entry.core

    async def acquire_reply(
        self,
        *,
        uid: str,
        agent_slug: str,
        fingerprint: str,
        chat_model: Any,
        embedding_model: Any,
    ) -> ReplyLease:
        """取得 reply 租约；相同 scope 的首次创建通过锁保持 single-flight。"""
        key = (uid, agent_slug)
        async with self._condition:
            while True:
                if key in self._exclusive_keys:
                    raise MemoryScopeBusyError("memory_scope_busy")
                current = self._entries.get(key)
                if current is None or current.fingerprint == fingerprint or current.active_replies == 0:
                    break
                await self._condition.wait()
            entry, close_after = await self._get_or_create_locked(
                key=key,
                fingerprint=fingerprint,
                chat_model=chat_model,
                embedding_model=embedding_model,
            )
            entry.active_replies += 1
            entry.last_used_at = time.monotonic()
            close_after.extend(self._evict_locked(exclude=key))

        await self._close_cores(close_after, suppress_errors=True)
        return ReplyLease(self, key, entry.core)

    async def _release_reply(self, key: tuple[str, str]) -> None:
        """递减 scope 活动 reply 计数。"""
        async with self._condition:
            entry = self._entries.get(key)
            if entry is None:
                return
            entry.active_replies = max(entry.active_replies - 1, 0)
            entry.last_used_at = time.monotonic()
            self._condition.notify_all()

    @asynccontextmanager
    async def acquire_exclusive(self, uid: str, agent_slug: str):
        """关闭并独占 scope；活动 reply 存在时显式返回 busy。"""
        async with self.acquire_exclusive_many([(uid, agent_slug)]) as cores:
            yield cores.get((uid, agent_slug))

    @asynccontextmanager
    async def acquire_inspection(self, uid: str, agent_slug: str):
        """在 scope 空闲时短暂阻止 reply 和维护，以便一致读取卡片。"""
        key = (uid, agent_slug)
        async with self._condition:
            entry = self._entries.get(key)
            if key in self._exclusive_keys or (entry is not None and entry.active_replies):
                raise MemoryScopeBusyError("memory_scope_busy")
            self._exclusive_keys.add(key)
        try:
            yield
        finally:
            async with self._condition:
                self._exclusive_keys.discard(key)
                self._condition.notify_all()

    @asynccontextmanager
    async def acquire_exclusive_many(self, scopes: list[tuple[str, str]]):
        """原子占用多个 scope，供 Agent/User 批量清理避免中途竞态。"""
        keys = list(dict.fromkeys(scopes))
        cores: dict[tuple[str, str], Any] = {}
        async with self._condition:
            for key in keys:
                entry = self._entries.get(key)
                if key in self._exclusive_keys or (entry is not None and entry.active_replies):
                    raise MemoryScopeBusyError("memory_scope_busy")
            self._exclusive_keys.update(keys)
            for key in keys:
                entry = self._entries.get(key)
                if entry is not None:
                    cores[key] = entry.core
        try:
            for key, core in cores.items():
                await core.close()
                async with self._condition:
                    current = self._entries.get(key)
                    if current is not None and current.core is core:
                        self._entries.pop(key)
            yield cores
        finally:
            async with self._condition:
                self._exclusive_keys.difference_update(keys)
                self._condition.notify_all()

    def _evict_locked(self, *, exclude: tuple[str, str]) -> list[Any]:
        """在 Registry 锁内选出过期和超限的空闲 core。"""
        now = time.monotonic()
        candidates = sorted(
            ((key, entry) for key, entry in self._entries.items() if key != exclude and entry.active_replies == 0),
            key=lambda item: item[1].last_used_at,
        )
        remove: list[tuple[str, str]] = [
            key for key, entry in candidates if now - entry.last_used_at >= self._idle_ttl_seconds
        ]
        remaining = len(self._entries) - len(remove)
        for key, _entry in candidates:
            if remaining <= self._max_entries:
                break
            if key not in remove:
                remove.append(key)
                remaining -= 1
        cores = [self._entries.pop(key).core for key in remove]
        return cores

    async def close_all(self) -> None:
        """关闭并移除所有无论是否已启动的 ReMe core。"""
        async with self._lock:
            entries = list(self._entries.values())
            self._entries.clear()
        await self._close_cores([entry.core for entry in entries], suppress_errors=True)

    @staticmethod
    async def _close_cores(cores: list[Any], *, suppress_errors: bool = False) -> None:
        """并发关闭全部 core；服务退出时记录错误但继续释放其他 scope。"""
        if not cores:
            return
        results = await asyncio.gather(*(core.close() for core in cores), return_exceptions=True)
        errors = [result for result in results if isinstance(result, BaseException)]
        if not errors:
            return
        if suppress_errors:
            for error in errors:
                logger.warning("ReMe core close failed: %s", error)
            return
        raise errors[0]

    async def is_busy(self, uid: str, agent_slug: str) -> bool:
        """返回 scope 是否存在活动 reply 或独占维护。"""
        async with self._lock:
            key = (uid, agent_slug)
            entry = self._entries.get(key)
            return key in self._exclusive_keys or bool(entry and entry.active_replies)


class ScopedReMeMiddleware(MiddlewareBase):
    """每轮按 scope 获取共享 core，并把 ReMe 故障隔离在聊天主链路外。"""

    def __init__(
        self,
        *,
        registry: ReMeRegistry,
        uid: str,
        agent_slug: str,
        fingerprint: str,
        chat_model: Any,
        embedding_model: Any,
        on_memory_updated: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._registry = registry
        self._uid = uid
        self._agent_slug = agent_slug
        self._fingerprint = fingerprint
        self._chat_model = chat_model
        self._embedding_model = embedding_model
        self._on_memory_updated = on_memory_updated
        self._core: Any | None = None
        self._lease: ReplyLease | None = None

    async def _ensure_core(self) -> Any:
        """取得工具发现和系统提示所需 core，不提前占用 reply lease。"""
        if self._core is None:
            self._core = await self._registry.get_core(
                uid=self._uid,
                agent_slug=self._agent_slug,
                fingerprint=self._fingerprint,
                chat_model=self._chat_model,
                embedding_model=self._embedding_model,
            )
        return self._core

    async def _ensure_lease(self) -> ReplyLease:
        """延迟取得租约，使工具发现和 reply 共用同一 scope core。"""
        if self._lease is None:
            self._lease = await self._registry.acquire_reply(
                uid=self._uid,
                agent_slug=self._agent_slug,
                fingerprint=self._fingerprint,
                chat_model=self._chat_model,
                embedding_model=self._embedding_model,
            )
            self._core = self._lease.core
        return self._lease

    async def list_tools(self) -> list:
        """仅通过 Middleware 暴露一次 memory_search。"""
        try:
            return await (await self._ensure_core()).list_tools()
        except Exception as exc:  # noqa: BLE001 - 工具发现失败不得阻断聊天
            logger.warning("ReMe tool discovery failed for %s/%s: %s", self._uid, self._agent_slug, exc)
            return []

    async def on_reply(
        self,
        agent: Any,
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator:
        """运行 ReMe reply hook；故障时回退到原始聊天处理器。"""
        try:
            lease = await self._ensure_lease()
        except MemoryScopeBusyError:
            async for item in next_handler(**input_kwargs):
                yield item
            return
        emitted = False
        completed = False
        try:
            try:
                async for item in lease.core.on_reply(agent, input_kwargs, next_handler):
                    emitted = True
                    yield item
                completed = True
            except Exception as exc:  # noqa: BLE001 - 记忆不得阻断聊天
                logger.warning("ReMe reply failed for %s/%s: %s", self._uid, self._agent_slug, exc)
                if not emitted:
                    async for item in next_handler(**input_kwargs):
                        yield item
            if completed and self._on_memory_updated is not None:
                try:
                    await self._on_memory_updated()
                except Exception as exc:  # noqa: BLE001 - catalog 观测不得阻断聊天
                    logger.warning("ReMe catalog update failed for %s/%s: %s", self._uid, self._agent_slug, exc)
        finally:
            await lease.release()
            self._lease = None

    async def on_reasoning(self, agent: Any, input_kwargs: dict, next_handler: Callable[..., AsyncGenerator]):
        """把推理阶段交给当前 ReMe core；缺少租约时保持原行为。"""
        if self._lease is None:
            async for item in next_handler(**input_kwargs):
                yield item
            return
        emitted = False
        try:
            async for item in self._lease.core.on_reasoning(agent, input_kwargs, next_handler):
                emitted = True
                yield item
        except Exception as exc:  # noqa: BLE001
            logger.warning("ReMe retrieval failed for %s/%s: %s", self._uid, self._agent_slug, exc)
            if not emitted:
                async for item in next_handler(**input_kwargs):
                    yield item

    async def on_system_prompt(self, agent: Any, current_prompt: str) -> str:
        """追加 ReMe 工具说明；异常时保持原系统提示词。"""
        try:
            return await (await self._ensure_core()).on_system_prompt(agent, current_prompt)
        except Exception as exc:  # noqa: BLE001
            logger.warning("ReMe prompt setup failed for %s/%s: %s", self._uid, self._agent_slug, exc)
            return current_prompt


def build_memory_models(projection: Any) -> tuple[Any, Any, str]:
    """由运行时投影构造 ReMe 的 Chat/Embedding 模型和轮换指纹。"""
    from yuxi.agentscope.runtime_models import register_yuxi_credentials

    register_yuxi_credentials()
    chat = projection.memory_chat_model_config
    embedding = projection.memory_embedding_model_config
    if not chat or not embedding:
        raise ValueError("Memory 模型投影不完整")

    chat_credential = CredentialFactory.from_dict(chat["credential_data"])
    if not chat_credential.api_key.get_secret_value().isascii():
        raise ValueError("Memory Chat 模型 API Key 必须是 ASCII 字符")
    chat_model_cls = chat_credential.get_chat_model_class()
    chat_model_config = chat["model_config"]
    chat_parameters = (
        chat_model_cls.Parameters(**chat_model_config.get("parameters", {}))
        if chat_model_config.get("parameters")
        else None
    )
    chat_model = chat_model_cls(
        credential=chat_credential,
        model=chat_model_config["model"],
        parameters=chat_parameters,
    )

    embedding_credential = CredentialFactory.from_dict(embedding["credential_data"])
    if not embedding_credential.api_key.get_secret_value().isascii():
        raise ValueError("Memory Embedding 模型 API Key 必须是 ASCII 字符")
    embedding_model_cls = embedding_credential.get_embedding_model_class()
    embedding_model = embedding_model_cls(
        credential=embedding_credential,
        model=embedding["model"],
        dimensions=embedding["dimensions"],
        embedding_base_url=embedding["base_url"],
    )

    fingerprint_payload = {
        "chat": chat,
        "embedding": embedding,
    }
    fingerprint = hashlib.sha256(
        json.dumps(fingerprint_payload, sort_keys=True, ensure_ascii=True).encode("utf-8"),
    ).hexdigest()
    return chat_model, embedding_model, fingerprint


@asynccontextmanager
async def reme_maintenance_app(*, workspace_dir: Path, chat_model: Any, embedding_model: Any):
    """使用 ReMe 公共 API 构造短生命周期 Dream/Reindex 应用。"""

    app = await _create_reme_app(
        workspace_dir=workspace_dir,
        chat_model=chat_model,
        embedding_model=embedding_model,
    )
    try:
        yield app
    finally:
        await app.close()


async def _create_reme_app(*, workspace_dir: Path, chat_model: Any, embedding_model: Any) -> Any:
    """构造并启动只开放 Yuxi 所需公开 Job 的 ReMe 应用。"""
    from reme import ReMe
    from reme.config import resolve_app_config

    config = resolve_app_config(
        log_config=False,
        workspace_dir=str(workspace_dir),
        enable_logo=False,
        log_to_file=False,
        log_to_console=False,
    )
    allowed_jobs = {
        "auto_memory",
        "auto_dream",
        "search",
        "node_search",
        "reindex",
        "daily_list",
        "daily_reindex",
        "frontmatter_read",
        "frontmatter_update",
        "move",
        "read",
        "write",
        "daily_write",
        "edit",
        "delete",
    }
    config["jobs"] = {name: value for name, value in config["jobs"].items() if name in allowed_jobs}
    components = config["components"]
    components["as_embedding"] = {
        "default": {
            "backend": "openai",
            "model": embedding_model.model,
            "dimensions": embedding_model.dimensions,
            "credential": {"api_key": "injected"},
        },
    }
    components["embedding_store"] = {
        "default": {"backend": "local", "as_embedding": "default"},
    }
    components["file_store"]["default"]["embedding_store"] = "default"

    app = ReMe(**config)
    await app.update_component("as_llm", "default", model=chat_model)
    await app.update_component("as_embedding", "default", model=embedding_model)
    await app.start()
    return app
