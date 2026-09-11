"""知识库后端模式过滤测试:两套后端数据不混用。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from yuxi.knowledge.manager import KnowledgeBaseManager

pytestmark = pytest.mark.unit


def _row(kb_id: str, kb_type: str) -> SimpleNamespace:
    return SimpleNamespace(
        kb_id=kb_id,
        name=kb_id,
        description=None,
        kb_type=kb_type,
        embedding_model_spec=None,
        llm_model_spec=None,
        query_params={},
        additional_params={},
        share_config=None,
        created_by=None,
        created_at=None,
        owning_department_id=None,
        remote_binding=None,
    )


@pytest.mark.parametrize(
    ("backend", "expected"),
    [("builtin", True), ("weknora", False)],
)
def test_builtin_kb_visibility_by_backend(monkeypatch, backend: str, expected: bool) -> None:
    monkeypatch.setenv("KNOWLEDGE_BACKEND", backend)

    assert KnowledgeBaseManager._kb_type_visible_in_current_backend("milvus") is expected


@pytest.mark.parametrize(
    ("backend", "expected"),
    [("builtin", False), ("weknora", True)],
)
def test_weknora_kb_visibility_by_backend(monkeypatch, backend: str, expected: bool) -> None:
    monkeypatch.setenv("KNOWLEDGE_BACKEND", backend)

    assert KnowledgeBaseManager._kb_type_visible_in_current_backend("weknora") is expected


async def _fake_get_all(self):
    return [_row("kb_builtin", "milvus"), _row("kb_remote", "weknora")]


async def test_get_databases_filters_by_current_backend(monkeypatch) -> None:
    """列表按当前后端过滤:进程 factory 注册态与本测试解耦。"""

    from yuxi.knowledge.implementations.weknora import WeKnoraKB

    monkeypatch.setenv("KNOWLEDGE_BACKEND", "weknora")
    monkeypatch.setattr("yuxi.repositories.knowledge_base_repository.KnowledgeBaseRepository.get_all", _fake_get_all)
    monkeypatch.setattr(
        "yuxi.knowledge.factory.KnowledgeBaseFactory.is_type_supported",
        classmethod(lambda cls, kb_type: True),
    )
    # 与进程注册态解耦:任意类型都解析为 WeKnoraKB 元数据(仅用于读取字段归一化)
    monkeypatch.setattr(
        "yuxi.knowledge.factory.KnowledgeBaseFactory.get_kb_class",
        classmethod(lambda cls, kb_type: WeKnoraKB),
    )

    databases = await KnowledgeBaseManager("/tmp/yuxi-test").get_databases()

    assert [db.kb_id for db in databases] == ["kb_remote"]

    monkeypatch.setenv("KNOWLEDGE_BACKEND", "builtin")
    databases = await KnowledgeBaseManager("/tmp/yuxi-test").get_databases()

    assert [db.kb_id for db in databases] == ["kb_builtin"]


@pytest.mark.parametrize(
    ("backend", "expected_initialized"),
    [("weknora", {"weknora"}), ("builtin", {"milvus", "dify"})],
)
async def test_initialize_skips_cross_backend_rows_without_failure(monkeypatch, backend, expected_initialized) -> None:
    """混合后端数据下初始化不失败:只初始化当前后端的执行器,另一后端的行视同不可见。"""

    from yuxi.knowledge.implementations.weknora import WeKnoraKB

    async def mixed_get_all(self):
        return [_row("kb_builtin", "milvus"), _row("kb_connector", "dify"), _row("kb_remote", "weknora")]

    monkeypatch.setenv("KNOWLEDGE_BACKEND", backend)
    monkeypatch.setenv("WEKNORA_BASE_URL", "http://weknora-app:8080/api/v1")
    monkeypatch.setenv("WEKNORA_API_KEY", "sk-local")
    monkeypatch.setattr("yuxi.repositories.knowledge_base_repository.KnowledgeBaseRepository.get_all", mixed_get_all)
    monkeypatch.setattr(
        "yuxi.knowledge.factory.KnowledgeBaseFactory.is_type_supported",
        classmethod(lambda cls, kb_type: True),
    )
    monkeypatch.setattr(
        "yuxi.knowledge.factory.KnowledgeBaseFactory.get_kb_class",
        classmethod(lambda cls, kb_type: WeKnoraKB),
    )
    initialized = set()
    manager = KnowledgeBaseManager("/tmp/yuxi-test")

    def fake_create_instance(kb_type):
        initialized.add(kb_type)
        return object()

    monkeypatch.setattr(manager, "_get_or_create_kb_instance", fake_create_instance)

    await manager.initialize()

    assert initialized == expected_initialized


async def test_check_accessible_treats_cross_backend_kb_as_missing(monkeypatch) -> None:
    """跨后端 kb_id 对当前模式不可访问,超级管理员也不例外。"""

    async def fake_get_by_kb_id(self, kb_id):
        return _row(kb_id, "weknora")

    monkeypatch.setenv("KNOWLEDGE_BACKEND", "builtin")
    monkeypatch.setattr(
        "yuxi.repositories.knowledge_base_repository.KnowledgeBaseRepository.get_by_kb_id", fake_get_by_kb_id
    )

    manager = KnowledgeBaseManager("/tmp/yuxi-test")
    superadmin = {"uid": "wkadmin", "role": "superadmin", "department_id": None}

    assert await manager.check_accessible(superadmin, "kb_remote") is False


def test_serialize_marks_pending_review_binding_not_connected() -> None:
    """待核对绑定不得伪装成已连接。"""

    from yuxi.knowledge.read_models import KnowledgeBaseSummary
    from yuxi.knowledge.weknora import BINDING_PENDING_REVIEW
    from server.utils.knowledge_response import serialize_knowledge_base

    pending = KnowledgeBaseSummary(
        kb_id="kb_pending",
        name="待核对库",
        description=None,
        kb_type="weknora",
        embedding_model_spec=None,
        llm_model_spec=None,
        query_params={},
        additional_params={},
        share_config={},
        created_by=None,
        created_at=None,
        remote_binding={"status": BINDING_PENDING_REVIEW, "instance": "x", "remote_kb_id": None},
    )
    confirmed = KnowledgeBaseSummary(
        kb_id="kb_ok",
        name="正常库",
        description=None,
        kb_type="weknora",
        embedding_model_spec=None,
        llm_model_spec=None,
        query_params={},
        additional_params={},
        share_config={},
        created_by=None,
        created_at=None,
        remote_binding={"status": "confirmed", "instance": "x", "remote_kb_id": "r1"},
    )

    assert serialize_knowledge_base(pending)["status"] == "绑定待核对"
    assert serialize_knowledge_base(pending)["binding_status"] == BINDING_PENDING_REVIEW
    assert serialize_knowledge_base(confirmed)["status"] == "已连接"
