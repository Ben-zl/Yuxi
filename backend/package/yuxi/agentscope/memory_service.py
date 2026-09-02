"""AgentScope 内部长期记忆管理用例。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from yuxi.agentscope.config_projection import project_runtime
from yuxi.agentscope.memory import (
    MemoryScopeBusyError,
    build_memory_models,
    reme_maintenance_app,
    validate_memory_workspace,
)
from yuxi.agentscope.memory_management import (
    clear_memory_workspace,
    delete_memory_card,
    list_memory_cards,
)
from yuxi.config import UserConfig
from yuxi.repositories.agent_memory_scope_repository import AgentMemoryScopeRepository
from yuxi.storage.postgres.manager import pg_manager


class AgentMemoryService:
    """协调 Registry、ReMe 索引、文件和 scope catalog。"""

    def __init__(self, *, registry, base_dir: str | Path) -> None:
        self.registry = registry
        self.base_dir = Path(base_dir)

    async def list_memories(
        self,
        uid: str,
        agent_slug: str,
        *,
        kind: str,
        category: str,
        page: int,
        page_size: int,
    ) -> dict:
        """仅在 scope 空闲时读取稳定的卡片快照。"""
        async with self.registry.acquire_inspection(uid, agent_slug):
            workspace = validate_memory_workspace(self.base_dir, uid, agent_slug)
            result = await asyncio.to_thread(
                list_memory_cards,
                workspace,
                kind=kind,
                category=category,
                page=page,
                page_size=page_size,
            )

        async with pg_manager.get_async_session_context() as db:
            record = await AgentMemoryScopeRepository(db).get(uid, agent_slug)
            enabled = (await UserConfig.load(db, uid)).schema.enable_memory
        record_state = record.to_dict() if record else {}
        result["scope"] = {
            "enabled": bool(enabled),
            "last_memory_at": record_state.get("last_memory_at"),
            "last_dream_date": record_state.get("last_dream_date"),
            "dream_status": record.dream_status if record else None,
        }
        return result

    async def delete_item(self, uid: str, agent_slug: str, memory_id: str) -> None:
        """独占 scope 删除一张卡片，并在提交删除前重建索引。"""
        async with self.registry.acquire_exclusive(uid, agent_slug):
            workspace = validate_memory_workspace(self.base_dir, uid, agent_slug)
            await delete_memory_card(
                workspace,
                memory_id,
                maintain_index=lambda daily_date: self.reindex_scope(
                    uid,
                    agent_slug,
                    workspace,
                    daily_date=daily_date,
                ),
            )

    async def clear_scope(self, uid: str, agent_slug: str) -> bool:
        """独占并幂等清空一个用户与 Agent scope。"""
        async with self.registry.acquire_exclusive(uid, agent_slug):
            return await self._delete_scope_data(uid, agent_slug)

    async def clear_agent(self, agent_slug: str) -> dict:
        """清理一个 Agent 在所有用户下的长期记忆。"""
        async with pg_manager.get_async_session_context() as db:
            records = await AgentMemoryScopeRepository(db).list_for_agent(agent_slug)
        return await self._clear_records(records)

    async def clear_user(self, uid: str) -> dict:
        """清理一个用户在所有 Agent 下的长期记忆。"""
        async with pg_manager.get_async_session_context() as db:
            records = await AgentMemoryScopeRepository(db).list_for_user(uid)
        return await self._clear_records(records)

    async def _load_projection(self, uid: str, agent_slug: str):
        """为维护任务解析模型，即使用户当前已关闭 Memory。"""
        async with pg_manager.get_async_session_context() as db:
            return await project_runtime(
                db,
                uid=uid,
                agent_slug=agent_slug,
                include_memory_models=True,
            )

    async def reindex_scope(
        self,
        uid: str,
        agent_slug: str,
        workspace: Path,
        *,
        daily_date: str | None = None,
    ) -> None:
        """使用短生命周期 ReMe 应用完整重建一个 scope 的混合索引。"""
        projection = await self._load_projection(uid, agent_slug)
        chat_model, embedding_model, _fingerprint = build_memory_models(projection)
        async with reme_maintenance_app(
            workspace_dir=workspace,
            chat_model=chat_model,
            embedding_model=embedding_model,
        ) as maintenance:
            if daily_date is not None:
                response = await maintenance.run_job("daily_reindex", date=daily_date)
                if getattr(response, "success", True) is False:
                    raise RuntimeError(f"ReMe daily reindex failed: {getattr(response, 'answer', '')}")
            response = await maintenance.run_job("reindex")
            if getattr(response, "success", True) is False:
                raise RuntimeError(f"ReMe reindex failed: {getattr(response, 'answer', '')}")

    async def _delete_scope_data(self, uid: str, agent_slug: str) -> bool:
        """调用方持有独占 lease 时删除 scope 目录和 catalog。"""
        workspace = validate_memory_workspace(self.base_dir, uid, agent_slug)
        removed = await asyncio.to_thread(
            clear_memory_workspace,
            workspace,
            base_dir=self.base_dir,
        )
        async with pg_manager.get_async_session_context() as db:
            await AgentMemoryScopeRepository(db).delete(uid, agent_slug)
            await db.commit()
        return removed

    async def _clear_records(self, records: list) -> dict:
        """原子占用全部 scope 后执行批量幂等清理。"""
        scopes = [(record.uid, record.agent_slug) for record in records]
        cleared = []
        async with self.registry.acquire_exclusive_many(scopes):
            for record in records:
                await self._delete_scope_data(record.uid, record.agent_slug)
                cleared.append(record.workspace_id)
        return {"success": True, "cleared_workspace_ids": cleared}


__all__ = ["AgentMemoryService", "MemoryScopeBusyError"]
