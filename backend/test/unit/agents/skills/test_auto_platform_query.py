from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest
from yuxi.agents.skills.buildin import BUILTIN_SKILLS


def _auto_platform_query_dir() -> Path:
    """返回 Auto Platform Query 内置 Skill 的源目录。"""
    for spec in BUILTIN_SKILLS:
        if spec.slug == "auto-platform-query":
            return spec.source_dir
    raise AssertionError("auto-platform-query builtin skill spec not found")


def _load_config_module(skill_dir: Path) -> ModuleType:
    """从 Skill 源目录加载配置模块，避免依赖调用进程的工作目录。"""
    config_path = skill_dir / "scripts" / "utils" / "config.py"
    spec = importlib.util.spec_from_file_location("auto_platform_query_config", config_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_auto_platform_query_loads_bundled_automation_api(monkeypatch: pytest.MonkeyPatch) -> None:
    """内置 Skill 应从自己的目录加载 automation-api SDK。"""
    skill_dir = _auto_platform_query_dir()
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    monkeypatch.setattr(sys, "path", sys.path.copy())
    for module_name in list(sys.modules):
        if module_name == "automation_api" or module_name.startswith("automation_api."):
            monkeypatch.delitem(sys.modules, module_name)

    config_module = _load_config_module(skill_dir)
    config_module.init_automation_api(
        {
            "base_url": "https://example.invalid",
            "project_id": "fixture-project",
            "user_id": "fixture-user",
            "timeout": 1,
            "max_retries": 0,
        }
    )

    sdk_module = importlib.import_module("automation_api")
    sdk_path = Path(sdk_module.__file__).resolve()
    assert sdk_path.is_relative_to(skill_dir / "utils" / "automation-api")


def test_auto_platform_query_prioritizes_direct_cli_execution() -> None:
    """Skill 应先执行已打包 CLI，避免准备步骤耗尽 Agent 迭代次数。"""
    content = (_auto_platform_query_dir() / "SKILL.md").read_text(encoding="utf-8")

    assert content.index("## 执行契约") < content.index("## 🔥 核心原则")
    assert "python3 <skill-dir>/scripts/cli.py tasks" in content
    assert "pip install" not in content
