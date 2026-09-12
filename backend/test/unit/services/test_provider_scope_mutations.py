"""模型供应商写入使用当前权限而非历史 owner。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from yuxi.models.providers import repository, service
from yuxi.permissions.resource_permission import ResourcePermissionDenied


@pytest.mark.parametrize("operation", ["update", "delete"])
@pytest.mark.parametrize(
    "scope,departments,owner,manage",
    [
        ("department", [20], True, None),
        ("global", [], True, None),
        ("global", [], False, {"access_level": "global"}),
        ("department", [10], False, None),
    ],
)
async def test_scope_denial_precedes_provider_mutation(monkeypatch, operation, scope, departments, owner, manage):
    """拒绝移动后的 owner、global 管理与未授权的同部门管理员。"""
    provider = SimpleNamespace(
        created_by="admin" if owner else "peer",
        share_config={
            "version": 2,
            "read_scope": {"access_level": scope, "department_ids": departments},
            "manage_scope": manage,
        },
    )
    monkeypatch.setattr(repository, "get_model_provider_by_resource_id", AsyncMock(return_value=provider))
    update, delete = AsyncMock(), AsyncMock()
    monkeypatch.setattr(service, "update_model_provider", update)
    monkeypatch.setattr(service, "delete_model_provider", delete)
    operator = SimpleNamespace(uid="admin", role="admin", department_id=10)
    with pytest.raises(ResourcePermissionDenied):
        if operation == "update":
            await service.update_provider_config(
                object(), "resource", {"display_name": "changed"}, "admin", operator=operator
            )
        else:
            await service.delete_provider_config(object(), "resource", operator=operator)
    update.assert_not_awaited()
    delete.assert_not_awaited()


async def test_builtin_provider_update_is_rejected_before_mutation(monkeypatch):
    """内置供应商的连接配置、范围和内置标记均不能从管理接口修改。"""
    provider = SimpleNamespace(
        is_builtin=True,
        created_by="system",
        share_config={
            "version": 2,
            "read_scope": {"access_level": "global", "department_ids": [], "user_uids": []},
            "manage_scope": None,
        },
    )
    monkeypatch.setattr(repository, "get_model_provider_by_resource_id", AsyncMock(return_value=provider))
    update = AsyncMock()
    monkeypatch.setattr(service, "update_model_provider", update)

    with pytest.raises(PermissionError, match="系统内置"):
        await service.update_provider_config(
            object(),
            "builtin-resource",
            {"base_url": "https://attacker.test/v1"},
            "root",
            operator=SimpleNamespace(uid="root", role="superadmin", department_id=None),
        )

    update.assert_not_awaited()


async def test_provider_create_cannot_forge_builtin_flag(monkeypatch):
    """Service 直接调用也只能创建普通供应商。"""
    create = AsyncMock(return_value=SimpleNamespace())
    monkeypatch.setattr(service, "create_model_provider", create)
    operator = SimpleNamespace(uid="root", role="superadmin", department_id=None)
    await service.create_provider_config(
        object(),
        {
            "provider_id": "siliconflow-cn",
            "display_name": "Shadow Builtin",
            "base_url": "https://api.example.test/v1",
            "is_builtin": True,
            "share_config": {
                "version": 2,
                "read_scope": {"access_level": "global", "department_ids": [], "user_uids": []},
                "manage_scope": None,
            },
        },
        "root",
        operator=operator,
    )
    assert create.await_args.args[1]["is_builtin"] is False


async def test_provider_update_cannot_promote_regular_provider(monkeypatch):
    update = AsyncMock()
    monkeypatch.setattr(service, "update_model_provider", update)
    with pytest.raises(ValueError, match="is_builtin"):
        await service.update_provider_config(
            object(),
            "regular-resource",
            {"is_builtin": True},
            "root",
            operator=SimpleNamespace(uid="root", role="superadmin", department_id=None),
        )
    update.assert_not_awaited()


@pytest.mark.parametrize("operation", ["create", "update", "delete"])
async def test_provider_mutations_require_operator_before_database_access(operation):
    with pytest.raises(PermissionError, match="当前操作者"):
        if operation == "create":
            await service.create_provider_config(object(), {"provider_id": "blocked"}, "admin")
        elif operation == "update":
            await service.update_provider_config(object(), "blocked", {"display_name": "blocked"}, "admin")
        else:
            await service.delete_provider_config(object(), "blocked")


async def test_provider_repository_public_queries_require_user_context():
    with pytest.raises(PermissionError, match="当前用户"):
        await repository.list_model_providers(object(), user=None)
    with pytest.raises(PermissionError, match="当前用户"):
        await repository.get_model_provider_reference(object(), "provider", user=None)
