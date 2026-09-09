"""WeKnora 托管文档上传、状态同步与重新解析用例测试。"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from yuxi.knowledge.base import KBOperationError
from yuxi.knowledge.runtime import knowledge_base
from yuxi.knowledge.weknora import (
    WEKNORA_TERMINAL_STATUSES,
    build_weknora_file_ref,
    parse_weknora_file_ref,
    weknora_status_label,
)
from yuxi.services.weknora_knowledge_service import (
    reparse_weknora_file,
    sync_weknora_file_statuses,
    upload_weknora_file,
)

pytestmark = pytest.mark.unit

FINGERPRINT = "a967587a3500f032"
CONFIRMED_BINDING = {"instance": FINGERPRINT, "remote_kb_id": "remote-kb-1", "status": "confirmed"}


def _settings_env(monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_BACKEND", "weknora")
    monkeypatch.setenv("WEKNORA_BASE_URL", "http://weknora-app:8080/api/v1")
    monkeypatch.setenv("WEKNORA_API_KEY", "sk-test")


def _client(handler):
    from yuxi.knowledge.weknora import WeKnoraClient, WeKnoraSettings

    return WeKnoraClient(
        WeKnoraSettings(base_url="http://weknora-app:8080/api/v1", api_key="sk-test"),
        transport=httpx.MockTransport(handler),
    )


@pytest.fixture
def confirmed_kb(monkeypatch):
    detail = SimpleNamespace(
        kb_id="kb_w",
        kb_type="weknora",
        owning_department_id=2,
        remote_binding=dict(CONFIRMED_BINDING),
    )

    async def fake_info(kb_id, **kwargs):
        return detail if kb_id == "kb_w" else None

    monkeypatch.setattr(knowledge_base, "get_database_info", fake_info)
    return detail


def test_file_ref_roundtrip_and_rejects_foreign_item() -> None:
    ref = build_weknora_file_ref("rid-1", "规范 文档 v2.md")
    assert parse_weknora_file_ref(ref) == ("rid-1", "规范 文档 v2.md")
    with pytest.raises(ValueError, match="非 WeKnora 文件引用"):
        parse_weknora_file_ref("minio://bucket/object.md")


def test_status_labels_map_known_and_expose_unknown() -> None:
    assert weknora_status_label("completed") == "完成"
    assert weknora_status_label("pending") == "排队"
    assert weknora_status_label("brand_new_state") == "未知状态(brand_new_state)"
    assert weknora_status_label(None) == "未知状态(缺失)"
    assert "completed" in WEKNORA_TERMINAL_STATUSES


@pytest.mark.asyncio
async def test_upload_returns_ref_and_initial_status(monkeypatch, confirmed_kb) -> None:
    _settings_env(monkeypatch)
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        return httpx.Response(200, json={"data": {"id": "rk-1", "parse_status": "pending"}})

    result = await upload_weknora_file(
        "kb_w", filename="规范.md", content=b"# hello", content_type="text/markdown", client=_client(handler)
    )

    assert requests == [("POST", "/api/v1/knowledge-bases/remote-kb-1/knowledge/file")]
    assert result["remote_knowledge_id"] == "rk-1"
    assert result["ref"] == build_weknora_file_ref("rk-1", "规范.md")
    assert result["remote_status"] == "pending"
    assert result["size"] == 7


@pytest.mark.asyncio
async def test_upload_propagates_remote_duplicate(monkeypatch, confirmed_kb) -> None:
    _settings_env(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"error": "duplicate"})

    with pytest.raises(KBOperationError, match="相同内容"):
        await upload_weknora_file("kb_w", filename="a.md", content=b"x", client=_client(handler))


@pytest.mark.asyncio
async def test_upload_timeout_surfaces_uncertain_outcome(monkeypatch, confirmed_kb) -> None:
    _settings_env(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)

    with pytest.raises(KBOperationError, match="上传失败"):
        await upload_weknora_file("kb_w", filename="a.md", content=b"x", client=_client(handler))


@pytest.mark.asyncio
async def test_upload_missing_remote_id_is_uncertain(monkeypatch, confirmed_kb) -> None:
    _settings_env(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {}})

    with pytest.raises(KBOperationError, match="结果不确定"):
        await upload_weknora_file("kb_w", filename="a.md", content=b"x", client=_client(handler))


@pytest.mark.asyncio
async def test_status_sync_updates_active_files_and_skips_terminal(monkeypatch) -> None:
    _settings_env(monkeypatch)
    updates = []
    metas = [
        {"file_id": "f1", "status": "pending", "remote_knowledge_id": "rk-1", "is_folder": False},
        {"file_id": "f2", "status": "completed", "remote_knowledge_id": "rk-2", "is_folder": False},
        {"file_id": "d1", "status": "pending", "remote_knowledge_id": None, "is_folder": True},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert "rk-1" in str(request.url)
        assert "rk-2" not in str(request.url)
        return httpx.Response(200, json={"data": [{"id": "rk-1", "parse_status": "failed", "error_message": "boom"}]})

    async def fake_update_fields(self, *, file_id, data, kb_id=None):
        updates.append((file_id, dict(data)))

    monkeypatch.setattr(
        "yuxi.repositories.knowledge_file_repository.KnowledgeFileRepository.update_fields", fake_update_fields
    )

    synced = await sync_weknora_file_statuses("kb_w", metas, client=_client(handler))

    assert updates == [("f1", {"status": "failed", "error_message": "boom"})]
    assert synced[0]["status"] == "failed"
    assert synced[1]["status"] == "completed"


@pytest.mark.asyncio
async def test_status_sync_failure_keeps_local_values(monkeypatch) -> None:
    _settings_env(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("down", request=request)

    metas = [{"file_id": "f1", "status": "processing", "remote_knowledge_id": "rk-1", "is_folder": False}]

    synced = await sync_weknora_file_statuses("kb_w", metas, client=_client(handler))

    # 远端不可达保留本地旧值,不把故障伪装成终态
    assert synced[0]["status"] == "processing"


@pytest.mark.asyncio
async def test_reparse_updates_local_status_from_remote(monkeypatch, confirmed_kb) -> None:
    _settings_env(monkeypatch)
    record = SimpleNamespace(file_id="f1", kb_id="kb_w", remote_knowledge_id="rk-1")

    async def fake_get_by_file_id(self, file_id):
        return record

    monkeypatch.setattr(
        "yuxi.repositories.knowledge_file_repository.KnowledgeFileRepository.get_by_file_id",
        fake_get_by_file_id,
    )
    updates = []

    async def fake_update_fields(self, *, file_id, data, kb_id=None):
        updates.append((file_id, dict(data)))

    monkeypatch.setattr(
        "yuxi.repositories.knowledge_file_repository.KnowledgeFileRepository.update_fields", fake_update_fields
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/knowledge/rk-1/reparse"
        return httpx.Response(200, json={"data": {"parse_status": "processing"}})

    result = await reparse_weknora_file("kb_w", "f1", client=_client(handler))

    assert result == {"file_id": "f1", "status": "processing"}
    assert updates == [("f1", {"status": "processing", "error_message": None})]


@pytest.mark.asyncio
async def test_reparse_rejects_foreign_document(monkeypatch, confirmed_kb) -> None:
    _settings_env(monkeypatch)
    other_kb_record = SimpleNamespace(file_id="f2", kb_id="kb_other", remote_knowledge_id="rk-2")

    async def fake_get_by_file_id(self, file_id):
        return other_kb_record

    monkeypatch.setattr(
        "yuxi.repositories.knowledge_file_repository.KnowledgeFileRepository.get_by_file_id",
        fake_get_by_file_id,
    )

    with pytest.raises(KBOperationError, match="不属于"):
        await reparse_weknora_file("kb_w", "f2", client=_client(lambda request: httpx.Response(200)))


@pytest.mark.asyncio
async def test_delete_file_confirms_remote_then_local(monkeypatch, confirmed_kb) -> None:
    _settings_env(monkeypatch)
    record = SimpleNamespace(file_id="f1", kb_id="kb_w", is_folder=False, remote_knowledge_id="rk-1")
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        return httpx.Response(200, json={"data": {}})

    async def fake_get_by_file_id(self, file_id):
        return record

    monkeypatch.setattr(
        "yuxi.repositories.knowledge_file_repository.KnowledgeFileRepository.get_by_file_id",
        fake_get_by_file_id,
    )

    from yuxi.services.weknora_knowledge_service import delete_weknora_file

    result = await delete_weknora_file("kb_w", "f1", client=_client(handler))

    assert result["remote_knowledge_id"] == "rk-1"
    assert requests == [("DELETE", "/api/v1/knowledge/rk-1")]


@pytest.mark.asyncio
async def test_delete_file_tolerates_remote_404(monkeypatch, confirmed_kb) -> None:
    _settings_env(monkeypatch)
    record = SimpleNamespace(file_id="f1", kb_id="kb_w", is_folder=False, remote_knowledge_id="rk-gone")

    async def fake_get_by_file_id(self, file_id):
        return record

    monkeypatch.setattr(
        "yuxi.repositories.knowledge_file_repository.KnowledgeFileRepository.get_by_file_id",
        fake_get_by_file_id,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "gone"})

    from yuxi.services.weknora_knowledge_service import delete_weknora_file

    result = await delete_weknora_file("kb_w", "f1", client=_client(handler))
    assert result["file_id"] == "f1"


@pytest.mark.asyncio
async def test_delete_file_timeout_keeps_remote_contract_error(monkeypatch, confirmed_kb) -> None:
    _settings_env(monkeypatch)
    record = SimpleNamespace(file_id="f1", kb_id="kb_w", is_folder=False, remote_knowledge_id="rk-1")

    async def fake_get_by_file_id(self, file_id):
        return record

    monkeypatch.setattr(
        "yuxi.repositories.knowledge_file_repository.KnowledgeFileRepository.get_by_file_id",
        fake_get_by_file_id,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("down", request=request)

    from yuxi.services.weknora_knowledge_service import delete_weknora_file

    with pytest.raises(KBOperationError, match="删除未确认"):
        await delete_weknora_file("kb_w", "f1", client=_client(handler))


def test_add_file_record_uses_source_path_for_tree(monkeypatch) -> None:
    """登记时 source_path 优先生效,保留目录层级(builtin 虚拟目录行为对齐)。"""

    from yuxi.knowledge.implementations.weknora import WeKnoraKB

    kb = WeKnoraKB("/tmp/yuxi-weknora-test")
    persisted = {}

    async def fake_persist(file_id, meta):
        persisted.update(meta)

    monkeypatch.setattr(kb, "_persist_file_meta", fake_persist)
    import asyncio

    meta = asyncio.run(
        kb.add_file_record(
            "kb_w",
            "weknora://rk-9/%E7%BB%86%E5%88%99.md",
            params={"source_path": "子目录/细则.md", "content_hashes": {}, "file_sizes": {}},
            operator_id="u1",
            additional_params={},
        )
    )

    assert meta["filename"] == "子目录/细则.md"
    assert meta["remote_knowledge_id"] == "rk-9"
    assert meta["created_by"] == "u1"


@pytest.mark.asyncio
async def test_executor_delete_file_rejects_cross_kb_file_id(monkeypatch) -> None:
    """跨库 file_id 不得静默删除本地行。"""

    from yuxi.knowledge.implementations.weknora import WeKnoraKB

    kb = WeKnoraKB("/tmp/yuxi-weknora-test")
    deleted = []

    async def fake_get_by_file_id(self, file_id):
        return SimpleNamespace(file_id=file_id, kb_id="kb_other", is_folder=False, remote_knowledge_id="rk-x")

    async def fake_repo_delete(self, file_id):
        deleted.append(file_id)

    monkeypatch.setattr(
        "yuxi.repositories.knowledge_file_repository.KnowledgeFileRepository.get_by_file_id",
        fake_get_by_file_id,
    )
    monkeypatch.setattr(
        "yuxi.repositories.knowledge_file_repository.KnowledgeFileRepository.delete",
        fake_repo_delete,
    )

    with pytest.raises(KBOperationError, match="不属于"):
        await kb.delete_file("kb_w", "f1")

    assert deleted == []


def test_source_paths_product_payload_preserves_tree(monkeypatch) -> None:
    """前端真实载荷 source_paths(复数 dict)经注册分支翻译为逐项 source_path。"""

    from server.routers.knowledge_router import _params_for_uploaded_document_item

    ref = build_weknora_file_ref("rk-9", "细则.md")
    params = {
        "source_paths": {ref: "子目录/细则.md"},
        "content_hashes": {ref: "h1"},
        "parent_id": None,
    }

    item_params = _params_for_uploaded_document_item(ref, params)

    assert item_params["source_path"] == "子目录/细则.md"
    assert item_params["content_hashes"] == {ref: "h1"}
