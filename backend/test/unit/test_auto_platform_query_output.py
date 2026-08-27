"""Auto Platform Query 文件输出回归测试。"""

import importlib.util
from pathlib import Path

import pytest


OUTPUT_MODULE = (
    Path(__file__).parents[2] / "package/yuxi/agents/skills/buildin/auto-platform-query/scripts/utils/output.py"
)
PERFEYE_CACHE_MODULE = (
    Path(__file__).parents[2]
    / "package/yuxi/agents/skills/buildin/auto-platform-query/scripts/utils/perfeye_cache.py"
)
@pytest.fixture
def output_module():
    """从 Skill 源目录加载独立输出模块。"""
    spec = importlib.util.spec_from_file_location("auto_platform_query_output", OUTPUT_MODULE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def perfeye_cache_module():
    """从 Skill 源目录加载 PerfEye 缓存模块。"""
    spec = importlib.util.spec_from_file_location("auto_platform_query_perfeye_cache", PERFEYE_CACHE_MODULE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_print_output_creates_parent_directories(output_module, tmp_path: Path) -> None:
    """大结果写入嵌套临时目录时无需先执行 mkdir。"""
    output_file = tmp_path / "outputs/tmp/raw/task.json"

    output_module.print_output('{"ok": true}', str(output_file))

    assert output_file.read_text(encoding="utf-8") == '{"ok": true}'


def test_print_output_reports_original_io_error(output_module, tmp_path: Path, capsys) -> None:
    """文件写入失败时不得被 Rich 参数错误覆盖。"""
    output_file = tmp_path / "directory"
    output_file.mkdir()

    with pytest.raises(SystemExit) as exc_info:
        output_module.print_output("content", str(output_file))

    assert exc_info.value.code == 1
    assert "写入文件失败" in capsys.readouterr().err


def test_perfeye_cache_defaults_to_shared_outputs_tmp(perfeye_cache_module, monkeypatch, tmp_path: Path) -> None:
    """PerfEye UUID 中间数据应写入会话共享目录，供其他子 Agent 读取。"""
    shared_tmp = tmp_path / "outputs/tmp"
    monkeypatch.setattr(perfeye_cache_module, "DEFAULT_CACHE_DIR", shared_tmp)

    cache = perfeye_cache_module.PerfeyeCache()
    perfeye_file = cache.save_task_uuids(169724, {"case": {"di_1145": "uuid-1"}})

    assert perfeye_file == str(shared_tmp / "perfeye_169724.json")
    assert (shared_tmp / "perfeye_169724.json").is_file()
