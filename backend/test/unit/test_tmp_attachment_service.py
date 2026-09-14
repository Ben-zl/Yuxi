from __future__ import annotations

import asyncio
import os
import tempfile
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault(
    "SAVE_DIR", os.path.join(os.environ.get("CLAUDE_JOB_DIR", tempfile.gettempdir()), "yuxi-test-saves")
)

from yuxi.services import conversation_service as service
from yuxi.services.run_submission_service import RunSubmissionAttachment

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def isolate_project_path_lock(monkeypatch):
    """单测的假数据库不模拟 PostgreSQL 路径锁。"""
    monkeypatch.setattr(service, "lock_project_workdir_changes", AsyncMock())
    monkeypatch.setattr(service, "_require_rollback_workdir_owner", AsyncMock())
    monkeypatch.setattr(
        service,
        "AgentRunRepository",
        lambda _db: SimpleNamespace(get_active_run_by_thread_for_user=AsyncMock(return_value=None)),
    )
    monkeypatch.setattr(
        service,
        "AgentRunRequestRepository",
        lambda _db: SimpleNamespace(has_queued_attachment_reference=AsyncMock(return_value=False)),
    )


class FakeUpload:
    def __init__(self, filename: str, content: bytes, content_type: str | None = None):
        self.filename = filename
        self.content_type = content_type
        self._content = content
        self._offset = 0

    async def seek(self, offset: int) -> None:
        self._offset = offset

    async def read(self, size: int = -1) -> bytes:
        if self._offset >= len(self._content):
            return b""
        end = len(self._content) if size < 0 else min(len(self._content), self._offset + size)
        chunk = self._content[self._offset : end]
        self._offset = end
        return chunk


class FakeMinioClient:
    KB_BUCKETS = {"documents": "knowledgebases"}

    def __init__(self):
        self.objects: dict[tuple[str, str], bytes] = {}
        self.uploads: list[dict] = []

    async def aupload_file(self, bucket_name: str, object_name: str, data: bytes, content_type: str | None = None):
        self.objects[(bucket_name, object_name)] = data
        self.uploads.append(
            {
                "bucket_name": bucket_name,
                "object_name": object_name,
                "data": data,
                "content_type": content_type,
            }
        )
        return SimpleNamespace(
            bucket_name=bucket_name,
            object_name=object_name,
            url=f"http://minio:9000/{bucket_name}/{object_name}",
        )

    async def adownload_file(self, bucket_name: str, object_name: str) -> bytes:
        try:
            return self.objects[(bucket_name, object_name)]
        except KeyError as exc:
            raise service.StorageError("missing object") from exc


@dataclass
class FakeConversation:
    id: int = 1
    uid: str = "user-1"
    agent_id: str = "agent-1"
    project_id: str = "project-1"
    status: str = "active"
    extra_metadata: dict | None = None


class FakeConversationRepository:
    def __init__(self, db):
        self.conversation = FakeConversation()
        self.attachments: list[dict] = []

    async def get_conversation_by_thread_id(self, thread_id: str):
        return self.conversation

    async def add_attachment(self, conversation_id: int, attachment_info: dict):
        self.attachments.append(attachment_info)
        return attachment_info

    async def add_attachments(self, conversation_id: int, attachment_infos: list[dict]):
        self.attachments.extend(attachment_infos)
        return attachment_infos

    async def get_attachments(self, conversation_id: int):
        return list(self.attachments)

    async def lock_attachments(self, conversation_id: int):
        return list(self.attachments)

    async def remove_attachment(self, conversation_id: int, file_id: str):
        before = len(self.attachments)
        self.attachments = [item for item in self.attachments if item.get("file_id") != file_id]
        return len(self.attachments) != before

    async def bind_attachments_to_request(self, conversation_id: int, request_id: str, file_ids: list[str]):
        selected = [item for item in self.attachments if item.get("file_id") in set(file_ids)]
        for item in selected:
            item["request_id"] = request_id
        return selected


class FakeWorkdir:
    relative_path = "projects/project-1"

    def __init__(self):
        self.writes: list[tuple[str, bytes]] = []
        self.deletes: list[str] = []
        self.directories = {"/"}

    def write_file(self, path: str, content: bytes):
        self.writes.append((path, content))
        return {"path": path}

    def copy_file_from_path(self, path: str, source_path: str, *, overwrite: bool = True):
        self.writes.append((path, Path(source_path).read_bytes()))
        return {"path": path}

    def delete(self, path: str):
        self.deletes.append(path)


class FakeDb:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


@pytest.fixture
def authorized_workdir(monkeypatch: pytest.MonkeyPatch) -> FakeWorkdir:
    workdir = FakeWorkdir()

    async def fake_ensure_workdir_available(**_kwargs):
        return workdir.relative_path

    monkeypatch.setattr(service, "ensure_conversation_workdir_available", fake_ensure_workdir_available)
    monkeypatch.setattr(service.Workdir, "open_existing", lambda *_args: workdir)
    return workdir


def test_materialize_tmp_attachment_files_uses_authorized_project_workdir():
    workdir = FakeWorkdir()

    record = service._materialize_tmp_attachment_files(
        thread_id="thread-1",
        uid="user-1",
        file_id="file-1",
        file_name="demo.pdf",
        file_content=b"pdf-bytes",
        parsed_markdown="# parsed",
        workdir=workdir,
    )

    assert [path for path, _content in workdir.writes] == [
        "/uploads/file-1_demo.pdf",
        "/uploads/attachments/file-1_demo.md",
    ]
    assert workdir.writes[0][1] == b"pdf-bytes"
    assert workdir.writes[1][1] == b"# parsed"
    assert record["original_path"] == "/home/gem/user-data/projects/project-1/uploads/file-1_demo.pdf"
    assert record["path"] == "/home/gem/user-data/projects/project-1/uploads/attachments/file-1_demo.md"
    assert record["storage_path"] == record["path"]


@pytest.mark.asyncio
async def test_delete_attachment_does_not_unlink_absolute_path_outside_workdir(
    monkeypatch,
    tmp_path,
):
    outside = tmp_path / "outside.txt"
    outside.write_text("must remain", encoding="utf-8")
    fake_repo = FakeConversationRepository(db=None)
    fake_repo.attachments = [
        {
            "file_id": "file-1",
            "storage_path": str(outside),
        }
    ]
    fake_workdir = FakeWorkdir()

    async def resolve_workdir(**_kwargs):
        return fake_workdir.relative_path

    async def invalidate_cache(_thread_id):
        return None

    monkeypatch.setattr(service, "ConversationRepository", lambda _db: fake_repo)
    monkeypatch.setattr(service, "resolve_conversation_workdir_path", resolve_workdir)
    monkeypatch.setattr(service.Workdir, "open_existing", lambda *_args: fake_workdir)
    monkeypatch.setattr(service, "invalidate_mention_cache", invalidate_cache)

    await service.delete_thread_attachment_view(
        thread_id="thread-1",
        file_id="file-1",
        db=FakeDb(),
        current_uid="user-1",
    )

    assert outside.read_text(encoding="utf-8") == "must remain"
    assert fake_workdir.deletes == []


@pytest.mark.asyncio
@pytest.mark.parametrize(("queued", "active"), [(True, False), (False, True)])
async def test_delete_attachment_rejects_request_or_run_still_using_file(
    monkeypatch, authorized_workdir, queued, active
):
    """被排队请求或活动 Run 引用的附件不得删除。"""
    repo = FakeConversationRepository(db=None)
    repo.attachments = [
        {
            "file_id": "file-1",
            "request_id": "request-1" if queued else None,
            "original_path": "/home/gem/user-data/projects/project-1/uploads/file.txt",
        }
    ]

    class RequestRepo:
        def __init__(self, _db):
            pass

        async def get_by_request_id(self, _request_id):
            return SimpleNamespace(status="queued") if queued else None

        async def has_queued_attachment_reference(self, **_kwargs):
            return False

    class RunRepo:
        def __init__(self, _db):
            pass

        async def get_active_run_by_thread_for_user(self, **_kwargs):
            return object() if active else None

    monkeypatch.setattr(service, "ConversationRepository", lambda _db: repo)
    monkeypatch.setattr(service, "AgentRunRequestRepository", RequestRepo, raising=False)
    monkeypatch.setattr(service, "AgentRunRepository", RunRepo)
    monkeypatch.setattr(
        service,
        "resolve_conversation_workdir_path",
        AsyncMock(return_value=authorized_workdir.relative_path),
    )
    monkeypatch.setattr(service, "invalidate_mention_cache", AsyncMock())
    with pytest.raises(service.HTTPException) as denied:
        await service.delete_thread_attachment_view(
            thread_id="thread-1",
            file_id="file-1",
            db=FakeDb(),
            current_uid="user-1",
        )
    assert denied.value.status_code == 409
    assert repo.attachments[0]["file_id"] == "file-1"
    assert authorized_workdir.deletes == []


@pytest.mark.asyncio
async def test_delete_attachment_preserves_file_when_commit_fails(monkeypatch, authorized_workdir):
    """数据库删除未提交成功时保留原附件文件。"""
    repo = FakeConversationRepository(db=None)
    repo.attachments = [
        {
            "file_id": "file-1",
            "original_path": "/home/gem/user-data/projects/project-1/uploads/file.txt",
        }
    ]

    class FailingDb(FakeDb):
        async def commit(self):
            raise RuntimeError("commit failed")

    monkeypatch.setattr(service, "ConversationRepository", lambda _db: repo)
    monkeypatch.setattr(
        service,
        "resolve_conversation_workdir_path",
        AsyncMock(return_value=authorized_workdir.relative_path),
    )
    monkeypatch.setattr(service, "invalidate_mention_cache", AsyncMock())
    with pytest.raises(RuntimeError, match="commit failed"):
        await service.delete_thread_attachment_view(
            thread_id="thread-1",
            file_id="file-1",
            db=FailingDb(),
            current_uid="user-1",
        )
    assert authorized_workdir.deletes == []


@pytest.mark.asyncio
async def test_delete_attachment_unlinks_only_after_commit_and_owner_recheck(monkeypatch, authorized_workdir):
    """成功删除只在持久提交且 Owner 再验证之后清理文件。"""
    repo = FakeConversationRepository(db=None)
    repo.attachments = [
        {
            "file_id": "file-1",
            "original_path": "/home/gem/user-data/projects/project-1/uploads/file.txt",
        }
    ]
    order = []

    class OrderedDb(FakeDb):
        async def commit(self):
            order.append("commit")
            await super().commit()

    async def recheck(_db, **_kwargs):
        order.append("owner")

    def delete(path):
        order.append("delete")
        authorized_workdir.deletes.append(path)

    monkeypatch.setattr(service, "ConversationRepository", lambda _db: repo)
    monkeypatch.setattr(
        service, "resolve_conversation_workdir_path", AsyncMock(return_value=authorized_workdir.relative_path)
    )
    monkeypatch.setattr(service, "_require_rollback_workdir_owner", recheck)
    monkeypatch.setattr(service, "invalidate_mention_cache", AsyncMock())
    monkeypatch.setattr(authorized_workdir, "delete", delete)

    await service.delete_thread_attachment_view(
        thread_id="thread-1",
        file_id="file-1",
        db=OrderedDb(),
        current_uid="user-1",
    )
    assert order == ["commit", "owner", "delete"]
    assert repo.attachments == []


@pytest.mark.asyncio
async def test_upload_tmp_attachment_writes_user_scoped_minio_object(monkeypatch):
    fake_minio = FakeMinioClient()
    monkeypatch.setattr(service, "get_minio_client", lambda: fake_minio)

    response = await service.upload_tmp_attachment_view(
        file=FakeUpload("demo.pdf", b"pdf-bytes", "application/pdf"),
        current_uid="user-1",
    )

    assert response["bucket_name"] == "knowledgebases"
    assert response["object_name"].startswith("tmp/chat_attachments/user-1/")
    assert response["parse_methods"][0] == "disable"
    assert fake_minio.objects[("knowledgebases", response["object_name"])] == b"pdf-bytes"


@pytest.mark.asyncio
async def test_parse_tmp_attachment_uses_selected_method_and_uploads_markdown(monkeypatch):
    fake_minio = FakeMinioClient()
    object_name = "tmp/chat_attachments/user-1/tmp-1/original/demo.pdf"
    fake_minio.objects[("knowledgebases", object_name)] = b"pdf-bytes"
    monkeypatch.setattr(service, "get_minio_client", lambda: fake_minio)

    parse_calls = []

    async def fake_parse(source: str, params: dict | None = None) -> str:
        parse_calls.append({"source": source, "params": params})
        return "# parsed"

    monkeypatch.setattr(service, "parse_document", fake_parse)

    response = await service.parse_tmp_attachment_view(
        object_name=object_name,
        file_name="demo.pdf",
        parse_method="disable",
        bucket_name="knowledgebases",
        current_uid="user-1",
    )

    assert parse_calls == [
        {
            "source": f"minio://knowledgebases/{object_name}",
            "params": {"ocr_engine": "disable"},
        }
    ]
    assert response["parsed_object_name"] == "tmp/chat_attachments/user-1/tmp-1/parsed/demo.md"
    assert fake_minio.objects[("knowledgebases", response["parsed_object_name"])] == b"# parsed"


@pytest.mark.asyncio
async def test_confirm_tmp_thread_attachments_materializes_original_and_parsed_files(monkeypatch, tmp_path: Path):
    fake_minio = FakeMinioClient()
    original_object = "tmp/chat_attachments/user-1/tmp-1/original/demo.pdf"
    parsed_object = "tmp/chat_attachments/user-1/tmp-1/parsed/demo.md"
    fake_minio.objects[("knowledgebases", original_object)] = b"pdf-bytes"
    fake_minio.objects[("knowledgebases", parsed_object)] = b"# parsed"
    fake_repo = FakeConversationRepository(db=None)
    fake_workdir = FakeWorkdir()

    monkeypatch.setattr(service, "get_minio_client", lambda: fake_minio)
    monkeypatch.setattr(service, "ConversationRepository", lambda db: fake_repo)

    async def fake_ensure_workdir_available(**_kwargs):
        return fake_workdir.relative_path

    monkeypatch.setattr(service, "ensure_conversation_workdir_available", fake_ensure_workdir_available)
    monkeypatch.setattr(service.Workdir, "open_existing", lambda *_args: fake_workdir)

    async def noop_invalidate(thread_id: str):
        return None

    monkeypatch.setattr(service, "invalidate_mention_cache", noop_invalidate)

    response = await service.confirm_tmp_thread_attachments_view(
        thread_id="thread-1",
        attachments=[
            {
                "file_name": "demo.pdf",
                "file_type": "application/pdf",
                "bucket_name": "knowledgebases",
                "object_name": original_object,
                "parsed_object_name": parsed_object,
                "truncated": False,
            }
        ],
        db=FakeDb(),
        current_uid="user-1",
    )

    [attachment] = response["attachments"]
    assert attachment["status"] == "parsed"
    original_name = Path(attachment["original_path"]).name
    markdown_name = Path(attachment["path"]).name
    assert original_name.endswith("_demo.pdf")
    assert markdown_name.endswith("_demo.md")
    assert fake_workdir.writes == [
        (f"/uploads/{original_name}", b"pdf-bytes"),
        (f"/uploads/attachments/{markdown_name}", b"# parsed"),
    ]
    stored = fake_repo.attachments[0]
    assert Path(stored["original_path"]).name == original_name
    assert stored["original_storage_path"] == stored["original_path"]
    assert stored["markdown_storage_path"] == stored["path"]
    assert stored["storage_path"] == stored["path"]


async def _async_none() -> None:
    return None


@pytest.mark.asyncio
async def test_direct_upload_keeps_duplicate_file_names_in_separate_workdir_paths(monkeypatch, authorized_workdir):
    """连续直传同名附件时，每个 file_id 必须拥有独立 backing file。"""
    fake_repo = FakeConversationRepository(db=None)
    monkeypatch.setattr(service, "ConversationRepository", lambda _db: fake_repo)
    monkeypatch.setattr(service, "invalidate_mention_cache", lambda _thread_id: _async_none())

    async def no_markdown(_upload):
        raise ValueError("not parsed")

    monkeypatch.setattr(service, "_convert_upload_to_markdown", no_markdown)

    first = await service.upload_thread_attachment_view(
        thread_id="thread-1",
        file=FakeUpload("report.txt", b"first", "text/plain"),
        db=FakeDb(),
        current_uid="user-1",
    )
    second = await service.upload_thread_attachment_view(
        thread_id="thread-1",
        file=FakeUpload("report.txt", b"second", "text/plain"),
        db=FakeDb(),
        current_uid="user-1",
    )

    assert first["file_id"] != second["file_id"]
    assert first["original_path"] != second["original_path"]
    assert authorized_workdir.writes == [
        (f"/uploads/{first['file_id']}_report.txt", b"first"),
        (f"/uploads/{second['file_id']}_report.txt", b"second"),
    ]


@pytest.mark.asyncio
async def test_direct_upload_repository_cancellation_removes_materialized_file(monkeypatch, authorized_workdir):
    """直传附件登记被取消时必须删除已物化文件。"""

    class CancellingConversationRepository(FakeConversationRepository):
        async def add_attachment(self, conversation_id: int, attachment_info: dict):
            raise asyncio.CancelledError

    fake_repo = CancellingConversationRepository(db=None)
    monkeypatch.setattr(service, "ConversationRepository", lambda _db: fake_repo)

    async def no_markdown(_upload):
        raise ValueError("not parsed")

    monkeypatch.setattr(service, "_convert_upload_to_markdown", no_markdown)

    with pytest.raises(asyncio.CancelledError):
        await service.upload_thread_attachment_view(
            thread_id="thread-1",
            file=FakeUpload("report.txt", b"report", "text/plain"),
            db=None,
            current_uid="user-1",
        )

    assert len(authorized_workdir.writes) == 1
    assert authorized_workdir.writes[0][0] in authorized_workdir.deletes


@pytest.mark.asyncio
async def test_direct_upload_conversion_cancellation_removes_original_file(monkeypatch, authorized_workdir):
    """原文件写入后转换任务被取消时必须补偿原文件。"""
    fake_repo = FakeConversationRepository(db=None)
    monkeypatch.setattr(service, "ConversationRepository", lambda _db: fake_repo)

    async def cancel_conversion(_upload):
        raise asyncio.CancelledError

    monkeypatch.setattr(service, "_convert_upload_to_markdown", cancel_conversion)

    with pytest.raises(asyncio.CancelledError):
        await service.upload_thread_attachment_view(
            thread_id="thread-1",
            file=FakeUpload("report.txt", b"report", "text/plain"),
            db=None,
            current_uid="user-1",
        )

    assert len(authorized_workdir.writes) == 1
    assert authorized_workdir.writes[0][0] in authorized_workdir.deletes


@pytest.mark.asyncio
async def test_direct_upload_partial_write_failure_removes_expected_file(monkeypatch, authorized_workdir):
    """copy 写完但抛异常时仍须按预生成 file_id 清理路径。"""
    fake_repo = FakeConversationRepository(db=None)
    monkeypatch.setattr(service, "ConversationRepository", lambda _db: fake_repo)
    original_write = service._write_workdir_file

    def fail_after_write(workdir, path: str, content: bytes):
        original_write(workdir, path, content)
        raise RuntimeError("copy failed")

    monkeypatch.setattr(service, "_write_workdir_file", fail_after_write)
    with pytest.raises(RuntimeError, match="copy failed"):
        await service.upload_thread_attachment_view(
            thread_id="thread-1",
            file=FakeUpload("report.txt", b"report", "text/plain"),
            db=FakeDb(),
            current_uid="user-1",
        )

    assert len(authorized_workdir.writes) == 1
    assert authorized_workdir.writes[0][0] in authorized_workdir.deletes


@pytest.mark.asyncio
async def test_direct_upload_commit_failure_rolls_back_file(monkeypatch, authorized_workdir):
    """数据库 commit 失败不能把未归属文件留在 Workdir。"""
    fake_repo = FakeConversationRepository(db=None)
    monkeypatch.setattr(service, "ConversationRepository", lambda _db: fake_repo)

    async def no_markdown(_upload):
        raise ValueError("not parsed")

    monkeypatch.setattr(service, "_convert_upload_to_markdown", no_markdown)
    monkeypatch.setattr(service, "_committed_attachment_visible", AsyncMock(return_value=False))

    class FailingDb(FakeDb):
        async def commit(self):
            raise RuntimeError("commit failed")

    db = FailingDb()
    with pytest.raises(RuntimeError, match="commit failed"):
        await service.upload_thread_attachment_view(
            thread_id="thread-1",
            file=FakeUpload("report.txt", b"report", "text/plain"),
            db=db,
            current_uid="user-1",
        )
    assert db.rollbacks == 1
    assert len(authorized_workdir.writes) == 1
    assert authorized_workdir.writes[0][0] in authorized_workdir.deletes


@pytest.mark.asyncio
async def test_direct_upload_cleanup_failure_is_attached_to_primary_error(monkeypatch, authorized_workdir):
    """Workdir 删除失败不能被吞掉或覆盖原始数据库错误。"""
    fake_repo = FakeConversationRepository(db=None)
    monkeypatch.setattr(service, "ConversationRepository", lambda _db: fake_repo)

    async def no_markdown(_upload):
        raise ValueError("not parsed")

    def fail_delete(_path: str):
        raise PermissionError("cannot delete")

    class FailingDb(FakeDb):
        async def commit(self):
            raise RuntimeError("commit failed")

    monkeypatch.setattr(service, "_convert_upload_to_markdown", no_markdown)
    monkeypatch.setattr(service, "_committed_attachment_visible", AsyncMock(return_value=False))
    monkeypatch.setattr(authorized_workdir, "delete", fail_delete)
    with pytest.raises(RuntimeError, match="commit failed") as exc_info:
        await service.upload_thread_attachment_view(
            thread_id="thread-1",
            file=FakeUpload("report.txt", b"report", "text/plain"),
            db=FailingDb(),
            current_uid="user-1",
        )

    assert exc_info.value.__notes__ == ["Attachment file cleanup also failed: ExceptionGroup"]


@pytest.mark.asyncio
async def test_direct_upload_commit_ack_failure_preserves_persisted_file(monkeypatch, authorized_workdir):
    """附件已提交但确认丢失时不得反向删除 backing file。"""
    fake_repo = FakeConversationRepository(db=None)
    monkeypatch.setattr(service, "ConversationRepository", lambda _db: fake_repo)
    monkeypatch.setattr(service, "invalidate_mention_cache", lambda _thread_id: _async_none())

    async def no_markdown(_upload):
        raise ValueError("not parsed")

    class FailingDb(FakeDb):
        async def commit(self):
            raise RuntimeError("commit acknowledgement lost")

    async def persisted(**_kwargs):
        return True

    monkeypatch.setattr(service, "_convert_upload_to_markdown", no_markdown)
    monkeypatch.setattr(service, "_committed_attachment_visible", persisted, raising=False)
    db = FailingDb()
    with pytest.raises(RuntimeError, match="acknowledgement lost"):
        await service.upload_thread_attachment_view(
            thread_id="thread-1",
            file=FakeUpload("report.txt", b"report", "text/plain"),
            db=db,
            current_uid="user-1",
        )
    assert len(authorized_workdir.writes) == 1
    assert authorized_workdir.deletes == []
    assert db.rollbacks == 0


@pytest.mark.asyncio
async def test_direct_upload_connection_loss_preserves_file_when_read_is_early(monkeypatch, authorized_workdir):
    """连接丢失后即时回读未见元数据，不代表服务端不会稍后完成提交。"""
    fake_repo = FakeConversationRepository(db=None)
    monkeypatch.setattr(service, "ConversationRepository", lambda _db: fake_repo)
    monkeypatch.setattr(service, "invalidate_mention_cache", lambda _thread_id: _async_none())
    monkeypatch.setattr(service, "_committed_attachment_visible", AsyncMock(return_value=False))

    async def no_markdown(_upload):
        raise ValueError("not parsed")

    class FailingDb(FakeDb):
        async def commit(self):
            raise OSError("connection lost")

    monkeypatch.setattr(service, "_convert_upload_to_markdown", no_markdown)
    with pytest.raises(OSError, match="connection lost"):
        await service.upload_thread_attachment_view(
            thread_id="thread-1",
            file=FakeUpload("report.txt", b"report", "text/plain"),
            db=FailingDb(),
            current_uid="user-1",
        )

    assert authorized_workdir.deletes == []


@pytest.mark.asyncio
async def test_conversation_attachment_cleanup_waits_for_rollback_after_repeated_cancel(authorized_workdir):
    """重复取消不能在数据库 rollback 完成前结束附件补偿。"""
    started = asyncio.Event()
    release = asyncio.Event()
    events: list[str] = []

    class Db:
        async def rollback(self):
            started.set()
            await release.wait()
            events.append("rollback")

    primary = asyncio.CancelledError()
    task = asyncio.create_task(
        service._rollback_materialized_attachments(
            Db(),
            authorized_workdir,
            ["/uploads/file-1.txt"],
            primary,
            uid="user-1",
            project_id="project-1",
        )
    )
    await started.wait()
    task.cancel()
    await asyncio.sleep(0.05)
    assert not task.done()
    release.set()
    await task
    assert events == ["rollback"]
    assert authorized_workdir.deletes == ["/uploads/file-1.txt"]


@pytest.mark.asyncio
async def test_tmp_confirm_repository_cancellation_removes_materialized_files(monkeypatch, authorized_workdir):
    """tmp confirm 批量登记被取消时必须删除整批已物化文件。"""

    class CancellingConversationRepository(FakeConversationRepository):
        async def add_attachments(self, conversation_id: int, attachment_infos: list[dict]):
            raise asyncio.CancelledError

    fake_minio = FakeMinioClient()
    first_object = "tmp/chat_attachments/user-1/tmp-1/original/first.txt"
    second_object = "tmp/chat_attachments/user-1/tmp-2/original/second.txt"
    fake_minio.objects[("knowledgebases", first_object)] = b"first"
    fake_minio.objects[("knowledgebases", second_object)] = b"second"
    fake_repo = CancellingConversationRepository(db=None)
    monkeypatch.setattr(service, "get_minio_client", lambda: fake_minio)
    monkeypatch.setattr(service, "ConversationRepository", lambda _db: fake_repo)

    with pytest.raises(asyncio.CancelledError):
        await service.confirm_tmp_thread_attachments_view(
            thread_id="thread-1",
            attachments=[
                {"object_name": first_object, "bucket_name": "knowledgebases"},
                {"object_name": second_object, "bucket_name": "knowledgebases"},
            ],
            db=None,
            current_uid="user-1",
        )

    assert len(authorized_workdir.writes) == 2
    assert {path for path, _content in authorized_workdir.writes} <= set(authorized_workdir.deletes)


@pytest.mark.asyncio
async def test_tmp_confirm_markdown_write_failure_removes_original_file(monkeypatch, authorized_workdir):
    """tmp confirm 的 Markdown 写入失败时不得遗留已写原文件。"""
    fake_minio = FakeMinioClient()
    original_object = "tmp/chat_attachments/user-1/tmp-1/original/report.pdf"
    parsed_object = "tmp/chat_attachments/user-1/tmp-1/parsed/report.md"
    fake_minio.objects[("knowledgebases", original_object)] = b"pdf"
    fake_minio.objects[("knowledgebases", parsed_object)] = b"# parsed"
    fake_repo = FakeConversationRepository(db=None)
    monkeypatch.setattr(service, "get_minio_client", lambda: fake_minio)
    monkeypatch.setattr(service, "ConversationRepository", lambda _db: fake_repo)
    original_write = service._write_workdir_file
    write_count = 0

    def fail_markdown_write(workdir, path: str, content: bytes):
        nonlocal write_count
        write_count += 1
        if write_count == 2:
            raise RuntimeError("markdown write failed")
        original_write(workdir, path, content)

    monkeypatch.setattr(service, "_write_workdir_file", fail_markdown_write)

    with pytest.raises(RuntimeError, match="markdown write failed"):
        await service.confirm_tmp_thread_attachments_view(
            thread_id="thread-1",
            attachments=[
                {
                    "object_name": original_object,
                    "parsed_object_name": parsed_object,
                    "bucket_name": "knowledgebases",
                }
            ],
            db=None,
            current_uid="user-1",
        )

    assert len(authorized_workdir.writes) == 1
    assert authorized_workdir.writes[0][0] in authorized_workdir.deletes


@pytest.mark.asyncio
async def test_tmp_confirm_commit_failure_removes_batch(monkeypatch, authorized_workdir):
    """批量附件 commit 失败时必须清理整批文件。"""
    fake_repo = FakeConversationRepository(db=None)
    fake_minio = FakeMinioClient()
    object_name = "tmp/chat_attachments/user-1/tmp-1/original/report.txt"
    fake_minio.objects[("knowledgebases", object_name)] = b"report"
    monkeypatch.setattr(service, "ConversationRepository", lambda _db: fake_repo)
    monkeypatch.setattr(service, "get_minio_client", lambda: fake_minio)
    monkeypatch.setattr(service, "invalidate_mention_cache", lambda _thread_id: _async_none())
    monkeypatch.setattr(service, "_committed_attachment_visible", AsyncMock(return_value=False))

    class FailingDb(FakeDb):
        async def commit(self):
            raise RuntimeError("commit failed")

    db = FailingDb()
    with pytest.raises(RuntimeError, match="commit failed"):
        await service.confirm_tmp_thread_attachments_view(
            thread_id="thread-1",
            attachments=[{"object_name": object_name, "bucket_name": "knowledgebases"}],
            db=db,
            current_uid="user-1",
        )
    assert db.rollbacks == 1
    assert len(authorized_workdir.writes) == 1
    assert authorized_workdir.writes[0][0] in authorized_workdir.deletes


@pytest.mark.asyncio
async def test_external_run_attachment_record_is_consumable_by_worker(monkeypatch):
    """外部 Run 落盘记录必须包含 Worker 使用的当前内容存储路径。"""
    from yuxi.agentscope import worker_job
    from yuxi.services import attachment_service

    fake_repo = FakeConversationRepository(db=None)
    fake_workdir = FakeWorkdir()
    monkeypatch.setattr(attachment_service, "ConversationRepository", lambda _db: fake_repo)
    monkeypatch.setattr(
        attachment_service,
        "ensure_conversation_workdir_available",
        lambda **_kwargs: fake_workdir.relative_path,
        raising=False,
    )

    async def fake_ensure_workdir_available(**_kwargs):
        return fake_workdir.relative_path

    monkeypatch.setattr(
        "yuxi.services.workdir_service.ensure_conversation_workdir_available",
        fake_ensure_workdir_available,
    )
    monkeypatch.setattr("yuxi.workspace.workdir.Workdir.open_existing", lambda *_args: fake_workdir)

    [record] = await attachment_service.persist_run_submission_attachments(
        conversation=fake_repo.conversation,
        uid=fake_repo.conversation.uid,
        attachments=(
            RunSubmissionAttachment(
                file_name="report.txt",
                media_type="text/plain",
                content=b"report",
            ),
        ),
        db=None,
    )

    class UploadClient:
        async def upload_workspace_file(self, *_args, source_path: str, destination: str, **_kwargs):
            assert source_path
            return destination

    monkeypatch.setattr(
        worker_job,
        "_trusted_attachment_upload_source",
        lambda *_args, **_kwargs: nullcontext(Path("/tmp/source")),
    )
    result = await worker_job._materialize_run_attachments(
        fake_repo,
        UploadClient(),
        run=SimpleNamespace(
            uid=fake_repo.conversation.uid,
            conversation_id=fake_repo.conversation.id,
            conversation_thread_id="thread-1",
            request_id="request-1",
        ),
        input_message=SimpleNamespace(
            content="read",
            extra_metadata={"attachment_file_ids": [record["file_id"]]},
        ),
        mapping=SimpleNamespace(
            agentscope_agent_id="agent-1",
            agentscope_session_id="session-1",
        ),
        workdir_path=fake_workdir.relative_path,
    )

    assert record["storage_path"] == record["path"]
    assert record["original_storage_path"] == record["original_path"]
    assert result.endswith(f"- /workspace/uploads/{record['file_id']}.txt")


@pytest.mark.asyncio
async def test_run_attachment_cancellation_after_file_write_removes_written_file(monkeypatch):
    """文件已写入但存储调用被取消时，Service 必须补偿该文件。"""
    from yuxi.services import attachment_service

    fake_repo = FakeConversationRepository(db=None)
    fake_workdir = FakeWorkdir()
    monkeypatch.setattr(attachment_service, "ConversationRepository", lambda _db: fake_repo)

    async def fake_ensure_workdir_available(**_kwargs):
        return fake_workdir.relative_path

    monkeypatch.setattr(
        "yuxi.services.workdir_service.ensure_conversation_workdir_available",
        fake_ensure_workdir_available,
    )
    monkeypatch.setattr("yuxi.workspace.workdir.Workdir.open_existing", lambda *_args: fake_workdir)
    original_write = attachment_service._write_workdir_file

    async def cancel_after_write(workdir, path: str, content: bytes):
        await original_write(workdir, path, content)
        raise asyncio.CancelledError

    monkeypatch.setattr(attachment_service, "_write_workdir_file", cancel_after_write)

    with pytest.raises(asyncio.CancelledError):
        await attachment_service.persist_run_submission_attachments(
            conversation=fake_repo.conversation,
            uid=fake_repo.conversation.uid,
            attachments=(
                RunSubmissionAttachment(
                    file_name="report.txt",
                    media_type="text/plain",
                    content=b"report",
                ),
            ),
            db=None,
        )

    assert len(fake_workdir.writes) == 1
    assert fake_workdir.deletes == [fake_workdir.writes[0][0]]


@pytest.mark.asyncio
async def test_run_attachment_database_cancellation_removes_all_written_files(monkeypatch):
    """附件登记被取消时，Service 必须补偿本批已写入文件。"""
    from yuxi.services import attachment_service

    class CancellingConversationRepository(FakeConversationRepository):
        async def add_attachments(self, conversation_id: int, attachment_infos: list[dict]):
            raise asyncio.CancelledError

    fake_repo = CancellingConversationRepository(db=None)
    fake_workdir = FakeWorkdir()
    monkeypatch.setattr(attachment_service, "ConversationRepository", lambda _db: fake_repo)

    async def fake_ensure_workdir_available(**_kwargs):
        return fake_workdir.relative_path

    monkeypatch.setattr(
        "yuxi.services.workdir_service.ensure_conversation_workdir_available",
        fake_ensure_workdir_available,
    )
    monkeypatch.setattr("yuxi.workspace.workdir.Workdir.open_existing", lambda *_args: fake_workdir)

    with pytest.raises(asyncio.CancelledError):
        await attachment_service.persist_run_submission_attachments(
            conversation=fake_repo.conversation,
            uid=fake_repo.conversation.uid,
            attachments=(
                RunSubmissionAttachment(
                    file_name="first.txt",
                    media_type="text/plain",
                    content=b"first",
                ),
                RunSubmissionAttachment(
                    file_name="second.txt",
                    media_type="text/plain",
                    content=b"second",
                ),
            ),
            db=None,
        )

    assert len(fake_workdir.writes) == 2
    assert set(fake_workdir.deletes) == {path for path, _content in fake_workdir.writes}


@pytest.mark.asyncio
async def test_run_attachment_cleanup_failure_is_attached_to_primary_error(monkeypatch):
    """附件 Service 删除失败必须在原异常上留下可观察诊断。"""
    from yuxi.services import attachment_service

    class FailingConversationRepository(FakeConversationRepository):
        async def add_attachments(self, conversation_id: int, attachment_infos: list[dict]):
            raise RuntimeError("database write failed")

    fake_repo = FailingConversationRepository(db=None)
    fake_workdir = FakeWorkdir()

    def fail_delete(_path: str):
        raise PermissionError("cannot delete")

    fake_workdir.delete = fail_delete
    monkeypatch.setattr(attachment_service, "ConversationRepository", lambda _db: fake_repo)

    async def fake_ensure_workdir_available(**_kwargs):
        return fake_workdir.relative_path

    monkeypatch.setattr(
        "yuxi.services.workdir_service.ensure_conversation_workdir_available",
        fake_ensure_workdir_available,
    )
    monkeypatch.setattr("yuxi.workspace.workdir.Workdir.open_existing", lambda *_args: fake_workdir)

    with pytest.raises(RuntimeError, match="database write failed") as exc_info:
        await attachment_service.persist_run_submission_attachments(
            conversation=fake_repo.conversation,
            uid=fake_repo.conversation.uid,
            attachments=(RunSubmissionAttachment(file_name="report.txt", media_type="text/plain", content=b"report"),),
            db=None,
        )

    assert exc_info.value.__notes__ == ["Run attachment cleanup also failed: ExceptionGroup"]


@pytest.mark.asyncio
async def test_parse_tmp_attachment_uses_object_name_for_type_validation(monkeypatch):
    fake_minio = FakeMinioClient()
    object_name = "tmp/chat_attachments/user-1/tmp-1/original/demo.docx"
    fake_minio.objects[("knowledgebases", object_name)] = b"docx-bytes"
    monkeypatch.setattr(service, "get_minio_client", lambda: fake_minio)

    with pytest.raises(service.HTTPException) as exc_info:
        await service.parse_tmp_attachment_view(
            object_name=object_name,
            file_name="demo.pdf",
            parse_method="disable",
            bucket_name="knowledgebases",
            current_uid="user-1",
        )

    assert exc_info.value.status_code == 400
    assert "PDF 和图片" in exc_info.value.detail


@pytest.mark.asyncio
async def test_parse_tmp_attachment_handles_url_metacharacters(monkeypatch):
    fake_minio = FakeMinioClient()
    object_name = "tmp/chat_attachments/user-1/tmp-1/original/q1?.pdf"
    fake_minio.objects[("knowledgebases", object_name)] = b"pdf-bytes"
    monkeypatch.setattr(service, "get_minio_client", lambda: fake_minio)

    parse_calls = []

    async def fake_parse(source: str, params: dict | None = None) -> str:
        parse_calls.append(source)
        return "# parsed"

    monkeypatch.setattr(service, "parse_document", fake_parse)

    response = await service.parse_tmp_attachment_view(
        object_name=object_name,
        file_name="ignored.pdf",
        parse_method="disable",
        bucket_name="knowledgebases",
        current_uid="user-1",
    )

    assert parse_calls == ["minio://knowledgebases/tmp/chat_attachments/user-1/tmp-1/original/q1%3F.pdf"]
    assert response["parsed_object_name"] == "tmp/chat_attachments/user-1/tmp-1/parsed/q1?.md"


@pytest.mark.asyncio
async def test_confirm_tmp_thread_attachments_rejects_non_parsed_object(monkeypatch, authorized_workdir):
    fake_minio = FakeMinioClient()
    original_object = "tmp/chat_attachments/user-1/tmp-1/original/demo.pdf"
    fake_minio.objects[("knowledgebases", original_object)] = b"pdf-bytes"
    fake_repo = FakeConversationRepository(db=None)

    monkeypatch.setattr(service, "get_minio_client", lambda: fake_minio)
    monkeypatch.setattr(service, "ConversationRepository", lambda db: fake_repo)

    with pytest.raises(service.HTTPException) as exc_info:
        await service.confirm_tmp_thread_attachments_view(
            thread_id="thread-1",
            attachments=[
                {
                    "file_name": "demo.pdf",
                    "file_type": "application/pdf",
                    "bucket_name": "knowledgebases",
                    "object_name": original_object,
                    "parsed_object_name": original_object,
                }
            ],
            db=None,
            current_uid="user-1",
        )

    assert exc_info.value.status_code == 400
    assert fake_repo.attachments == []


@pytest.mark.asyncio
async def test_confirm_tmp_thread_attachments_validates_batch_before_commit(monkeypatch, authorized_workdir):
    fake_minio = FakeMinioClient()
    valid_object = "tmp/chat_attachments/user-1/tmp-1/original/valid.pdf"
    missing_object = "tmp/chat_attachments/user-1/tmp-2/original/missing.pdf"
    fake_minio.objects[("knowledgebases", valid_object)] = b"pdf-bytes"
    fake_repo = FakeConversationRepository(db=None)

    monkeypatch.setattr(service, "get_minio_client", lambda: fake_minio)
    monkeypatch.setattr(service, "ConversationRepository", lambda db: fake_repo)

    with pytest.raises(service.HTTPException) as exc_info:
        await service.confirm_tmp_thread_attachments_view(
            thread_id="thread-1",
            attachments=[
                {"file_name": "valid.pdf", "bucket_name": "knowledgebases", "object_name": valid_object},
                {"file_name": "missing.pdf", "bucket_name": "knowledgebases", "object_name": missing_object},
            ],
            db=None,
            current_uid="user-1",
        )

    assert exc_info.value.status_code == 400
    assert fake_repo.attachments == []


@pytest.mark.asyncio
async def test_confirm_tmp_thread_attachments_keeps_duplicate_names_separate(monkeypatch, authorized_workdir):
    fake_minio = FakeMinioClient()
    first_object = "tmp/chat_attachments/user-1/tmp-1/original/report.pdf"
    second_object = "tmp/chat_attachments/user-1/tmp-2/original/report.pdf"
    fake_minio.objects[("knowledgebases", first_object)] = b"first"
    fake_minio.objects[("knowledgebases", second_object)] = b"second"
    fake_repo = FakeConversationRepository(db=None)

    monkeypatch.setattr(service, "get_minio_client", lambda: fake_minio)
    monkeypatch.setattr(service, "ConversationRepository", lambda db: fake_repo)

    async def noop_invalidate(thread_id: str):
        return None

    monkeypatch.setattr(service, "invalidate_mention_cache", noop_invalidate)

    response = await service.confirm_tmp_thread_attachments_view(
        thread_id="thread-1",
        attachments=[
            {"file_name": "report.pdf", "bucket_name": "knowledgebases", "object_name": first_object},
            {"file_name": "report.pdf", "bucket_name": "knowledgebases", "object_name": second_object},
        ],
        db=FakeDb(),
        current_uid="user-1",
    )

    first, second = response["attachments"]
    assert first["original_path"] != second["original_path"]
    assert authorized_workdir.writes == [
        (f"/uploads/{Path(first['original_path']).name}", b"first"),
        (f"/uploads/{Path(second['original_path']).name}", b"second"),
    ]
