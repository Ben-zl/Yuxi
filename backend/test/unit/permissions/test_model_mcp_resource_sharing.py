from types import SimpleNamespace

import pytest

from yuxi.permissions.resource_permission import (
    ResourcePermission,
    normalize_permission_config,
    resolve_mcp_permission,
    resolve_model_provider_permission,
)
from yuxi.storage.postgres.models_business import MCPServer, ModelProvider


def _user(role: str, department_id: int, uid: str = "u-1"):
    return SimpleNamespace(role=role, department_id=department_id, uid=uid)


def _resource(scope: str, departments: list[int], created_by: str = "owner"):
    return SimpleNamespace(
        created_by=created_by,
        share_config={
            "version": 2,
            "read_scope": {"access_level": scope, "department_ids": departments, "user_uids": []},
            "manage_scope": None,
        },
    )


@pytest.mark.parametrize("resolver", [resolve_model_provider_permission, resolve_mcp_permission])
def test_department_resource_is_not_visible_to_another_department(resolver):
    resource = _resource("department", [10])

    assert resolver(_user("user", 10), resource) == ResourcePermission.READ
    assert resolver(_user("user", 11), resource) == ResourcePermission.NONE


def test_superadmin_can_manage_global_resource():
    resource = _resource("global", [])

    assert resolve_model_provider_permission(_user("superadmin", 999), resource) == ResourcePermission.MANAGE


def test_share_config_rejects_user_scope_for_model_and_mcp():
    with pytest.raises(ValueError, match="无权"):
        normalize_permission_config(
            {
                "version": 2,
                "read_scope": {"access_level": "user", "user_uids": ["u-1"]},
                "manage_scope": None,
            },
            allowed_access_levels={"global", "department"},
            strict=True,
        )


def test_sanitized_provider_omits_all_credential_fields():
    provider = ModelProvider(
        provider_id="shared-provider",
        display_name="Shared Provider",
        base_url="https://provider.example.test/v1",
        api_key_env="PROVIDER_API_KEY",
        api_key="secret-key",
        headers_json={"Authorization": "Bearer secret"},
        extra_json={"private_token": "secret-extra"},
    )

    sanitized = provider.to_dict(sanitize=True)

    assert sanitized["credential_status"] == "configured"
    assert not {"api_key_env", "api_key", "headers_json", "extra_json"} & sanitized.keys()


def test_sanitized_mcp_omits_all_connection_credential_fields():
    server = MCPServer(
        slug="shared-mcp",
        name="Shared MCP",
        transport="sse",
        url="https://mcp.example.test/sse",
        command="mcp-command",
        args=["--token", "secret-arg"],
        env={"MCP_TOKEN": "secret-env"},
        headers={"Authorization": "Bearer secret"},
    )

    sanitized = server.to_dict(sanitize=True)

    assert sanitized["credential_status"] == "configured"
    assert not {"command", "args", "env", "headers"} & sanitized.keys()
