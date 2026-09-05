from pathlib import Path

from yuxi.agents.backends.sandbox import paths as sandbox_paths
from yuxi.config import (
    get_legacy_storage_dir,
    get_runtime_dir,
    get_skill_data_dir,
    get_skill_projection_dir,
    get_user_data_dir,
)


def test_runtime_directory_uses_explicit_environment(monkeypatch, tmp_path: Path):
    """显式运行目录应由当前进程环境拥有。"""
    runtime_dir = tmp_path / "runtime" / "api"
    monkeypatch.setenv("YUXI_RUNTIME_DIR", str(runtime_dir))

    assert get_runtime_dir() == runtime_dir


def test_runtime_directory_default_does_not_fall_back_to_save_dir(monkeypatch, tmp_path: Path):
    """缺少配置时，可丢弃运行数据不得回落到持久保存目录。"""
    save_dir = tmp_path / "saves"
    monkeypatch.setenv("YUXI_LEGACY_STORAGE_DIR", str(save_dir))
    monkeypatch.delenv("YUXI_RUNTIME_DIR", raising=False)

    runtime_dir = get_runtime_dir()

    assert runtime_dir != get_legacy_storage_dir()
    assert save_dir not in runtime_dir.parents
    assert runtime_dir.name.startswith("yuxi-runtime-")


def test_skill_storage_directories_support_explicit_domain_mounts(monkeypatch, tmp_path: Path):
    """Skill 持久源与授权投影必须能作为独立存储域挂载。"""
    data_dir = tmp_path / "skill-data"
    projection_dir = tmp_path / "skill-projections"
    monkeypatch.setenv("YUXI_SKILL_DATA_DIR", str(data_dir))
    monkeypatch.setenv("YUXI_SKILL_PROJECTION_DIR", str(projection_dir))

    assert get_skill_data_dir() == data_dir
    assert get_skill_projection_dir() == projection_dir


def test_storage_defaults_do_not_derive_from_legacy_save_dir(monkeypatch, tmp_path: Path):
    legacy_dir = tmp_path / "legacy-saves"
    monkeypatch.setenv("YUXI_LEGACY_STORAGE_DIR", str(legacy_dir))
    monkeypatch.delenv("YUXI_USER_DATA_DIR", raising=False)
    monkeypatch.delenv("YUXI_SKILL_DATA_DIR", raising=False)
    monkeypatch.delenv("YUXI_SKILL_PROJECTION_DIR", raising=False)

    assert get_user_data_dir() == Path("user-data")
    assert get_skill_data_dir() == Path("skill-sources")
    assert get_skill_projection_dir() == Path("skill-projections")


def test_sandbox_paths_use_explicit_user_data_directory(monkeypatch, tmp_path: Path):
    """Sandbox 文件边界优先使用显式用户数据目录。"""
    user_data_dir = tmp_path / "user-data"
    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(user_data_dir))
    monkeypatch.setattr(sandbox_paths.conf, "save_dir", str(tmp_path / "saves"))

    assert sandbox_paths.sandbox_user_data_dir("thread-1") == user_data_dir / "thread-1" / "user-data"
    assert sandbox_paths.sandbox_uploads_dir("thread-1") == user_data_dir / "thread-1" / "user-data" / "uploads"
