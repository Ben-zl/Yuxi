"""AgentTask 创建和换绑的 Agent 删除并发屏障测试。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from yuxi.permissions import ResourcePermission
from yuxi.services import agent_task_crud_service


class FakeDb:
    def __init__(self):
        self.commit = AsyncMock()
        self.refresh = AsyncMock()
        # 部门存在性锁查询：返回部门 id 表示存在
        self.execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: 11))


class FakeTaskRepository:
    task = None

    def __init__(self, _db):
        pass

    async def create(self, **kwargs):
        return SimpleNamespace(**kwargs)

    async def get_for_update(self, _task_id):
        return self.task


@pytest.mark.asyncio
async def test_create_task_locks_active_agent_before_writing_reference(monkeypatch):
    """任务创建必须持有同一 Agent 行锁，不能越过删除 tombstone。"""
    calls = []
    agent = SimpleNamespace(id=7, slug="agent-a", name="A", share_config={"read_scope": {"access_level": "global"}})

    class AgentRepo:
        def __init__(self, _db):
            pass

        async def get_for_update_by_slug(self, slug):
            calls.append(slug)
            return agent

    monkeypatch.setattr(agent_task_crud_service, "AgentRepository", AgentRepo)
    monkeypatch.setattr(agent_task_crud_service, "AgentTaskRepository", FakeTaskRepository)
    monkeypatch.setattr(
        agent_task_crud_service, "resolve_agent_permission", lambda _user, _agent: ResourcePermission.MANAGE
    )
    service = agent_task_crud_service.AgentTaskCRUDService(FakeDb())

    task = await service.create_task(
        user=SimpleNamespace(uid="user-1", department_id=11),
        payload={"name": "task", "agent_slug": "agent-a", "prompt": "run"},
    )

    assert calls == ["agent-a"]
    assert task.agent_id == 7
    assert task.department_id == 11


@pytest.mark.asyncio
async def test_update_task_locks_replacement_agent_before_task_row(monkeypatch):
    """任务换绑先锁新 Agent，再锁 Task，和删除侧保持固定锁顺序。"""
    calls = []
    agent = SimpleNamespace(id=8, slug="agent-b", name="B", share_config={"read_scope": {"access_level": "global"}})
    task = SimpleNamespace(
        id="task-1",
        owner_uid="user-1",
        created_by="user-1",
        share_config={"read_scope": {"access_level": "user", "user_uids": ["user-1"]}},
        agent_id=7,
        enabled=True,
        archived_at=None,
        name="task",
        prompt="run",
        api_enabled=False,
        tool_approval_mode="always_trust",
    )

    class AgentRepo:
        def __init__(self, _db):
            pass

        async def get_for_update_by_slug(self, slug):
            calls.append(f"agent:{slug}")
            return agent

    class TaskRepo(FakeTaskRepository):
        async def get_for_update(self, _task_id):
            calls.append("task")
            return task

    monkeypatch.setattr(agent_task_crud_service, "AgentRepository", AgentRepo)
    monkeypatch.setattr(agent_task_crud_service, "AgentTaskRepository", TaskRepo)
    monkeypatch.setattr(agent_task_crud_service, "can_manage_task", lambda _user, _task: True)
    monkeypatch.setattr(
        agent_task_crud_service, "resolve_agent_permission", lambda _user, _agent: ResourcePermission.MANAGE
    )
    service = agent_task_crud_service.AgentTaskCRUDService(FakeDb())

    updated = await service.update_task(
        user=SimpleNamespace(uid="user-1"),
        task_id="task-1",
        payload={"agent_slug": "agent-b"},
    )

    assert calls[:2] == ["agent:agent-b", "task"]
    assert updated.agent_id == 8
