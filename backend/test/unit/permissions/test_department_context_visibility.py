"""资源共享范围与部门上下文的纯函数行为测试。"""

from __future__ import annotations

from yuxi.permissions.resource_permission import scope_matches
from yuxi.services.department_context_service import DepartmentContext


def _context(department_id, role="user"):
    return DepartmentContext(7, "test-member", "测试成员", "user", department_id, "B", role, None, 0)


def test_department_scope_is_not_union():
    """部门级共享只匹配单一活动部门，不是全部成员部门的并集。"""
    actor = _context(12)
    assert not scope_matches(actor, {"access_level": "department", "department_ids": [11], "user_uids": []})
    assert scope_matches(actor, {"access_level": "department", "department_ids": [12], "user_uids": []})


def test_global_and_user_scopes_keep_original_semantics():
    actor = _context(12)
    assert scope_matches(actor, {"access_level": "global", "department_ids": [], "user_uids": []})
    assert scope_matches(actor, {"access_level": "user", "department_ids": [], "user_uids": ["test-member"]})
    other_uid = DepartmentContext(8, "other", "他人", "user", 12, "B", "user", None, 0)
    assert not scope_matches(other_uid, {"access_level": "user", "department_ids": [], "user_uids": ["test-member"]})


def test_context_without_department_matches_no_department_scope():
    actor = _context(None)
    assert not scope_matches(actor, {"access_level": "department", "department_ids": [12], "user_uids": []})
    assert scope_matches(actor, {"access_level": "global", "department_ids": [], "user_uids": []})


def test_superadmin_effective_role_compatible_with_admin_sets():
    """超管有效角色恒为 superadmin，与既有 admin 角色集合兼容。"""
    superadmin = DepartmentContext(1, "root", "超管", "superadmin", None, None, "superadmin", None, 0)
    assert superadmin.role in {"admin", "superadmin"}
