"""AgentScope 线程映射与模型切换单元测试。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from yuxi.agentscope import runner

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _no_retained_team_workers(monkeypatch):
    """默认线程没有长期 Team worker；相关用例单独覆盖。"""
    monkeypatch.setattr(
        runner,
        "AgentScopeTeamWorkerRepository",
        lambda _db: SimpleNamespace(
            list_active_for_parent_thread=AsyncMock(return_value=[]),
        ),
    )


async def test_existing_thread_switches_model_after_remote_update(monkeypatch):
    """已有线程模型变化时先更新远端，再提交本地映射。"""
    record = SimpleNamespace(
        model_spec="p:old",
        agentscope_agent_id="agent",
        agentscope_session_id="session",
        agentscope_credential_id="credential-old",
    )
    projection = SimpleNamespace(
        model_spec="p:new",
        agent_request={"name": "agent"},
        credential_data={"type": "openai_credential", "api_key": "secret"},
        chat_model_config={"model": "new", "credential_id": None},
        skills=[],
    )
    db = SimpleNamespace(commit=AsyncMock())
    client = SimpleNamespace(
        create_credential=AsyncMock(return_value="credential-new"),
        update_agent=AsyncMock(),
        update_session_model=AsyncMock(),
        list_workspace_skills=AsyncMock(return_value=[]),
        remove_workspace_skill=AsyncMock(),
        add_workspace_skill=AsyncMock(),
        delete_credential=AsyncMock(),
    )
    monkeypatch.setattr(runner.thread_session_repo, "get_thread_session", AsyncMock(return_value=record))
    monkeypatch.setattr(runner, "project_runtime", AsyncMock(return_value=projection))
    update_mapping = AsyncMock(return_value=record)
    monkeypatch.setattr(runner.thread_session_repo, "update_thread_session_model", update_mapping)

    result = await runner.ensure_thread_session(
        db,
        client,
        uid="u",
        thread_id="t",
        agent_slug="a",
        model_spec="p:new",
    )

    assert result is record
    client.update_session_model.assert_awaited_once_with(
        "u", "agent", "session", {"model": "new", "credential_id": "credential-new"}
    )
    update_mapping.assert_awaited_once()
    db.commit.assert_awaited_once()


async def test_existing_thread_updates_retained_team_workers_before_deleting_old_credential(monkeypatch):
    """主线程轮换凭证时，长期 worker 必须先同步模型配置再删除旧凭证。"""
    call_order = []

    async def update_session_model(*args):
        call_order.append(("update", args[2]))

    async def delete_credential(*_args):
        call_order.append(("delete", "credential-old"))

    record = SimpleNamespace(
        model_spec="p:old",
        agentscope_agent_id="leader-agent",
        agentscope_session_id="leader-session",
        agentscope_credential_id="credential-old",
    )
    projection = SimpleNamespace(
        model_spec="p:new",
        agent_request={"name": "leader"},
        credential_data={"type": "openai_credential", "api_key": "secret"},
        chat_model_config={"model": "new", "credential_id": None},
        skills=[],
    )
    worker = SimpleNamespace(
        worker_agent_id="worker-agent",
        worker_session_id="worker-session",
    )
    db = SimpleNamespace(commit=AsyncMock())
    client = SimpleNamespace(
        create_credential=AsyncMock(return_value="credential-new"),
        update_agent=AsyncMock(),
        update_session_model=AsyncMock(side_effect=update_session_model),
        list_workspace_skills=AsyncMock(return_value=[]),
        remove_workspace_skill=AsyncMock(),
        add_workspace_skill=AsyncMock(),
        delete_credential=AsyncMock(side_effect=delete_credential),
    )
    monkeypatch.setattr(runner.thread_session_repo, "get_thread_session", AsyncMock(return_value=record))
    monkeypatch.setattr(runner, "project_runtime", AsyncMock(return_value=projection))
    monkeypatch.setattr(
        runner.thread_session_repo,
        "update_thread_session_model",
        AsyncMock(return_value=record),
    )
    list_workers = AsyncMock(return_value=[worker])
    monkeypatch.setattr(
        runner,
        "AgentScopeTeamWorkerRepository",
        lambda _db: SimpleNamespace(list_active_for_parent_thread=list_workers),
        raising=False,
    )

    await runner.ensure_thread_session(
        db,
        client,
        uid="u",
        thread_id="thread",
        agent_slug="leader",
        model_spec="p:new",
    )

    list_workers.assert_awaited_once_with(uid="u", parent_thread_id="thread")
    assert client.update_session_model.await_args_list == [
        (("u", "leader-agent", "leader-session", {"model": "new", "credential_id": "credential-new"}),),
        (("u", "worker-agent", "worker-session", {"model": "new", "credential_id": "credential-new"}),),
    ]
    assert call_order == [
        ("update", "leader-session"),
        ("update", "worker-session"),
        ("delete", "credential-old"),
    ]


async def test_existing_thread_model_update_failure_keeps_mapping(monkeypatch):
    """远端 PATCH 失败时不改写本地模型映射。"""
    record = SimpleNamespace(
        model_spec="p:old",
        agentscope_agent_id="agent",
        agentscope_session_id="session",
        agentscope_credential_id="credential-old",
    )
    db = SimpleNamespace(commit=AsyncMock())
    client = SimpleNamespace(
        create_credential=AsyncMock(return_value="credential-new"),
        update_agent=AsyncMock(),
        update_session_model=AsyncMock(side_effect=RuntimeError("locked")),
        list_workspace_skills=AsyncMock(return_value=[]),
        remove_workspace_skill=AsyncMock(),
        add_workspace_skill=AsyncMock(),
        delete_credential=AsyncMock(),
    )
    monkeypatch.setattr(runner.thread_session_repo, "get_thread_session", AsyncMock(return_value=record))
    monkeypatch.setattr(
        runner,
        "project_runtime",
        AsyncMock(
            return_value=SimpleNamespace(
                model_spec="p:new",
                agent_request={"name": "agent"},
                credential_data={},
                chat_model_config={"model": "new"},
                skills=[],
            )
        ),
    )
    update_mapping = AsyncMock()
    monkeypatch.setattr(runner.thread_session_repo, "update_thread_session_model", update_mapping)

    with pytest.raises(RuntimeError, match="locked"):
        await runner.ensure_thread_session(
            db,
            client,
            uid="u",
            thread_id="t",
            agent_slug="a",
            model_spec="p:new",
        )
    update_mapping.assert_not_awaited()
    db.commit.assert_not_awaited()


async def test_existing_thread_reconciles_agent_model_and_skills_each_round(monkeypatch):
    """已有线程必须逐轮应用 Prompt、模型参数和 Skill 配置。"""
    record = SimpleNamespace(
        model_spec="p:model",
        agentscope_agent_id="agent",
        agentscope_session_id="session",
        agentscope_credential_id="credential-old",
    )
    projection = SimpleNamespace(
        model_spec="p:model",
        agent_request={"name": "new", "system_prompt": "new prompt", "react_config": {"max_iters": 12}},
        credential_data={"type": "anthropic_credential", "api_key": "secret"},
        chat_model_config={"model": "model", "parameters": {"thinking_enable": True}},
        skills=[{"slug": "skill-b", "source_dir": "/skills/b"}],
    )
    db = SimpleNamespace(commit=AsyncMock())
    client = SimpleNamespace(
        create_credential=AsyncMock(return_value="credential-new"),
        update_agent=AsyncMock(),
        update_session_model=AsyncMock(),
        list_workspace_skills=AsyncMock(return_value=["skill-a"]),
        add_workspace_skill=AsyncMock(),
        remove_workspace_skill=AsyncMock(),
        delete_credential=AsyncMock(),
    )
    monkeypatch.setattr(runner.thread_session_repo, "get_thread_session", AsyncMock(return_value=record))
    monkeypatch.setattr(runner, "project_runtime", AsyncMock(return_value=projection))
    monkeypatch.setattr(
        runner.thread_session_repo,
        "update_thread_session_model",
        AsyncMock(return_value=record),
    )

    result = await runner.ensure_thread_session(
        db,
        client,
        uid="u",
        thread_id="t",
        agent_slug="a",
        model_spec=None,
    )

    assert result is record
    client.update_agent.assert_awaited_once_with("u", "agent", projection.agent_request)
    client.update_session_model.assert_awaited_once_with(
        "u",
        "agent",
        "session",
        {"model": "model", "parameters": {"thinking_enable": True}, "credential_id": "credential-new"},
    )
    client.remove_workspace_skill.assert_awaited_once_with("u", "agent", "session", "skill-a")
    client.add_workspace_skill.assert_awaited_once_with("u", "agent", "session", "/skills/b")
