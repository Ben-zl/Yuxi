"""WeKnora 托管知识库内容维护权(can_write_content)解析测试。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from server.utils import knowledge_permissions
from yuxi.knowledge.read_models import KnowledgeBaseSummary
from yuxi.knowledge.weknora import BINDING_CONFIRMED
from yuxi.permissions import resolve_knowledge_content_write

pytestmark = pytest.mark.unit


def _kb(owning_department_id):
    return SimpleNamespace(
        kb_type="weknora",
        owning_department_id=owning_department_id,
        share_config={"version": 2, "read_scope": {"access_level": "global"}, "manage_scope": None},
        created_by="someone-else",
    )


def _user(role="user", department_id=2, uid="u1"):
    return SimpleNamespace(role=role, department_id=department_id, uid=uid)


@pytest.mark.parametrize(
    ("user", "expected"),
    [
        (_user(role="superadmin", department_id=None), True),  # 超级管理员全部可维护
        (_user(role="user", department_id=2), True),  # 归属部门普通成员
        (_user(role="admin", department_id=2), True),  # 归属部门管理员
        (_user(role="user", department_id=3), False),  # 其他部门(即使读范围 global)只读
        (_user(role="user", department_id=None), False),  # 无部门用户无隐式权限
    ],
)
def test_content_write_matrix(user, expected) -> None:
    assert resolve_knowledge_content_write(user, _kb(2)) is expected


def test_creator_identity_does_not_bypass_department_boundary() -> None:
    kb = _kb(2)
    kb.created_by = "u1"

    assert resolve_knowledge_content_write(_user(uid="u1", department_id=3), kb) is False


def test_builtin_kb_never_writable() -> None:
    assert resolve_knowledge_content_write(_user(role="admin", department_id=2), _kb(None)) is False


def test_serialize_emits_can_write_content_only_for_weknora() -> None:
    from server.utils.knowledge_response import serialize_knowledge_base

    weknora = KnowledgeBaseSummary(
        kb_id="kb_w",
        name="w",
        description=None,
        kb_type="weknora",
        embedding_model_spec=None,
        llm_model_spec=None,
        query_params={},
        additional_params={},
        share_config={},
        created_by=None,
        created_at=None,
        remote_binding={"status": BINDING_CONFIRMED},
        can_write_content=True,
    )
    builtin = KnowledgeBaseSummary(
        kb_id="kb_b",
        name="b",
        description=None,
        kb_type="milvus",
        embedding_model_spec=None,
        llm_model_spec=None,
        query_params={},
        additional_params={},
        share_config={},
        created_by=None,
        created_at=None,
    )

    assert serialize_knowledge_base(weknora)["can_write_content"] is True
    assert "can_write_content" not in serialize_knowledge_base(builtin)


@pytest.mark.asyncio
async def test_content_write_dependency_rejects_outside_department(monkeypatch) -> None:
    monkeypatch.setenv("KNOWLEDGE_BACKEND", "weknora")
    monkeypatch.setenv("WEKNORA_BASE_URL", "http://weknora-app:8080/api/v1")
    monkeypatch.setenv("WEKNORA_API_KEY", "sk-local")

    detail = SimpleNamespace(
        kb_id="kb_w",
        kb_type="weknora",
        owning_department_id=2,
        share_config={"version": 2, "read_scope": {"access_level": "global"}, "manage_scope": None},
        created_by="owner",
    )

    async def fake_info(kb_id):
        return detail

    monkeypatch.setattr(knowledge_permissions.knowledge_base, "get_database_info", fake_info)

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_permissions.require_knowledge_base_content_write("kb_w", _user(department_id=3))
    assert exc_info.value.status_code == 403

    granted = await knowledge_permissions.require_knowledge_base_content_write("kb_w", _user(department_id=2))
    assert granted is detail


@pytest.mark.asyncio
async def test_read_dependency_allows_member_only_in_weknora_mode(monkeypatch) -> None:
    async def fake_info(kb_id):
        return SimpleNamespace(
            kb_id=kb_id,
            kb_type="weknora",
            share_config={"version": 2, "read_scope": {"access_level": "global"}, "manage_scope": None},
            created_by="owner",
        )

    monkeypatch.setattr(knowledge_permissions.knowledge_base, "get_database_info", fake_info)
    member = _user(role="user", department_id=2)

    monkeypatch.setenv("KNOWLEDGE_BACKEND", "weknora")
    monkeypatch.setenv("WEKNORA_BASE_URL", "http://weknora-app:8080/api/v1")
    monkeypatch.setenv("WEKNORA_API_KEY", "sk-local")
    assert await knowledge_permissions.require_knowledge_base_read("kb_w", member) is member

    monkeypatch.setenv("KNOWLEDGE_BACKEND", "builtin")
    with pytest.raises(HTTPException) as exc_info:
        await knowledge_permissions.require_knowledge_base_read("kb_w", member)
    assert exc_info.value.status_code == 403
