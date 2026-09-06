"""发布元数据必须跨前后端与锁文件一致。"""

import json
import os
from pathlib import Path
import tomllib

import pytest

EXPECTED_RELEASE_VERSION = "0.7.2"


def _assert_versions(root: Path) -> None:
    """从独立包元数据与锁文件验证发布版本。"""
    frontend = json.loads((root / "web/package.json").read_text())["version"]
    assert frontend == EXPECTED_RELEASE_VERSION, "web/package.json"
    for path in ("backend/pyproject.toml", "backend/package/pyproject.toml"):
        assert tomllib.loads((root / path).read_text())["project"]["version"] == frontend, path
    packages = tomllib.loads((root / "backend/uv.lock").read_text())["package"]
    versions = {p["name"]: p["version"] for p in packages if p["name"] in {"yuxi", "yuxi-workspace"}}
    assert versions == {"yuxi": frontend, "yuxi-workspace": frontend}


def test_release_versions_match():
    """实际仓库的前后端与锁文件使用同一发布版本。"""
    root = Path(os.environ.get("YUXI_PROJECT_ROOT", Path(__file__).resolve().parents[4]))
    _assert_versions(root)


@pytest.mark.parametrize("stale", ["workspace", "package", "lock-package", "lock-workspace", "all"])
def test_release_guard_rejects_stale_backend_version(tmp_path, stale):
    """各来源单独或同时回退到旧版本时都必须失败。"""
    (tmp_path / "web").mkdir()
    (tmp_path / "backend/package").mkdir(parents=True)
    versions = {
        name: "0.7.1" if stale in {name, "all"} else "0.7.2"
        for name in ("web", "workspace", "package", "lock-package", "lock-workspace")
    }
    (tmp_path / "web/package.json").write_text(json.dumps({"version": versions["web"]}))
    for path, name in (("backend/pyproject.toml", "workspace"), ("backend/package/pyproject.toml", "package")):
        (tmp_path / path).write_text(f'[project]\nversion="{versions[name]}"\n')
    (tmp_path / "backend/uv.lock").write_text(
        f'[[package]]\nname="yuxi"\nversion="{versions["lock-package"]}"\n'
        f'[[package]]\nname="yuxi-workspace"\nversion="{versions["lock-workspace"]}"\n'
    )
    with pytest.raises(AssertionError):
        _assert_versions(tmp_path)
