import base64
import shutil
from contextlib import contextmanager

import pytest

from yuxi.agentscope import skill_snapshot

pytestmark = pytest.mark.unit


def _snapshot_file(path: str, content: bytes = b"# skill\n") -> dict:
    return {
        "path": path,
        "content_base64": base64.b64encode(content).decode("ascii"),
        "executable": False,
    }


def test_materialized_snapshot_rejects_slug_outside_temporary_root(tmp_path):
    """持久化快照中的 slug 不得改变物化目录边界。"""
    escaped = tmp_path / "escaped"
    snapshot = {
        "slug": str(escaped),
        "snapshot_directories": [],
        "snapshot_files": [_snapshot_file("SKILL.md")],
    }

    try:
        with pytest.raises(ValueError, match="slug"):
            with skill_snapshot.materialized_skill_source(snapshot):
                pass
    finally:
        shutil.rmtree(escaped, ignore_errors=True)

    assert not escaped.exists()


def test_capture_rejects_skill_tree_with_too_many_entries(monkeypatch, tmp_path):
    """提交期不得把超出条目上限的 Skill tree 写入运行快照。"""
    source_dir = tmp_path / "reporter"
    source_dir.mkdir()
    (source_dir / "SKILL.md").write_bytes(b"# skill\n")
    (source_dir / "extra.txt").write_bytes(b"x")
    monkeypatch.setattr(skill_snapshot, "MAX_SKILL_SNAPSHOT_ENTRIES", 1, raising=False)

    with pytest.raises(ValueError, match="条目"):
        skill_snapshot.capture_skill_tree({"slug": "reporter", "source_dir": str(source_dir)})


def test_capture_stops_enumerating_after_entry_limit(monkeypatch, tmp_path):
    """条目数超过限额后，不再继续遍历余下文件。"""
    source_dir = tmp_path / "reporter"
    source_dir.mkdir()
    for name in ("SKILL.md", "second", "third"):
        (source_dir / name).write_bytes(b"x")
    monkeypatch.setattr(skill_snapshot, "MAX_SKILL_SNAPSHOT_ENTRIES", 1)
    original_scandir = skill_snapshot.os.scandir
    visited = []

    @contextmanager
    def tracked_scandir(path):
        with original_scandir(path) as listing:

            def entries():
                for entry in listing:
                    visited.append(entry.name)
                    yield entry

            yield entries()

    monkeypatch.setattr(skill_snapshot.os, "scandir", tracked_scandir)
    with pytest.raises(ValueError, match="条目"):
        skill_snapshot.capture_skill_tree({"slug": "reporter", "source_dir": str(source_dir)})
    assert len(visited) == 2


def test_capture_rejects_skill_tree_over_total_size_limit(monkeypatch, tmp_path):
    """提交期必须在读取文件时限制 Skill tree 的未编码总字节数。"""
    source_dir = tmp_path / "reporter"
    source_dir.mkdir()
    (source_dir / "SKILL.md").write_bytes(b"12345")
    monkeypatch.setattr(skill_snapshot, "MAX_SKILL_SNAPSHOT_BYTES", 4, raising=False)

    with pytest.raises(ValueError, match="大小"):
        skill_snapshot.capture_skill_tree({"slug": "reporter", "source_dir": str(source_dir)})


def test_materialized_snapshot_rechecks_entry_limit(monkeypatch):
    """执行期不能信任数据库中已持久化的快照条目数量。"""
    monkeypatch.setattr(skill_snapshot, "MAX_SKILL_SNAPSHOT_ENTRIES", 1, raising=False)
    snapshot = {
        "slug": "reporter",
        "snapshot_directories": [],
        "snapshot_files": [
            _snapshot_file("SKILL.md"),
            _snapshot_file("extra.txt", b"x"),
        ],
    }

    with pytest.raises(ValueError, match="条目"):
        with skill_snapshot.materialized_skill_source(snapshot):
            pass


def test_materialized_snapshot_rechecks_total_size_limit(monkeypatch):
    """执行期必须限制解码后的总字节数，不能只依赖提交期校验。"""
    monkeypatch.setattr(skill_snapshot, "MAX_SKILL_SNAPSHOT_BYTES", 4, raising=False)
    snapshot = {
        "slug": "reporter",
        "snapshot_directories": [],
        "snapshot_files": [_snapshot_file("SKILL.md", b"12345")],
    }

    with pytest.raises(ValueError, match="大小"):
        with skill_snapshot.materialized_skill_source(snapshot):
            pass


@pytest.mark.parametrize(
    ("directories", "files"),
    [
        (["scripts"], [_snapshot_file("SKILL.md"), _snapshot_file("scripts")]),
        ([], [_snapshot_file("SKILL.md"), _snapshot_file("scripts"), _snapshot_file("scripts/run.sh")]),
    ],
)
def test_materialized_snapshot_rejects_file_directory_conflicts(directories, files):
    """同一路径或父路径不能同时被声明为文件和目录。"""
    snapshot = {
        "slug": "reporter",
        "snapshot_directories": directories,
        "snapshot_files": files,
    }

    with pytest.raises(ValueError, match="冲突"):
        with skill_snapshot.materialized_skill_source(snapshot):
            pass
