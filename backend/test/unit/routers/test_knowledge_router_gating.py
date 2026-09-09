"""知识库路由装配门控测试:weknora 部署配置不完整时路由不得注册。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_COLLECT_ROUTES = (
    "import json; from server.routers import router; print(json.dumps(sorted({route.path for route in router.routes})))"
)

# 聚合 router 的 path 不含 /api 前缀,前缀在 main.py 挂载时统一追加
_KNOWLEDGE_PATH_PREFIXES = ("/knowledge", "/evaluation", "/graph", "/workspace/knowledge")


def _backend_root() -> Path:
    """server 包所在目录(容器内 /app,宿主为 backend/)。"""

    for parent in Path(__file__).resolve().parents:
        if (parent / "server" / "routers").is_dir():
            return parent
    raise AssertionError("未定位到包含 server 包的后端根目录")


def _collect_route_paths(tmp_path: Path, env_updates: dict[str, str | None]) -> list[str]:
    env = os.environ.copy()
    env.pop("LITE_MODE", None)
    for key, value in env_updates.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    env["PYTHONPATH"] = os.pathsep.join(part for part in (str(_backend_root()), env.get("PYTHONPATH", "")) if part)
    result = subprocess.run(
        [sys.executable, "-c", _COLLECT_ROUTES],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout.splitlines()[-1])


def test_weknora_backend_without_config_registers_no_knowledge_routes(tmp_path):
    """weknora 模式缺少部署配置时,知识库路由组必须整体不注册。"""

    routes = _collect_route_paths(
        tmp_path,
        {"KNOWLEDGE_BACKEND": "weknora", "WEKNORA_BASE_URL": None, "WEKNORA_API_KEY": None},
    )

    for prefix in _KNOWLEDGE_PATH_PREFIXES:
        assert not any(path.startswith(prefix) for path in routes), prefix


def test_builtin_backend_registers_knowledge_routes(tmp_path):
    """builtin 模式路由注册保持现状。"""

    routes = _collect_route_paths(tmp_path, {"KNOWLEDGE_BACKEND": "builtin"})

    assert any(path.startswith("/knowledge") for path in routes)


def test_weknora_backend_with_complete_config_registers_knowledge_routes(tmp_path):
    """weknora 配置就绪后路由恢复注册,fail-closed 只针对缺配场景。"""

    routes = _collect_route_paths(
        tmp_path,
        {
            "KNOWLEDGE_BACKEND": "weknora",
            "WEKNORA_BASE_URL": "http://weknora-app:8080/api/v1",
            "WEKNORA_API_KEY": "sk-local",
        },
    )

    assert any(path.startswith("/knowledge") for path in routes)
