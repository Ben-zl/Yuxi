"""WPS 协作 Channel 的 Yuxi ingress 与传输边界回归。"""

from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from cryptography.fernet import Fernet

from server.routers.agent_channel_router import ChannelCreate, ChannelUpdate, agent_channels
from server.utils.auth_middleware import get_admin_user
from yuxi.channels.wps_xiezuo import WPSAttachment, WPSChannelEvent, WPSXiezuoTransport
from yuxi.services.agentscope_channel_service import AgentScopeChannelService, encrypt_app_secret
from yuxi.services.attachment_service import (
    MAX_ATTACHMENT_SIZE_BYTES,
    persist_run_submission_attachments,
    rollback_run_submission_attachments,
)
from yuxi.services.run_submission_service import RunSubmissionAttachment
from yuxi.services import wps_channel_ingress_service as wps_ingress
from yuxi.services.wps_channel_ingress_service import _convert_event_content
from yuxi.services import wps_channel_runtime
from yuxi.services.wps_channel_runtime import WPSChannelRuntime, wps_channel_status_key
from yuxi.storage.postgres.models_business import AgentScopeChannelBinding
from yuxi.workspace.paths import runtime_workdir_path


class FakeDB:
    """记录控制面事务边界的最小 AsyncSession 替身。"""

    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1

    async def refresh(self, _record):
        return None


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
async def test_reconcile_only_validates_yuxi_projection(monkeypatch, credential_key):
    """WPS 控制面不再创建 AgentScope 原生 Channel、Agent 或 Credential。"""
    project_runtime = AsyncMock(return_value=SimpleNamespace(model_spec="provider:model"))
    monkeypatch.setattr("yuxi.services.agentscope_channel_service.project_runtime", project_runtime)
    binding = _binding(encrypt_app_secret("wps-secret"))
    binding.agentscope_channel_id = "legacy-channel"
    binding.agentscope_agent_id = "legacy-agent"
    binding.agentscope_credential_id = "legacy-credential"
    db = FakeDB()

    result = await AgentScopeChannelService(db).reconcile(binding)

    assert result.sync_status == "synced"
    assert result.model_spec is None
    assert result.agentscope_channel_id is None
    assert result.agentscope_agent_id is None
    assert result.agentscope_credential_id is None
    project_runtime.assert_awaited_once_with(db, uid="owner-1", agent_slug="assistant", model_spec=None)


def test_channel_management_schema_has_no_model_override():
    """协作渠道管理接口不接受渠道级模型覆盖。"""
    assert "model_spec" not in ChannelCreate.model_fields
    assert "model_spec" not in ChannelUpdate.model_fields


def test_wps_event_converts_files_to_run_submission_attachments():
    """WPS 附件进入统一 RunSubmissionCommand 的附件 DTO。"""
    event = WPSChannelEvent(
        channel_id="binding-1",
        channel_user_id="user-1",
        channel_message_id="message-1",
        chat_id="chat-1",
        text="分析附件",
        attachments=(WPSAttachment(name="report.pdf", media_type="application/pdf", content=b"pdf", sha256="digest"),),
    )

    text, attachments = _convert_event_content(event)

    assert text == "分析附件"
    assert len(attachments) == 1
    assert attachments[0].file_name == "report.pdf"
    assert attachments[0].content == b"pdf"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("delivery_created", "accepted_after_failure", "expected_deletes"),
    [(False, False, 0), (True, True, 0), (True, False, 1)],
)
async def test_ingress_only_deletes_new_orphaned_delivery(
    monkeypatch, credential_key, delivery_created, accepted_after_failure, expected_deletes
):
    """失败清理不能删除重投或已被并发接受的投递事实。"""
    db = FakeDB()
    binding = _binding(encrypt_app_secret("wps-secret"))
    binding.sync_status = "synced"
    owner = SimpleNamespace(uid="owner-1", is_deleted=False, department_id=11)
    delivery = SimpleNamespace(id=7)
    deliveries = SimpleNamespace(
        create_if_missing=AsyncMock(return_value=(delivery, delivery_created)),
        get_by_request_id=AsyncMock(return_value=delivery),
        delete=AsyncMock(),
    )
    request_repo = SimpleNamespace(
        get_by_request_id=AsyncMock(side_effect=[None, SimpleNamespace() if accepted_after_failure else None])
    )

    @asynccontextmanager
    async def session_context():
        yield db

    monkeypatch.setattr(wps_ingress.pg_manager, "get_async_session_context", session_context)
    monkeypatch.setattr(
        wps_ingress,
        "AgentScopeChannelBindingRepository",
        lambda _db: SimpleNamespace(get=AsyncMock(return_value=binding)),
    )
    monkeypatch.setattr(
        wps_ingress,
        "UserRepository",
        lambda _db: SimpleNamespace(get_by_uid=AsyncMock(return_value=owner)),
    )
    monkeypatch.setattr(wps_ingress, "ChannelDeliveryRepository", lambda _db: deliveries)
    monkeypatch.setattr(wps_ingress, "AgentRunRequestRepository", lambda _db: request_repo)
    monkeypatch.setattr(
        wps_ingress,
        "AgentRunRepository",
        lambda _db: SimpleNamespace(get_run_by_request_id=AsyncMock(return_value=None)),
    )
    monkeypatch.setattr(wps_ingress, "submit_run_command", AsyncMock(side_effect=RuntimeError("intake failed")))

    event = WPSChannelEvent(
        channel_id=binding.id,
        channel_user_id="user-1",
        channel_message_id="message-1",
        chat_id="chat-1",
        text="hello",
    )
    with pytest.raises(RuntimeError, match="intake failed"):
        await wps_ingress.submit_wps_channel_event(binding.id, event)

    assert deliveries.delete.await_count == expected_deletes


@pytest.mark.asyncio
async def test_ingress_rejects_sender_outside_allowlist(monkeypatch, credential_key):
    """WPS ingress 不得以 binding owner 身份替未授权发送者执行。"""
    db = FakeDB()
    binding = _binding(encrypt_app_secret("wps-secret"))
    binding.sync_status = "synced"

    @asynccontextmanager
    async def session_context():
        yield db

    monkeypatch.setattr(wps_ingress.pg_manager, "get_async_session_context", session_context)
    monkeypatch.setattr(
        wps_ingress,
        "AgentScopeChannelBindingRepository",
        lambda _db: SimpleNamespace(get=AsyncMock(return_value=binding)),
    )

    event = WPSChannelEvent(
        channel_id=binding.id,
        channel_user_id="attacker",
        channel_message_id="message-1",
        chat_id="chat-1",
        text="hello",
    )
    with pytest.raises(ValueError, match="allow_from"):
        await wps_ingress.submit_wps_channel_event(binding.id, event)


@pytest.mark.asyncio
async def test_disabled_binding_status_ignores_stale_connected_key(monkeypatch, credential_key):
    """binding 禁用后不能继续展示 Redis 中的旧 connected 状态。"""
    binding = _binding(encrypt_app_secret("wps-secret"))
    binding.enabled = False
    binding.sync_status = "synced"
    redis = SimpleNamespace(get=AsyncMock(return_value='{"state":"connected"}'))
    monkeypatch.setattr(
        "yuxi.services.agentscope_channel_service.get_redis_client",
        AsyncMock(return_value=redis),
    )

    result = await AgentScopeChannelService(FakeDB()).status(binding)

    assert result == {"state": "stopped", "last_error": ""}
    redis.get.assert_not_awaited()


@pytest.mark.asyncio
async def test_stop_deletes_runtime_status_without_blocking_on_redis_failure(monkeypatch):
    """transport 停止后删除状态；Redis 清理失败不能恢复或阻断连接。"""
    runtime = WPSChannelRuntime()
    task = AsyncMock()
    task.cancel = Mock()
    transport = SimpleNamespace(aclose=AsyncMock())
    runtime._running["binding-1"] = SimpleNamespace(
        fingerprint="fingerprint",
        task=task,
        transport=transport,
    )
    redis = SimpleNamespace(delete=AsyncMock(side_effect=RuntimeError("redis unavailable")))
    monkeypatch.setattr(wps_channel_runtime, "get_redis_client", AsyncMock(return_value=redis))
    monkeypatch.setattr(wps_channel_runtime.asyncio, "gather", AsyncMock(return_value=[]))

    await runtime._stop("binding-1")

    assert "binding-1" not in runtime._running
    task.cancel.assert_called_once_with()
    transport.aclose.assert_awaited_once_with()
    redis.delete.assert_awaited_once_with(wps_channel_status_key("binding-1"))


def test_transport_attachment_limit_matches_run_intake_limit():
    """WPS 下载边界不得高于统一 Run intake 的 5 MB 限制。"""
    assert WPSXiezuoTransport.Config().max_media_bytes == MAX_ATTACHMENT_SIZE_BYTES


@pytest.mark.asyncio
async def test_run_submission_rejects_more_than_ten_external_attachments():
    """统一 Run intake 在写 Workdir 前拒绝超过十个附件。"""
    attachments = tuple(
        RunSubmissionAttachment(file_name=f"{index}.txt", media_type="text/plain", content=b"x") for index in range(11)
    )

    with pytest.raises(ValueError, match="最多提交 10 个附件"):
        await persist_run_submission_attachments(
            conversation=SimpleNamespace(),
            uid="owner-1",
            attachments=attachments,
            db=FakeDB(),
        )


@pytest.mark.asyncio
async def test_run_submission_attachment_rollback_uses_workdir_capability(monkeypatch):
    """intake 回滚只通过 Workdir 删除运行时路径，不取得宿主机路径。"""
    workdir = SimpleNamespace(relative_path="projects/00000000-0000-0000-0000-000000000001", delete=Mock())
    monkeypatch.setattr(
        "yuxi.workspace.workdir.Workdir.open_existing",
        Mock(return_value=workdir),
    )
    runtime_path = f"{runtime_workdir_path(workdir.relative_path)}/uploads/file.txt"

    await rollback_run_submission_attachments(
        records=[{"path": runtime_path, "original_path": runtime_path}],
        uid="owner-1",
        workdir_path=workdir.relative_path,
    )

    workdir.delete.assert_called_once_with("/uploads/file.txt")


@pytest.mark.asyncio
async def test_transport_does_not_ack_or_deduplicate_failed_ingress(credential_key):
    """Yuxi intake 失败时 WPS 返回失败 ACK，并允许平台重投。"""
    transport = WPSXiezuoTransport(
        "binding-1",
        WPSXiezuoTransport.Credentials(
            app_id="app-1",
            app_secret=Fernet(credential_key).encrypt(b"wps-secret").decode(),
        ),
        WPSXiezuoTransport.Config(group_reply_policy="all"),
    )
    transport._emit = AsyncMock(side_effect=RuntimeError("database unavailable"))
    payload = {
        "message": {"id": "message-1", "type": "text", "content": {"text": {"content": "hello"}}},
        "sender": {"id": "sender-1", "type": "user"},
        "chat": {"id": "chat-1", "type": "p2p"},
    }

    with pytest.raises(RuntimeError, match="database unavailable"):
        await transport._handle_message_payload(payload)

    assert transport._is_duplicate("message-1") is False


@pytest.mark.asyncio
async def test_delivery_reads_only_same_run_output_message():
    """WPS 回复不能从相邻消息猜测结果。"""
    run = SimpleNamespace(id="run-1", status="completed", output_message_id=7)
    db = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(run_id="run-2", content="wrong")))

    result = await WPSChannelRuntime._delivery_text(db, run)

    assert result == "任务已完成，但未找到该次运行的输出，请在 Yuxi 对话中查看详情。"


def test_agentscope_service_has_no_native_wps_channel_registration():
    """shipping AgentScope 服务不得注册 WPS 原生执行入口。"""
    source = Path("server/agentscope_main.py").read_text(encoding="utf-8")
    assert "WPSXiezuoChannel" not in source
    assert "ChannelRunMirrorMiddleware" not in source
    assert "channels=[" not in source


def test_all_agent_channel_routes_require_admin():
    """协作渠道控制面每个接口都以管理员依赖为最终权限边界。"""
    routes = [route for route in agent_channels.routes if getattr(route, "methods", None)]
    assert routes
    for route in routes:
        dependencies = {dependency.call for dependency in route.dependant.dependencies}
        assert get_admin_user in dependencies, f"{route.path} 缺少管理员权限依赖"
