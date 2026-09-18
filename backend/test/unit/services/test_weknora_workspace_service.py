"""WeKnora 部门 workspace 开通与专属 Key 解析测试(远端传输边界注入)。"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from cryptography.fernet import Fernet

from yuxi.knowledge.base import KBOperationError
from yuxi.services import weknora_workspace_service as service

pytestmark = pytest.mark.unit

SETTINGS = SimpleNamespace(
    base_url="http://weknora-app:8080/api/v1",
    api_key="sk-provision",
)


def _patch_department_lock(monkeypatch) -> None:
    """单元库不连 PG：部门存在性锁以 no-op 替代（集成路径由真实库覆盖）。"""

    async def _noop(_session, _department_id: int) -> None:
        return None

    monkeypatch.setattr(service, "_lock_department_row", _noop)


@pytest.fixture
def credential_env(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("WEKNORA_WORKSPACE_CREDENTIAL_KEY", key)
    return key


@pytest.fixture
def settings_env(monkeypatch):
    monkeypatch.setenv("WEKNORA_BASE_URL", SETTINGS.base_url)
    monkeypatch.setenv("WEKNORA_API_KEY", SETTINGS.api_key)


class FakeWorkspaceRepos:
    """记录 workspace 映射仓储副作用的替身。

    race_winner 模拟并发开通竞态:首次读取为空,create 撞唯一约束后才能读到先落库者。
    """

    def __init__(self, initial=None, conflict=False, race_winner=None):
        self.rows: dict[int, SimpleNamespace] = {row.department_id: row for row in (initial or [])}
        self.created: list[dict] = []
        self.replaced: list[dict] = []
        self.conflict = conflict
        self.race_winner = race_winner

    def install(self, monkeypatch):
        repos = self

        async def fake_get(self, department_id):
            if repos.race_winner is not None and not repos.rows:
                return None
            return repos.rows.get(int(department_id))

        async def fake_create(self, data, db=None):
            if repos.conflict or repos.race_winner is not None:
                from sqlalchemy.exc import IntegrityError

                if repos.race_winner is not None:
                    repos.rows[repos.race_winner.department_id] = repos.race_winner
                raise IntegrityError("stmt", {}, Exception("unique"))
            repos.created.append(dict(data))
            row = SimpleNamespace(**data)
            repos.rows[data["department_id"]] = row
            return row

        async def fake_replace(self, department_id, data, db=None):
            repos.replaced.append(dict(data))
            row = SimpleNamespace(department_id=int(department_id), **data)
            repos.rows[int(department_id)] = row
            return row

        monkeypatch.setattr(
            "yuxi.repositories.weknora_workspace_repository.WeknoraWorkspaceRepository.get_by_department", fake_get
        )
        monkeypatch.setattr(
            "yuxi.repositories.weknora_workspace_repository.WeknoraWorkspaceRepository.create", fake_create
        )
        monkeypatch.setattr(
            "yuxi.repositories.weknora_workspace_repository.WeknoraWorkspaceRepository.replace_for_department",
            fake_replace,
        )
        return self


def _client(handler):
    from yuxi.knowledge.weknora import WeKnoraClient, WeKnoraSettings

    return WeKnoraClient(
        WeKnoraSettings(base_url=SETTINGS.base_url, api_key=SETTINGS.api_key),
        transport=httpx.MockTransport(handler),
    )


def _patch_department_name(monkeypatch, name="研发一部"):
    async def fake_name(self, department_id):
        return name

    monkeypatch.setattr("yuxi.repositories.department_repository.DepartmentRepository.get_name_by_id", fake_name)


def _patch_selfcheck(monkeypatch, handler) -> None:
    """自检客户端注入 MockTransport,保持真实 settings 出站行为。"""

    from yuxi.knowledge.weknora import WeKnoraClient

    monkeypatch.setattr(
        service, "_build_client", lambda settings: WeKnoraClient(settings, transport=httpx.MockTransport(handler))
    )


def test_cipher_roundtrip_and_invalid_token(credential_env) -> None:
    ciphertext = service.encrypt_workspace_key("sk-dept-secret")
    assert ciphertext != "sk-dept-secret"
    assert service.decrypt_workspace_key(ciphertext) == "sk-dept-secret"

    with pytest.raises(RuntimeError, match="解密"):
        service.decrypt_workspace_key(ciphertext[:-4] + "AAAA")


def test_cipher_requires_credential_key(monkeypatch) -> None:
    monkeypatch.delenv("WEKNORA_WORKSPACE_CREDENTIAL_KEY", raising=False)
    with pytest.raises(RuntimeError, match="WEKNORA_WORKSPACE_CREDENTIAL_KEY"):
        service.encrypt_workspace_key("sk-x")


@pytest.mark.asyncio
async def test_ensure_reuses_confirmed_mapping_without_remote_call(
    credential_env, settings_env, monkeypatch
) -> None:
    encrypted = service.encrypt_workspace_key("sk-dept-existing")
    row = SimpleNamespace(
        department_id=2,
        workspace_tenant_id="ws-9",
        workspace_name="研发一部",
        encrypted_api_key=encrypted,
        instance=service.weknora_instance_fingerprint(),
        status=service.WORKSPACE_CONFIRMED,
    )
    FakeWorkspaceRepos(initial=[row]).install(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("已有可用映射时不得发起远端调用")

    api_key, mapping = await service.ensure_department_workspace(2, client=_client(handler))

    assert api_key == "sk-dept-existing"
    assert mapping is row


@pytest.mark.asyncio
async def test_ensure_provisions_self_checks_and_persists(credential_env, settings_env, monkeypatch) -> None:
    repos = FakeWorkspaceRepos().install(monkeypatch)
    _patch_department_name(monkeypatch)
    _patch_department_lock(monkeypatch)
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path, request.headers.get("x-api-key")))
        if request.method == "POST" and request.url.path.endswith("/tenants"):
            return httpx.Response(200, json={"data": {"id": 10035, "name": "研发一部", "api_key": "sk-dept-new"}})
        if request.method == "GET" and request.url.path.endswith("/knowledge-bases"):
            return httpx.Response(200, json={"data": []})
        raise AssertionError(f"unexpected request: {request.method} {request.url.path}")

    _patch_selfcheck(monkeypatch, handler)

    api_key, mapping = await service.ensure_department_workspace(2, client=_client(handler))

    assert api_key == "sk-dept-new"
    assert mapping.workspace_tenant_id == "10035"
    assert mapping.status == service.WORKSPACE_CONFIRMED
    # 开通走部署 Key,自检走部门专属 Key
    assert requests[0] == ("POST", "/api/v1/tenants", "sk-provision")
    assert requests[1][2] == "sk-dept-new"
    # 落库密文不可反读出明文 Key
    (payload,) = repos.created
    assert payload["encrypted_api_key"] != "sk-dept-new"
    assert service.decrypt_workspace_key(payload["encrypted_api_key"]) == "sk-dept-new"


@pytest.mark.asyncio
async def test_ensure_missing_api_key_in_response_is_uncertain(credential_env, settings_env, monkeypatch) -> None:
    FakeWorkspaceRepos().install(monkeypatch)
    _patch_department_name(monkeypatch)
    _patch_department_lock(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"id": 10036, "name": "研发一部"}})

    with pytest.raises(KBOperationError, match="人工核对"):
        await service.ensure_department_workspace(2, client=_client(handler))


@pytest.mark.asyncio
async def test_ensure_self_check_failure_marks_pending_review(credential_env, settings_env, monkeypatch) -> None:
    repos = FakeWorkspaceRepos().install(monkeypatch)
    _patch_department_name(monkeypatch)
    _patch_department_lock(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"data": {"id": 10037, "api_key": "sk-dept-bad"}})
        return httpx.Response(403, json={"error": "forbidden"})

    _patch_selfcheck(monkeypatch, handler)

    with pytest.raises(KBOperationError, match="自检失败"):
        await service.ensure_department_workspace(2, client=_client(handler))

    (payload,) = repos.replaced
    assert payload["workspace_tenant_id"] == "10037"
    assert payload["status"] == service.WORKSPACE_PENDING_REVIEW


@pytest.mark.asyncio
async def test_ensure_concurrent_create_reuses_winner(credential_env, settings_env, monkeypatch) -> None:
    winner = SimpleNamespace(
        department_id=2,
        workspace_tenant_id="ws-winner",
        workspace_name="研发一部",
        encrypted_api_key=service.encrypt_workspace_key("sk-dept-winner"),
        instance=service.weknora_instance_fingerprint(),
        status=service.WORKSPACE_CONFIRMED,
    )
    FakeWorkspaceRepos(race_winner=winner).install(monkeypatch)
    _patch_department_name(monkeypatch)
    _patch_department_lock(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"data": {"id": 10038, "api_key": "sk-dept-loser"}})
        return httpx.Response(200, json={"data": []})

    _patch_selfcheck(monkeypatch, handler)

    api_key, mapping = await service.ensure_department_workspace(2, client=_client(handler))

    # 竞态:本分支创建的空间成为残留,复用先落库者的专属 Key
    assert api_key == "sk-dept-winner"
    assert mapping.workspace_tenant_id == "ws-winner"
    assert mapping is winner


@pytest.mark.asyncio
async def test_ensure_reprovision_replaces_unusable_mapping(credential_env, settings_env, monkeypatch) -> None:
    """待核对/旧实例映射经重新开通后被覆盖,恢复路径可用(P1 回归)。"""
    stale = SimpleNamespace(
        department_id=2,
        workspace_tenant_id="ws-stale",
        workspace_name="研发一部",
        encrypted_api_key=service.encrypt_workspace_key("sk-dept-stale"),
        instance="0000000000000000",
        status=service.WORKSPACE_PENDING_REVIEW,
    )
    repos = FakeWorkspaceRepos(initial=[stale]).install(monkeypatch)
    _patch_department_name(monkeypatch)
    _patch_department_lock(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"data": {"id": 10040, "api_key": "sk-dept-fresh"}})
        return httpx.Response(200, json={"data": []})

    _patch_selfcheck(monkeypatch, handler)

    api_key, mapping = await service.ensure_department_workspace(2, client=_client(handler))

    assert api_key == "sk-dept-fresh"
    assert mapping.workspace_tenant_id == "10040"
    assert mapping.status == service.WORKSPACE_CONFIRMED
    # 重新开通走覆盖而非新增,不产生第二行
    assert repos.created == []
    assert len(repos.replaced) == 1
    assert repos.rows[2].workspace_tenant_id == "10040"


@pytest.mark.asyncio
async def test_resolve_returns_department_scoped_settings(credential_env, settings_env, monkeypatch) -> None:
    row = SimpleNamespace(
        department_id=3,
        workspace_tenant_id="ws-3",
        workspace_name="研发二部",
        encrypted_api_key=service.encrypt_workspace_key("sk-dept-3"),
        instance=service.weknora_instance_fingerprint(),
        status=service.WORKSPACE_CONFIRMED,
    )
    FakeWorkspaceRepos(initial=[row]).install(monkeypatch)

    settings, mapping = await service.resolve_department_settings(3)

    assert settings.api_key == "sk-dept-3"
    assert settings.base_url == SETTINGS.base_url
    assert mapping is row


@pytest.mark.asyncio
async def test_resolve_rejects_missing_mapping(settings_env, monkeypatch) -> None:
    FakeWorkspaceRepos().install(monkeypatch)

    with pytest.raises(KBOperationError, match="未开通"):
        await service.resolve_department_settings(9)


@pytest.mark.asyncio
async def test_resolve_rejects_pending_review_mapping(credential_env, settings_env, monkeypatch) -> None:
    row = SimpleNamespace(
        department_id=2,
        workspace_tenant_id="ws-p",
        encrypted_api_key=service.encrypt_workspace_key("sk-dept-p"),
        instance=service.weknora_instance_fingerprint(),
        status=service.WORKSPACE_PENDING_REVIEW,
    )
    FakeWorkspaceRepos(initial=[row]).install(monkeypatch)

    with pytest.raises(KBOperationError, match="人工核对"):
        await service.resolve_department_settings(2)


@pytest.mark.asyncio
async def test_resolve_rejects_instance_mismatch(credential_env, settings_env, monkeypatch) -> None:
    row = SimpleNamespace(
        department_id=2,
        workspace_tenant_id="ws-old-instance",
        encrypted_api_key=service.encrypt_workspace_key("sk-dept-old"),
        instance="0000000000000000",
        status=service.WORKSPACE_CONFIRMED,
    )
    FakeWorkspaceRepos(initial=[row]).install(monkeypatch)

    with pytest.raises(KBOperationError, match="服务地址"):
        await service.resolve_department_settings(2)


@pytest.mark.asyncio
async def test_resolve_rejects_undecryptable_ciphertext(credential_env, settings_env, monkeypatch) -> None:
    row = SimpleNamespace(
        department_id=2,
        workspace_tenant_id="ws-corrupt",
        encrypted_api_key="not-a-fernet-token",
        instance=service.weknora_instance_fingerprint(),
        status=service.WORKSPACE_CONFIRMED,
    )
    FakeWorkspaceRepos(initial=[row]).install(monkeypatch)

    with pytest.raises(KBOperationError, match="解密失败"):
        await service.resolve_department_settings(2)
