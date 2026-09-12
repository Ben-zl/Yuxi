"""Provider/MCP 所有权不得越过当前共享边界。"""

from types import SimpleNamespace

import pytest

from yuxi.permissions.resource_permission import (
    resolve_agent_permission,
    resolve_mcp_permission,
    resolve_model_provider_permission,
)


@pytest.mark.parametrize("resolver", [resolve_model_provider_permission, resolve_mcp_permission])
@pytest.mark.parametrize(
    "scope,departments,owner,manage,expected",
    [
        ("department", [20], True, None, "none"),
        ("department", [10], True, None, "manage"),
        ("global", [], True, None, "read"),
        ("global", [], True, "department", "read"),
        ("global", [], False, "global", "read"),
        ("department", [10], False, None, "read"),
        ("department", [10], False, "department", "manage"),
    ],
)
def test_admin_obeys_current_resource_scope(resolver, scope, departments, owner, manage, expected):
    """移动、提升与同部门授权均使用当前范围。"""
    user = SimpleNamespace(uid="admin", role="admin", department_id=10)
    resource = SimpleNamespace(
        created_by="admin" if owner else "peer",
        share_config={
            "version": 2,
            "read_scope": {"access_level": scope, "department_ids": departments},
            "manage_scope": {"access_level": manage, "department_ids": [10]} if manage else None,
        },
    )
    assert resolver(user, resource).value == expected
    assert resolve_agent_permission(user, resource).value == ("manage" if owner or manage else "read")
