import threading
import weakref
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from yuxi.agents.backends.sandbox import provider as provider_module
from yuxi.agents.backends.sandbox.provisioner_client import SandboxRecord

pytestmark = pytest.mark.unit


def _provider(client) -> provider_module.ProvisionerSandboxProvider:
    provider = object.__new__(provider_module.ProvisionerSandboxProvider)
    provider._client = client
    provider._lock = threading.Lock()
    provider._thread_locks = weakref.WeakValueDictionary()
    provider._connections = {}
    provider._last_touch_at = {}
    provider._touch_interval_seconds = 30
    return provider


@pytest.mark.parametrize("method_name", ["acquire", "get"])
def test_persistent_sandbox_creation_bootstraps_empty_skill_projection(
    monkeypatch,
    tmp_path,
    method_name,
):
    """持久 Sandbox 创建不能依赖执行期重新读取当前 Skill 授权。"""
    projection_root = tmp_path / "skill-projections"
    client = SimpleNamespace(
        create=Mock(
            return_value=SandboxRecord(
                sandbox_id="sandbox-1",
                sandbox_url="http://sandbox",
                workdir_path=None,
            )
        )
    )
    provider = _provider(client)
    monkeypatch.setattr(provider_module, "get_skill_projection_dir", lambda: projection_root, raising=False)
    monkeypatch.setattr(provider_module, "load_user_agent_env", lambda _uid: {})

    if method_name == "acquire":
        provider.acquire("thread-1", uid="user-1")
    else:
        provider.get("thread-1", uid="user-1", create_if_missing=True)

    assert (projection_root / "user-1").is_dir()
    client.create.assert_called_once()


def test_ephemeral_sandbox_does_not_create_user_skill_projection(monkeypatch, tmp_path):
    """远程 Skill 安装的一次性 Sandbox 不应创建持久用户目录。"""
    projection_root = tmp_path / "skill-projections"
    client = SimpleNamespace(
        create=Mock(
            return_value=SandboxRecord(
                sandbox_id="sandbox-1",
                sandbox_url="http://sandbox",
                workdir_path=None,
            )
        )
    )
    provider = _provider(client)
    monkeypatch.setattr(provider_module, "get_skill_projection_dir", lambda: projection_root, raising=False)

    provider.acquire("thread-1", uid="remote-skill", inherit_env=False)

    assert not projection_root.exists()
    client.create.assert_called_once()


def test_persistent_sandbox_rejects_symlink_skill_projection(monkeypatch, tmp_path):
    """UID projection 不能通过符号链接把只读 Skill 挂载指向目录外。"""
    projection_root = tmp_path / "skill-projections"
    outside = tmp_path / "outside"
    projection_root.mkdir()
    outside.mkdir()
    (projection_root / "user-1").symlink_to(outside, target_is_directory=True)
    client = SimpleNamespace(create=Mock())
    provider = _provider(client)
    monkeypatch.setattr(provider_module, "get_skill_projection_dir", lambda: projection_root, raising=False)
    monkeypatch.setattr(provider_module, "load_user_agent_env", lambda _uid: {})

    with pytest.raises(ValueError, match="Skill projection"):
        provider.acquire("thread-1", uid="user-1")

    client.create.assert_not_called()
