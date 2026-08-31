"""WPS 协作 Channel 的 Yuxi 控制面与镜像回归。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from cryptography.fernet import Fernet

from server.routers.agent_channel_router import ChannelCreate, ChannelUpdate, agent_channels
from server.utils.auth_middleware import get_admin_user
from yuxi.agentscope.channel_middleware import ChannelRunMirrorMiddleware
from yuxi.services.agentscope_channel_service import (
    AgentScopeChannelService,
    encrypt_app_secret,
)
from yuxi.services.agent_run_service import cancel_agent_run_view
from yuxi.services.agentscope_channel_run_service import (
    cancel_agentscope_channel_run,
)
from yuxi.storage.postgres.models_business import AgentScopeChannelBinding


class FakeDB:
    """记录事务边界的最小 AsyncSession 替身。"""

    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1

    async def refresh(self, _record):
        return None


class FakeClient:
    """记录 reconcile 调用顺序的 AgentScope 客户端替身。"""

    def __init__(self, *, fail_at: str | None = None):
        self.calls = []
        self.fail_at = fail_at

    async def _call(self, name, result=None):
        self.calls.append(name)
        if self.fail_at == name:
            raise RuntimeError(f"{name} failed with token=[REDACTED_SECRET]")
        return result

    async def create_credential(self, *_args):
        return await self._call("create_credential", "credential-1")

    async def update_credential(self, *_args):
        return await self._call("update_credential")

    async def create_agent(self, *_args):
        return await self._call("create_agent", "agent-1")

    async def update_agent(self, *_args):
        return await self._call("update_agent")

    async def create_channel(self, _uid, payload):
        self.channel_payload = payload
        return await self._call("create_channel", {"id": "channel-1"})

    async def update_channel(self, _uid, _channel_id, payload):
        self.channel_payload = payload
        return await self._call("update_channel", {"id": "channel-1"})

    async def list_channel_sessions(self, *_args):
        return await self._call("list_channel_sessions", [])


def _binding(ciphertext: str) -> AgentScopeChannelBinding:
    return AgentScopeChannelBinding(
        id="binding-1",
        owner_uid="owner-1",
        agent_slug="assistant",
        name="WPS 测试",
        channel_type="wps_xiezuo",
        app_id="app-1",
        encrypted_app_secret=ciphertext,
        allow_from=["user-1"],
        group_reply_policy="mention_only",
        enabled=True,
        model_spec=None,
        sync_status="pending",
        created_by="admin-1",
        updated_by="admin-1",
    )


@pytest.fixture
def credential_key(monkeypatch):
    key = Fernet.generate_key()
    monkeypatch.setenv("AGENTSCOPE_CHANNEL_CREDENTIAL_KEY", key.decode())
    return key


@pytest.mark.asyncio
async def test_reconcile_uses_native_channel_and_persists_each_remote_id(
    monkeypatch,
    credential_key,
):
    """WPS 同步使用原生 Channel 配置且不创建第二套运行器。"""
    projection = SimpleNamespace(
        model_spec="provider:model",
        credential_data={"type": "fake", "api_key": "model-key"},
        chat_model_config={"type": "fake", "credential_id": None, "model": "model"},
        agent_request={"name": "agent", "system_prompt": "prompt"},
    )
    project_runtime = AsyncMock(return_value=projection)
    monkeypatch.setattr(
        "yuxi.services.agentscope_channel_service.project_runtime",
        project_runtime,
    )
    db = FakeDB()
    client = FakeClient()
    binding = _binding(encrypt_app_secret("wps-secret"))
    binding.model_spec = "legacy-provider:legacy-model"

    result = await AgentScopeChannelService(db, client).reconcile(binding)

    assert result.sync_status == "synced"
    assert result.agentscope_credential_id == "credential-1"
    assert result.agentscope_agent_id == "agent-1"
    assert result.agentscope_channel_id == "channel-1"
    assert db.commits >= 4
    assert client.calls[:3] == ["create_credential", "create_agent", "create_channel"]
    assert client.channel_payload["session"]["busy_message_policy"] == "queue"
    assert client.channel_payload["session"]["permission_mode"] == "dont_ask"
    assert client.channel_payload["credentials"]["app_secret"] != "wps-secret"
    assert "encrypted_app_secret" not in result.to_dict()
    assert "model_spec" not in result.to_dict()
    assert result.model_spec == "provider:model"
    project_runtime.assert_awaited_once_with(
        db,
        uid="owner-1",
        agent_slug="assistant",
        model_spec=None,
    )


def test_channel_management_schema_has_no_model_override():
    """协作渠道管理接口不再接受渠道级模型覆盖。"""
    assert "model_spec" not in ChannelCreate.model_fields
    assert "model_spec" not in ChannelUpdate.model_fields


@pytest.mark.asyncio
async def test_partial_failure_keeps_remote_id_for_reconcile(monkeypatch, credential_key):
    """远端部分失败后保留已完成步骤，不回滚成不可恢复状态。"""
    monkeypatch.setattr(
        "yuxi.services.agentscope_channel_service.project_runtime",
        AsyncMock(
            return_value=SimpleNamespace(
                model_spec="provider:model",
                credential_data={"type": "fake"},
                chat_model_config={"type": "fake", "credential_id": None},
                agent_request={"name": "agent"},
            ),
        ),
    )
    db = FakeDB()
    binding = _binding(encrypt_app_secret("wps-secret"))
    service = AgentScopeChannelService(db, FakeClient(fail_at="create_agent"))
    service.bindings.get = AsyncMock(return_value=binding)

    result = await service.reconcile(binding)

    assert result.sync_status == "error"
    assert result.agentscope_credential_id == "credential-1"
    assert result.agentscope_agent_id is None
    assert db.rollbacks == 1
    assert "[REDACTED_SECRET]" in result.last_error


@pytest.mark.asyncio
async def test_projection_failure_does_not_block_native_reply_stream():
    """Yuxi 镜像失败只记账失败，不截断 AgentScope 原生回复。"""
    middleware = ChannelRunMirrorMiddleware(uid="owner", agent_id="agent", session_id="session")
    middleware._open_run = AsyncMock(side_effect=RuntimeError("database unavailable"))

    async def next_handler(**_kwargs):
        yield {"type": "text_block_delta", "delta": "reply"}

    inputs = {
        "content": [{"type": "text", "text": "hello"}],
        "metadata": {"channel_id": "channel-1", "channel_message_id": "message-1"},
    }
    events = [
        item
        async for item in middleware.on_reply(
            None,
            {"inputs": inputs},
            next_handler,
        )
    ]

    assert events == [{"type": "text_block_delta", "delta": "reply"}]


def test_all_agent_channel_routes_require_admin():
    """协作渠道控制面每个接口都以管理员依赖为最终权限边界。"""
    routes = [route for route in agent_channels.routes if getattr(route, "methods", None)]
    assert routes
    for route in routes:
        dependencies = {dependency.call for dependency in route.dependant.dependencies}
        assert get_admin_user in dependencies, f"{route.path} 缺少管理员权限依赖"


@pytest.mark.asyncio
async def test_channel_run_cancel_interrupts_agentscope_without_arq(monkeypatch):
    """Channel Run 取消只中断原生 Session，不进入常规 ARQ 取消链。"""
    run = SimpleNamespace(source="agentscope_channel")
    run_repo = SimpleNamespace(get_run_for_user=AsyncMock(return_value=run))
    cancel_channel = AsyncMock(return_value={"run": {"status": "cancel_requested"}})
    cancel_regular = AsyncMock()
    monkeypatch.setattr(
        "yuxi.services.agent_run_service.AgentRunRepository",
        lambda _db: run_repo,
    )
    monkeypatch.setattr(
        "yuxi.services.agentscope_channel_run_service.cancel_agentscope_channel_run",
        cancel_channel,
    )
    monkeypatch.setattr(
        "yuxi.services.agent_run_service.request_cancel_agent_run",
        cancel_regular,
    )

    result = await cancel_agent_run_view(
        run_id="run-1",
        current_uid="owner-1",
        db=FakeDB(),
    )

    assert result == {"run": {"status": "cancel_requested"}}
    cancel_channel.assert_awaited_once()
    cancel_regular.assert_not_awaited()


@pytest.mark.asyncio
async def test_channel_run_cancel_marks_request_then_interrupts_session(monkeypatch):
    """Channel Run 取消事实提交后，中断映射到的 AgentScope Session。"""
    run = SimpleNamespace(
        id="run-1",
        status="running",
        conversation_thread_id="thread-1",
    )
    cancelled = SimpleNamespace(to_dict=lambda: {"status": "cancel_requested"})
    run_repo = SimpleNamespace(request_cancel=AsyncMock(return_value=cancelled))
    mapping = SimpleNamespace(
        agentscope_agent_id="agent-1",
        agentscope_session_id="session-1",
    )
    client = SimpleNamespace(interrupt_session=AsyncMock())
    monkeypatch.setattr(
        "yuxi.services.agentscope_channel_run_service.AgentRunRepository",
        lambda _db: run_repo,
    )
    monkeypatch.setattr(
        "yuxi.services.agentscope_channel_run_service.get_thread_session",
        AsyncMock(return_value=mapping),
    )
    monkeypatch.setattr(
        "yuxi.services.agentscope_channel_run_service.AgentScopeServiceClient",
        lambda _url: client,
    )
    db = FakeDB()

    result = await cancel_agentscope_channel_run(
        run=run,
        current_uid="owner-1",
        db=db,
    )

    assert result == {"run": {"status": "cancel_requested"}}
    run_repo.request_cancel.assert_awaited_once_with("run-1")
    assert db.commits == 1
    client.interrupt_session.assert_awaited_once_with(
        "owner-1",
        "agent-1",
        "session-1",
    )
