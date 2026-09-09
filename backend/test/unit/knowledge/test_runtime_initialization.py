from __future__ import annotations

import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest
from yuxi.knowledge.manager import KnowledgeBaseManager

pytestmark = pytest.mark.unit


def test_knowledge_runtime_preserves_lite_mode(tmp_path):
    env = os.environ.copy()
    env["LITE_MODE"] = "1"
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json; from yuxi.knowledge.runtime import knowledge_base; "
                "from yuxi.knowledge.factory import KnowledgeBaseFactory; "
                "print(json.dumps({"
                "'manager': type(knowledge_base).__name__, "
                "'types': sorted(KnowledgeBaseFactory.get_available_types())"
                "}))"
            ),
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )

    loaded = json.loads(result.stdout.splitlines()[-1])
    assert loaded == {"manager": "KnowledgeBaseManager", "types": ["dify", "notion"]}


def _run_factory_types_subprocess(tmp_path, env_updates: dict[str, str | None]) -> str:
    """以独立进程读取执行器注册表,避免污染当前进程的 factory 单例。

    必须导入 yuxi.knowledge.runtime:执行器注册与后端值校验都发生在该模块装配期。
    """

    env = os.environ.copy()
    env.pop("LITE_MODE", None)
    for key, value in env_updates.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json; import yuxi.knowledge.runtime; "
                "from yuxi.knowledge.factory import KnowledgeBaseFactory; "
                "print(json.dumps(sorted(KnowledgeBaseFactory.get_available_types())))"
            ),
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.splitlines()[-1]


def test_weknora_backend_does_not_register_builtin_executor(tmp_path):
    """weknora 模式不得注册内置执行器,即使部署配置完整。"""

    loaded = json.loads(
        _run_factory_types_subprocess(
            tmp_path,
            {
                "KNOWLEDGE_BACKEND": "weknora",
                "WEKNORA_BASE_URL": "http://weknora-app:8080/api/v1",
                "WEKNORA_API_KEY": "sk-local",
            },
        )
    )

    assert loaded == ["dify", "notion"]


def test_builtin_backend_registers_builtin_executor(tmp_path):
    """builtin 模式执行器注册保持现状,milvus 可用。"""

    loaded = json.loads(_run_factory_types_subprocess(tmp_path, {"KNOWLEDGE_BACKEND": "builtin"}))

    assert loaded == ["dify", "milvus", "notion"]


def test_invalid_backend_fails_fast_at_assembly(tmp_path):
    """非法后端值必须在装配期抛错,不能静默落回 builtin。"""

    with pytest.raises(subprocess.CalledProcessError) as exc_info:
        _run_factory_types_subprocess(tmp_path, {"KNOWLEDGE_BACKEND": "dify"})

    assert "KNOWLEDGE_BACKEND" in exc_info.value.stderr


@pytest.mark.asyncio
async def test_initialize_creates_executors_without_loading_all_configs(monkeypatch):
    """initialize() 只创建已使用类型的执行器，不加载全部 KB 配置。"""
    manager = KnowledgeBaseManager("/tmp/yuxi-test")

    async def fake_get_all(_self):
        return [
            SimpleNamespace(kb_id="kb_1", kb_type="milvus"),
        ]

    fake_instance = SimpleNamespace()

    def fake_create(_kb_type, _work_dir):
        return fake_instance

    monkeypatch.setattr(
        "yuxi.repositories.knowledge_base_repository.KnowledgeBaseRepository.get_all",
        fake_get_all,
    )
    monkeypatch.setattr(
        "yuxi.knowledge.manager.KnowledgeBaseFactory.is_type_supported",
        classmethod(lambda cls, _kb_type: True),
    )
    monkeypatch.setattr(
        "yuxi.knowledge.manager.KnowledgeBaseFactory.create",
        staticmethod(fake_create),
    )

    await manager.initialize()

    assert "milvus" in manager.kb_instances


@pytest.mark.asyncio
async def test_initialize_propagates_failure_from_backend_already_in_use(monkeypatch, tmp_path):
    manager = KnowledgeBaseManager(str(tmp_path))

    async def fake_get_all(_self):
        return [SimpleNamespace(kb_id="kb_1", kb_type="milvus")]

    def fail_create(_kb_type, _work_dir):
        raise ConnectionError("milvus unavailable")

    monkeypatch.setattr(
        "yuxi.repositories.knowledge_base_repository.KnowledgeBaseRepository.get_all",
        fake_get_all,
    )
    monkeypatch.setattr(
        "yuxi.knowledge.manager.KnowledgeBaseFactory.is_type_supported",
        classmethod(lambda cls, _kb_type: True),
    )
    monkeypatch.setattr(
        "yuxi.knowledge.manager.KnowledgeBaseFactory.create",
        staticmethod(fail_create),
    )

    with pytest.raises(RuntimeError, match="milvus:ConnectionError"):
        await manager.initialize()


@pytest.mark.asyncio
async def test_initialize_rejects_persisted_unsupported_backend(monkeypatch, tmp_path):
    manager = KnowledgeBaseManager(str(tmp_path))

    async def fake_get_all(_self):
        return [SimpleNamespace(kb_id="kb_legacy", kb_type="removed-backend")]

    monkeypatch.setattr(
        "yuxi.repositories.knowledge_base_repository.KnowledgeBaseRepository.get_all",
        fake_get_all,
    )

    with pytest.raises(RuntimeError, match="removed-backend:unsupported"):
        await manager.initialize()
