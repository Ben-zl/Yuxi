"""进程级运行能力配置测试。"""

from __future__ import annotations

import pytest

from yuxi.config.runtime import (
    knowledge_api_enabled,
    knowledge_backend,
    knowledge_backend_ready,
    knowledge_capability_enabled,
    lite_mode_enabled,
    weknora_backend_config_missing,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("value", [" true ", "\tTRUE\n", " 1 "])
def test_lite_mode_owner_normalizes_supported_values(monkeypatch, value: str) -> None:
    """所有调用方共享的 Owner 必须统一处理大小写与边界空白。"""

    monkeypatch.setenv("LITE_MODE", value)

    assert lite_mode_enabled() is True
    assert knowledge_capability_enabled() is False


@pytest.mark.parametrize("value", ["", " false ", "0", "unexpected"])
def test_lite_mode_owner_rejects_other_values(monkeypatch, value: str) -> None:
    """非约定真值不能意外开启 LITE 能力边界。"""

    monkeypatch.setenv("LITE_MODE", value)

    assert lite_mode_enabled() is False
    assert knowledge_capability_enabled() is True


def _clear_backend_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KNOWLEDGE_BACKEND", raising=False)
    for name in ("WEKNORA_BASE_URL", "WEKNORA_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def test_knowledge_backend_defaults_to_builtin(monkeypatch) -> None:
    """未配置时默认 builtin,保持未接入环境的行为不变。"""

    _clear_backend_env(monkeypatch)

    assert knowledge_backend() == "builtin"
    assert knowledge_backend_ready() is True
    assert knowledge_api_enabled() is True


@pytest.mark.parametrize(
    ("value", "expected"),
    [("builtin", "builtin"), (" BUILTIN ", "builtin"), ("weknora", "weknora"), ("WeKnora\n", "weknora")],
)
def test_knowledge_backend_normalizes_supported_values(monkeypatch, value: str, expected: str) -> None:
    monkeypatch.setenv("KNOWLEDGE_BACKEND", value)

    assert knowledge_backend() == expected


@pytest.mark.parametrize("value", ["dify", " milvus ", "both", "weknora,builtin"])
def test_knowledge_backend_rejects_invalid_values(monkeypatch, value: str) -> None:
    """非法后端值必须在装配期抛错,不允许静默落回 builtin。"""

    monkeypatch.setenv("KNOWLEDGE_BACKEND", value)

    with pytest.raises(ValueError, match="KNOWLEDGE_BACKEND"):
        knowledge_backend()


def test_builtin_backend_ignores_weknora_config(monkeypatch) -> None:
    """builtin 模式无额外部署配置要求,残留 WEKNORA_* 配置不影响判定。"""

    _clear_backend_env(monkeypatch)
    monkeypatch.setenv("KNOWLEDGE_BACKEND", "builtin")

    assert weknora_backend_config_missing() == ["WEKNORA_BASE_URL", "WEKNORA_API_KEY"]
    assert knowledge_backend_ready() is True
    assert knowledge_api_enabled() is True


def test_weknora_backend_not_ready_when_config_missing(monkeypatch) -> None:
    """weknora 模式缺少部署配置时知识库链路必须 fail-closed。"""

    _clear_backend_env(monkeypatch)
    monkeypatch.setenv("KNOWLEDGE_BACKEND", "weknora")

    assert weknora_backend_config_missing() == ["WEKNORA_BASE_URL", "WEKNORA_API_KEY"]
    assert knowledge_backend_ready() is False
    assert knowledge_api_enabled() is False


def test_weknora_backend_ready_with_transport_config(monkeypatch) -> None:
    """地址与 API Key 齐备即视为传输层就绪,模型标识留给建库用例校验。"""

    _clear_backend_env(monkeypatch)
    monkeypatch.setenv("KNOWLEDGE_BACKEND", "weknora")
    monkeypatch.setenv("WEKNORA_BASE_URL", " http://weknora-app:8080/api/v1 ")
    monkeypatch.setenv("WEKNORA_API_KEY", " sk-local ")

    assert weknora_backend_config_missing() == []
    assert knowledge_backend_ready() is True
    assert knowledge_api_enabled() is True


def test_lite_mode_disables_knowledge_even_with_weknora_config(monkeypatch) -> None:
    """LITE 规则保留:即使 weknora 配置完整,LITE 模式下知识库链路仍关闭。"""

    monkeypatch.setenv("LITE_MODE", "true")
    monkeypatch.setenv("KNOWLEDGE_BACKEND", "weknora")
    monkeypatch.setenv("WEKNORA_BASE_URL", "http://weknora-app:8080/api/v1")
    monkeypatch.setenv("WEKNORA_API_KEY", "sk-local")

    assert knowledge_capability_enabled() is False
    assert knowledge_api_enabled() is False
