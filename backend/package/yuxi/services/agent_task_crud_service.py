"""AgentTask 定义管理：CRUD、可见范围与管理权（任务中心工单 02/05/10）。

任务使用与 Agent/Skill 相同的 share_config v2 规则；任务范围不得超过
引用智能体的可见范围（父规格 #958）。删除不是任务生命周期入口，
统一走启停与归档。
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.permissions.resource_permission import (
    DEFAULT_SCOPE,
    ResourcePermission,
    resolve_agent_permission,
    resolve_resource_permission,
)
from yuxi.repositories.agent_repository import AgentRepository
from yuxi.repositories.agent_task_repository import AgentTaskRepository
from yuxi.storage.postgres.models_business import Agent, AgentTask, User
from yuxi.utils.datetime_utils import utc_now_naive

# 任务可见范围复用 Agent 权限策略（read=可见可触发，manage=编辑管理）
from yuxi.permissions.resource_permission import AGENT_PERMISSION_POLICY

from yuxi.utils.ids import new_uuid

TASK_TOOL_APPROVAL_MODES = ("always_trust", "default")


def task_permission(user: User, task: AgentTask) -> ResourcePermission:
    """任务可见性：复用统一资源权限解析（创建者与超管自动 MANAGE）。"""
    return resolve_resource_permission(user, task, AGENT_PERMISSION_POLICY)


def can_manage_task(user: User, task: AgentTask) -> bool:
    """管理权：创建者或具备 manage 权限的用户。"""
    return task_permission(user, task) == ResourcePermission.MANAGE


def can_view_task(user: User, task: AgentTask) -> bool:
    return task_permission(user, task) != ResourcePermission.NONE


def _normalize_share_config(raw: dict | None, *, owner_uid: str) -> dict:
    """规范化为 v2 共享配置；空视为个人（user 范围仅创建者可读）。"""
    if not raw:
        scope = {"access_level": "user", "department_ids": [], "user_uids": [owner_uid]}
    else:
        level = raw.get("access_level")
        if level not in ("user", "department"):
            raise HTTPException(status_code=422, detail="任务可见范围只支持个人或部门")
        if level == "department":
            departments = sorted({int(v) for v in raw.get("department_ids") or []})
            if not departments:
                raise HTTPException(status_code=422, detail="部门共享至少需要选择一个部门")
            scope = {"access_level": "department", "department_ids": departments, "user_uids": []}
        else:
            scope = {"access_level": "user", "department_ids": [], "user_uids": [owner_uid]}
    return {"version": 2, "read_scope": scope, "manage_scope": None}


def _scope_within(task_scope: dict, agent_share_config: dict | None) -> bool:
    """任务范围不得超过智能体范围（子集判定，v2 read_scope）。

    任务只支持个人或部门范围（_normalize_share_config 限制），
    因此无需处理 global 任务分支。
    """
    agent = (agent_share_config or {}).get("read_scope") or DEFAULT_SCOPE
    task = task_scope.get("read_scope") or task_scope
    if agent.get("access_level") == "global":
        return True
    if agent.get("access_level") == "department":
        if task.get("access_level") != "department":
            return True
        agent_depts = set(agent.get("department_ids") or [])
        return set(task.get("department_ids") or []).issubset(agent_depts)
    return task.get("access_level") == "user"


class AgentTaskCRUDService:
    """任务定义的创建、查询、编辑与生命周期操作。"""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.tasks = AgentTaskRepository(db)

    def _load_agent(self, agent: Agent | None) -> Agent:
        if agent is None:
            raise HTTPException(status_code=404, detail="智能体不存在")
        return agent

    async def create_task(self, *, user: User, payload: dict[str, Any]) -> AgentTask:
        """创建个人/部门任务；固定智能体名称与 slug 展示快照。"""
        from yuxi.services.agent_task_schedule_service import apply_schedule_fields

        agent = self._load_agent(
            await AgentRepository(self.db).get_for_update_by_slug(str(payload.get("agent_slug") or ""))
        )
        if resolve_agent_permission(user, agent) == ResourcePermission.NONE:
            raise HTTPException(status_code=403, detail="无权使用该智能体")
        share_config = _normalize_share_config(payload.get("share_config"), owner_uid=str(user.uid))
        if not _scope_within(share_config, agent.share_config):
            raise HTTPException(status_code=422, detail="任务可见范围不能超过智能体可见范围")

        approval_mode = payload.get("tool_approval_mode") or "always_trust"
        if approval_mode not in TASK_TOOL_APPROVAL_MODES:
            raise HTTPException(status_code=422, detail="无效的工具审批模式")
        if user.department_id is None:
            raise HTTPException(status_code=403, detail="创建任务需要有效部门上下文")

        fields = apply_schedule_fields(payload)  # 校验并编译定时配置；未启用返回空
        task = await self.tasks.create(
            id=new_uuid(),
            name=str(payload.get("name") or "").strip(),
            owner_uid=str(user.uid),
            department_id=user.department_id,
            agent_id=agent.id,
            agent_name_snapshot=agent.name,
            agent_slug_snapshot=agent.slug,
            prompt=str(payload.get("prompt") or ""),
            share_config=share_config,
            enabled=True,
            api_enabled=bool(payload.get("api_enabled")),
            tool_approval_mode=approval_mode,
            **fields,
        )
        await self.db.commit()
        await self.db.refresh(task)
        return task

    async def get_task_for(self, *, user: User, task_id: str, manage: bool = False) -> AgentTask:
        task = await self.tasks.get(task_id)
        if task is None or (manage and not can_manage_task(user, task)):
            raise HTTPException(status_code=404, detail="任务不存在")
        if not can_view_task(user, task):
            raise HTTPException(status_code=404, detail="任务不存在")
        return task

    async def list_tasks(self, *, user: User) -> list[AgentTask]:
        tasks = await self.tasks.list_recent()
        return [t for t in tasks if can_view_task(user, t)]

    async def update_task(self, *, user: User, task_id: str, payload: dict[str, Any]) -> AgentTask:
        """编辑任务；只影响未来触发，不改写既有 TaskExecution。"""
        from yuxi.services.agent_task_schedule_service import apply_schedule_fields

        replacement_agent = None
        if "agent_slug" in payload:
            replacement_agent = self._load_agent(
                await AgentRepository(self.db).get_for_update_by_slug(str(payload["agent_slug"]))
            )

        task = await self.tasks.get_for_update(task_id)
        if task is None or not can_manage_task(user, task):
            raise HTTPException(status_code=404, detail="任务不存在")

        if "name" in payload:
            task.name = str(payload["name"] or "").strip() or task.name
        if "prompt" in payload:
            task.prompt = str(payload["prompt"] or "")
        if "api_enabled" in payload:
            task.api_enabled = bool(payload["api_enabled"])
        if "tool_approval_mode" in payload:
            mode = payload["tool_approval_mode"]
            if mode not in TASK_TOOL_APPROVAL_MODES:
                raise HTTPException(status_code=422, detail="无效的工具审批模式")
            task.tool_approval_mode = mode
        if "share_config" in payload:
            share_config = _normalize_share_config(payload.get("share_config"), owner_uid=task.owner_uid)
            agent = await self.db.get(Agent, task.agent_id) if task.agent_id else None
            if agent is not None and not _scope_within(share_config, agent.share_config):
                raise HTTPException(status_code=422, detail="任务可见范围不能超过智能体可见范围")
            task.share_config = share_config
        if "agent_slug" in payload:
            agent = replacement_agent
            if resolve_agent_permission(user, agent) == ResourcePermission.NONE:
                raise HTTPException(status_code=403, detail="无权使用该智能体")
            existing_scope = (task.share_config or {}).get("read_scope") or {}
            share_config = _normalize_share_config(
                {
                    "access_level": existing_scope.get("access_level"),
                    "department_ids": existing_scope.get("department_ids"),
                },
                owner_uid=task.owner_uid,
            )
            if not _scope_within(share_config, agent.share_config):
                raise HTTPException(status_code=422, detail="任务可见范围不能超过智能体可见范围")
            task.agent_id = agent.id
            task.agent_name_snapshot = agent.name
            task.agent_slug_snapshot = agent.slug

        schedule_payload = payload.get("schedule")
        fields = apply_schedule_fields({"schedule": schedule_payload} if schedule_payload is not None else {})
        for key, value in fields.items():
            setattr(task, key, value)
        if not (schedule_payload or {}).get("enabled") and "schedule" in payload:
            task.schedule_mode = None
            task.schedule_cron = None
            task.next_run_at = None

        await self._apply_lifecycle(task, payload)
        await self.db.commit()
        await self.db.refresh(task)
        return task

    async def _apply_lifecycle(self, task: AgentTask, payload: dict[str, Any]) -> None:
        """启停与归档：归档隐含停用并取消排队项；停用保留当前 Run。"""
        from yuxi.services.agent_task_dispatcher import AgentTaskDispatcher

        if "enabled" in payload and not bool(payload["enabled"]) and task.enabled:
            task.enabled = False
            await AgentTaskDispatcher(self.db).cancel_queued(task)
        elif "enabled" in payload and bool(payload["enabled"]) and not task.enabled:
            task.enabled = True
        if bool(payload.get("archived")) and task.archived_at is None:
            task.enabled = False
            task.archived_at = utc_now_naive()
            await AgentTaskDispatcher(self.db).cancel_queued(task)
