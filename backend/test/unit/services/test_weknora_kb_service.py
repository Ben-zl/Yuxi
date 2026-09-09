"""WeKnora 托管知识库建库与删除用例测试(远端传输边界注入,不触达真实服务)。"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from yuxi.knowledge.base import KBNameConflictError, KBOperationError
from yuxi.knowledge.runtime import knowledge_base
from yuxi.knowledge.weknora import WeKnoraClient, WeKnoraSettings
from yuxi.services import weknora_kb_service
from yuxi.services.weknora_kb_service import (
    BINDING_CONFIRMED,
    BINDING_PENDING_REVIEW,
    create_weknora_database,
    delete_weknora_database,
)

pytestmark = pytest.mark.unit

SETTINGS = WeKnoraSettings(
    base_url="http://weknora-app:8080/api/v1",
    api_key="sk-test",
    embedding_model_id="emb-1",
    summary_model_id="sum-1",
)


@pytest.fixture
def env_ready(monkeypatch):
    monkeypatch.setenv("WEKNORA_BASE_URL", SETTINGS.base_url)
    monkeypatch.setenv("WEKNORA_API_KEY", "sk-test")
    monkeypatch.setenv("WEKNORA_EMBEDDING_MODEL_ID", "emb-1")
    monkeypatch.setenv("WEKNORA_SUMMARY_MODEL_ID", "sum-1")


class FakeRepos:
    """记录本地知识库仓储副作用的替身。"""

    def __init__(self):
        self.created: dict[str, dict] = {}
        self.updated: dict[str, dict] = {}
        self.deleted: list[str] = []

    def install(self, monkeypatch):
        repos = self

        async def fake_create(self, data):
            repos.created[data["kb_id"]] = data
            return SimpleNamespace(**data)

        async def fake_update(self, kb_id, data):
            repos.updated[kb_id] = data
            return SimpleNamespace(kb_id=kb_id)

        async def fake_delete(self, kb_id):
            repos.deleted.append(kb_id)

        async def fake_get_by_kb_id(self, kb_id):
            return None

        async def fake_department_get_by_id(self, department_id):
            return SimpleNamespace(id=department_id, name=f"dept-{department_id}")

        monkeypatch.setattr("yuxi.repositories.knowledge_base_repository.KnowledgeBaseRepository.create", fake_create)
        monkeypatch.setattr("yuxi.repositories.knowledge_base_repository.KnowledgeBaseRepository.update", fake_update)
        monkeypatch.setattr("yuxi.repositories.knowledge_base_repository.KnowledgeBaseRepository.delete", fake_delete)
        monkeypatch.setattr(
            "yuxi.repositories.knowledge_base_repository.KnowledgeBaseRepository.get_by_kb_id",
            fake_get_by_kb_id,
        )
        monkeypatch.setattr(
            "yuxi.repositories.department_repository.DepartmentRepository.get_by_id",
            fake_department_get_by_id,
        )
        return repos


def _client(handler) -> WeKnoraClient:
    return WeKnoraClient(SETTINGS, transport=httpx.MockTransport(handler))


def _ok_create_handler(remote_kb_id="remote-1"):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path.endswith("/knowledge-bases"):
            return httpx.Response(200, json={"data": {"id": remote_kb_id, "name": "研发知识库"}})
        if request.method == "GET":
            return httpx.Response(200, json={"data": {"id": remote_kb_id, "name": "研发知识库"}})
        raise AssertionError(f"unexpected request: {request.method} {request.url.path}")

    return handler


@pytest.mark.asyncio
async def test_create_confirms_binding_on_both_sides(env_ready, monkeypatch) -> None:
    repos = FakeRepos().install(monkeypatch)
    monkeypatch.setattr(knowledge_base, "database_name_exists", _async_false())
    detail = SimpleNamespace(kb_id="kb_x", kb_type="weknora")
    monkeypatch.setattr(knowledge_base, "get_database_info", _async_return(detail))

    result = await create_weknora_database(
        database_name="研发知识库",
        description="desc",
        owning_department_id=2,
        created_by="rd1admin",
        client=_client(_ok_create_handler()),
    )

    assert result is detail
    ((kb_id, record),) = repos.created.items()
    # 登记时即带归属部门与待核对绑定
    assert record["kb_type"] == "weknora"
    assert record["owning_department_id"] == 2
    assert record["remote_binding"]["status"] == BINDING_PENDING_REVIEW
    # 归属部门默认授权:本部门可读、本部门可管理
    assert record["share_config"]["read_scope"]["department_ids"] == [2]
    assert record["share_config"]["manage_scope"]["department_ids"] == [2]
    # 确认后绑定落定
    binding = repos.updated[kb_id]
    assert binding["remote_binding"]["status"] == BINDING_CONFIRMED
    assert binding["remote_binding"]["remote_kb_id"] == "remote-1"
    assert repos.deleted == []


@pytest.mark.asyncio
async def test_create_with_definite_remote_rejection_rolls_back_local(env_ready, monkeypatch) -> None:
    repos = FakeRepos().install(monkeypatch)
    monkeypatch.setattr(knowledge_base, "database_name_exists", _async_false())

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"error": "name exists"})

    with pytest.raises(KBOperationError, match="拒绝创建"):
        await create_weknora_database(
            database_name="冲突库",
            description="",
            owning_department_id=2,
            created_by="rd1admin",
            client=_client(handler),
        )

    # 远端 4xx 明确未创建资源,本地登记必须撤销
    assert len(repos.created) == 1
    assert repos.deleted == list(repos.created.keys())


@pytest.mark.asyncio
async def test_create_timeout_keeps_pending_review_binding(env_ready, monkeypatch) -> None:
    repos = FakeRepos().install(monkeypatch)
    monkeypatch.setattr(knowledge_base, "database_name_exists", _async_false())

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)

    with pytest.raises(KBOperationError, match="待核对"):
        await create_weknora_database(
            database_name="不确定库",
            description="",
            owning_department_id=2,
            created_by="rd1admin",
            client=_client(handler),
        )

    # 结果不确定:本地登记与待核对绑定保留,不得自动重试或删除
    ((kb_id, record),) = repos.created.items()
    assert record["remote_binding"]["status"] == BINDING_PENDING_REVIEW
    assert repos.deleted == []


@pytest.mark.asyncio
async def test_create_requires_model_configuration(monkeypatch) -> None:
    monkeypatch.setenv("WEKNORA_BASE_URL", SETTINGS.base_url)
    monkeypatch.setenv("WEKNORA_API_KEY", "sk-test")
    monkeypatch.delenv("WEKNORA_EMBEDDING_MODEL_ID", raising=False)
    monkeypatch.delenv("WEKNORA_SUMMARY_MODEL_ID", raising=False)

    with pytest.raises(KBOperationError, match="模型配置"):
        await create_weknora_database(
            database_name="缺模型库",
            description="",
            owning_department_id=2,
            created_by="rd1admin",
            client=_client(_ok_create_handler()),
        )


@pytest.mark.asyncio
async def test_create_rejects_unknown_department(env_ready, monkeypatch) -> None:
    FakeRepos().install(monkeypatch)

    async def missing_department(self, department_id):
        return None

    monkeypatch.setattr("yuxi.repositories.department_repository.DepartmentRepository.get_by_id", missing_department)

    with pytest.raises(KBOperationError, match="不存在"):
        await create_weknora_database(
            database_name="幽灵部门库",
            description="",
            owning_department_id=99,
            created_by="rd1admin",
            client=_client(_ok_create_handler()),
        )


@pytest.mark.asyncio
async def test_create_conflict_name_short_circuits_before_remote(env_ready, monkeypatch) -> None:
    repos = FakeRepos().install(monkeypatch)

    async def name_exists(name):
        return True

    monkeypatch.setattr(knowledge_base, "database_name_exists", name_exists)

    with pytest.raises(KBNameConflictError):
        await create_weknora_database(
            database_name="同名库",
            description="",
            owning_department_id=2,
            created_by="rd1admin",
            client=_client(_ok_create_handler()),
        )
    assert repos.created == {}


def _confirmed_binding_detail():
    return SimpleNamespace(
        kb_id="kb_bound",
        kb_type="weknora",
        owning_department_id=2,
        remote_binding={
            "instance": weknora_kb_service.weknora_instance_fingerprint(SETTINGS),
            "remote_kb_id": "remote-1",
            "status": BINDING_CONFIRMED,
        },
    )


@pytest.mark.asyncio
async def test_delete_confirms_remote_before_local_cleanup(env_ready, monkeypatch) -> None:
    repos = FakeRepos().install(monkeypatch)
    detail = _confirmed_binding_detail()
    monkeypatch.setattr(knowledge_base, "get_database_info", _async_return(detail))
    deleted_remote = []

    def handler(request: httpx.Request) -> httpx.Response:
        deleted_remote.append(request.url.path)
        return httpx.Response(200, json={"data": {}})

    result = await delete_weknora_database("kb_bound", client=_client(handler))

    assert result["message"] == "删除成功"
    assert deleted_remote == ["/api/v1/knowledge-bases/remote-1"]
    assert repos.deleted == ["kb_bound"]


@pytest.mark.asyncio
async def test_delete_treats_remote_404_as_already_gone(env_ready, monkeypatch) -> None:
    repos = FakeRepos().install(monkeypatch)
    monkeypatch.setattr(knowledge_base, "get_database_info", _async_return(_confirmed_binding_detail()))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "not found"})

    await delete_weknora_database("kb_bound", client=_client(handler))

    assert repos.deleted == ["kb_bound"]


@pytest.mark.asyncio
async def test_delete_timeout_keeps_local_record(env_ready, monkeypatch) -> None:
    repos = FakeRepos().install(monkeypatch)
    monkeypatch.setattr(knowledge_base, "get_database_info", _async_return(_confirmed_binding_detail()))

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)

    with pytest.raises(KBOperationError, match="保留"):
        await delete_weknora_database("kb_bound", client=_client(handler))

    assert repos.deleted == []


@pytest.mark.asyncio
async def test_delete_refuses_when_instance_fingerprint_changed(env_ready, monkeypatch) -> None:
    repos = FakeRepos().install(monkeypatch)
    stale = SimpleNamespace(
        kb_id="kb_stale",
        kb_type="weknora",
        remote_binding={
            "instance": "0000000000000000",
            "remote_kb_id": "remote-1",
            "status": BINDING_CONFIRMED,
        },
    )
    monkeypatch.setattr(knowledge_base, "get_database_info", _async_return(stale))
    requested = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.path)
        return httpx.Response(200, json={"data": {}})

    with pytest.raises(KBOperationError, match="实例"):
        await delete_weknora_database("kb_stale", client=_client(handler))

    # 换址后不得对远端发起任何删除
    assert requested == []
    assert repos.deleted == []


@pytest.mark.asyncio
async def test_delete_refuses_pending_review_binding(env_ready, monkeypatch) -> None:
    repos = FakeRepos().install(monkeypatch)
    pending = SimpleNamespace(
        kb_id="kb_pending",
        kb_type="weknora",
        remote_binding={"instance": "x", "remote_kb_id": None, "status": BINDING_PENDING_REVIEW},
    )
    monkeypatch.setattr(knowledge_base, "get_database_info", _async_return(pending))

    with pytest.raises(KBOperationError, match="远端绑定未确认"):
        await delete_weknora_database("kb_pending", client=_client(_ok_create_handler()))

    assert repos.deleted == []


def _async_false():
    async def false(value):
        return False

    return false


def _async_return(value):
    async def returns(*args, **kwargs):
        return value

    return returns


@pytest.mark.asyncio
async def test_update_syncs_remote_before_local(env_ready, monkeypatch) -> None:
    repos = FakeRepos().install(monkeypatch)
    detail = _confirmed_binding_detail()
    detail.kb_id = "kb_bound"
    monkeypatch.setattr(knowledge_base, "get_database_info", _async_return(detail))
    remote_calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        remote_calls.append((request.method, request.url.path))
        return httpx.Response(200, json={"data": {}})

    updated = SimpleNamespace(kb_id="kb_bound", kb_type="weknora")
    monkeypatch.setattr(knowledge_base, "update_database", _async_return(updated))

    result = await weknora_kb_service.update_weknora_database(
        "kb_bound", name="新名称", description="新描述", client=_client(handler)
    )

    assert result is updated
    assert remote_calls == [("PUT", "/api/v1/knowledge-bases/remote-1")]
    assert repos.deleted == []


@pytest.mark.asyncio
async def test_update_remote_failure_keeps_local_unchanged(env_ready, monkeypatch) -> None:
    FakeRepos().install(monkeypatch)
    monkeypatch.setattr(knowledge_base, "get_database_info", _async_return(_confirmed_binding_detail()))
    local_updated = []
    monkeypatch.setattr(
        knowledge_base,
        "update_database",
        _recorder(local_updated),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"error": "name conflict"})

    with pytest.raises(KBOperationError, match="同步失败"):
        await weknora_kb_service.update_weknora_database("kb_bound", name="冲突名", client=_client(handler))

    assert local_updated == []


def _recorder(calls):
    async def record(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(kb_id="kb_bound")

    return record


def test_admin_cannot_modify_share_config() -> None:
    """归属部门管理员不能配置跨部门共享(仅超级管理员)。"""
    from yuxi.knowledge.base import KBOperationError
    from yuxi.services.weknora_kb_service import validate_weknora_share_config_change

    with pytest.raises(KBOperationError, match="仅超级管理员"):
        validate_weknora_share_config_change(
            operator_role="admin",
            owning_department_id=2,
            share_config={"version": 2, "read_scope": {"access_level": "global"}, "manage_scope": None},
        )


def test_read_scope_must_keep_owning_department() -> None:
    from yuxi.knowledge.base import KBOperationError
    from yuxi.services.weknora_kb_service import validate_weknora_share_config_change

    with pytest.raises(KBOperationError, match="包含归属部门"):
        validate_weknora_share_config_change(
            operator_role="superadmin",
            owning_department_id=2,
            share_config={
                "version": 2,
                "read_scope": {"access_level": "department", "department_ids": [4], "user_uids": []},
                "manage_scope": None,
            },
        )


def test_manage_scope_cannot_exceed_owning_department() -> None:
    from yuxi.knowledge.base import KBOperationError
    from yuxi.services.weknora_kb_service import validate_weknora_share_config_change

    with pytest.raises(KBOperationError, match="整库管理"):
        validate_weknora_share_config_change(
            operator_role="superadmin",
            owning_department_id=2,
            share_config={
                "version": 2,
                "read_scope": {"access_level": "department", "department_ids": [2, 3, 4], "user_uids": []},
                "manage_scope": {"access_level": "department", "department_ids": [2, 3], "user_uids": []},
            },
        )


def test_superadmin_granting_extra_read_department_is_allowed() -> None:
    from yuxi.services.weknora_kb_service import validate_weknora_share_config_change

    validate_weknora_share_config_change(
        operator_role="superadmin",
        owning_department_id=2,
        share_config={
            "version": 2,
            "read_scope": {"access_level": "department", "department_ids": [2, 4], "user_uids": []},
            "manage_scope": {"access_level": "department", "department_ids": [2], "user_uids": []},
        },
    )
