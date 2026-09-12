from unittest.mock import AsyncMock

import pytest

from yuxi.agentscope.config_projection import RuntimeProjection
from yuxi.services import run_resource_snapshot_service as service


async def test_snapshot_can_be_loaded_with_historical_decryption_secret(monkeypatch):
    """轮换当前密钥后，历史 keyring 仍可读取活动 Run 的旧快照。"""
    stored = {}

    async def store(_db, snapshot):
        stored[snapshot.id] = snapshot

    projection = RuntimeProjection(
        agent_slug="agent-1",
        model_spec="resource:model",
        agent_request={},
        credential_data={},
        chat_model_config={},
    )
    monkeypatch.setattr(service, "project_runtime", AsyncMock(return_value=projection))
    monkeypatch.setattr(service, "store_snapshot", store)
    monkeypatch.setattr(
        service,
        "get_snapshot",
        AsyncMock(side_effect=lambda _db, snapshot_id, uid: stored.get(snapshot_id)),
    )
    monkeypatch.setenv("API_KEY_DERIVATION_SECRET", "old-snapshot-secret-that-is-at-least-32-characters")
    monkeypatch.delenv("RUN_RESOURCE_SNAPSHOT_DECRYPTION_SECRETS", raising=False)

    reference = await service.capture_run_resources(
        object(), uid="user-1", agent_slug="agent-1", model_spec="resource:model", thread_id=None
    )

    monkeypatch.setenv("API_KEY_DERIVATION_SECRET", "new-snapshot-secret-that-is-at-least-32-characters")
    monkeypatch.setenv(
        "RUN_RESOURCE_SNAPSHOT_DECRYPTION_SECRETS",
        '["old-snapshot-secret-that-is-at-least-32-characters"]',
    )
    projection = await service.load_run_resources(
        object(), uid="user-1", manifest={"runtime_snapshot": reference}, agent_slug="agent-1"
    )

    assert isinstance(projection, RuntimeProjection)


@pytest.mark.parametrize("raw", ['"not-a-list"', '["short"]', '{bad-json'])
def test_snapshot_keyring_rejects_invalid_configuration(monkeypatch, raw):
    monkeypatch.setenv("API_KEY_DERIVATION_SECRET", "current-snapshot-secret-that-is-at-least-32-characters")
    monkeypatch.setenv("RUN_RESOURCE_SNAPSHOT_DECRYPTION_SECRETS", raw)

    with pytest.raises(RuntimeError, match="RUN_RESOURCE_SNAPSHOT_DECRYPTION_SECRETS"):
        service._snapshot_keys()
