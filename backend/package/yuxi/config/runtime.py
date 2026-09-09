"""进程级运行能力配置。"""

import os

KNOWLEDGE_BACKEND_BUILTIN = "builtin"
KNOWLEDGE_BACKEND_WEKNORA = "weknora"

_WEKNORA_REQUIRED_ENV = ("WEKNORA_BASE_URL", "WEKNORA_API_KEY")


def lite_mode_enabled() -> bool:
    """返回当前进程是否运行在轻量能力模式。"""

    return os.environ.get("LITE_MODE", "").strip().lower() in {"true", "1"}


def knowledge_capability_enabled() -> bool:
    """返回当前进程是否拥有知识库、图谱与评估能力。"""

    return not lite_mode_enabled()


def knowledge_backend() -> str:
    """返回当前知识库后端选择;非法配置直接抛错,让装配期失败暴露问题。"""

    backend = os.environ.get("KNOWLEDGE_BACKEND", "").strip().lower() or KNOWLEDGE_BACKEND_BUILTIN
    if backend not in (KNOWLEDGE_BACKEND_BUILTIN, KNOWLEDGE_BACKEND_WEKNORA):
        raise ValueError(f"KNOWLEDGE_BACKEND 仅接受 builtin|weknora,当前值: {backend!r}")
    return backend


def weknora_backend_config_missing() -> list[str]:
    """返回 weknora 模式缺失的必需部署配置变量名,不包含任何配置值。"""

    return [name for name in _WEKNORA_REQUIRED_ENV if not os.environ.get(name, "").strip()]


def knowledge_backend_ready() -> bool:
    """返回当前知识库后端部署配置是否可用;builtin 无额外部署配置要求。"""

    if knowledge_backend() == KNOWLEDGE_BACKEND_BUILTIN:
        return True
    return not weknora_backend_config_missing()


def knowledge_api_enabled() -> bool:
    """返回知识库 HTTP 路由链路是否可用(能力开启且后端配置就绪)。

    仅声明路由装配门控;Agent 工具与技能入口的后端门控在工具装配层另行收敛。
    """

    return knowledge_capability_enabled() and knowledge_backend_ready()
