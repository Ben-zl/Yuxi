"""Team 子智能体 backend 元数据。"""

from yuxi.agents.base import BaseAgent
from yuxi.agents.buildin.subagent.context import SubAgentContext

_SUBAGENT_DISABLED_TOOLS = frozenset({"present_artifacts", "ask_user_question"})


class SubAgentBackend(BaseAgent):
    """子智能体的管理面定义。"""

    name = "子智能体"
    description = "用于被主智能体通过 Team 调用的专用智能体后端。"
    capabilities = ["file_upload", "files"]
    context_schema = SubAgentContext

    async def get_info(self, **kwargs) -> dict:
        info = await super().get_info(**kwargs)
        tools_item = (info.get("configurable_items") or {}).get("tools")
        if isinstance(tools_item, dict):
            tools_item["options"] = [
                option
                for option in tools_item.get("options") or []
                if option.get("key") not in _SUBAGENT_DISABLED_TOOLS
            ]
        return info


__all__ = ["SubAgentBackend", "SubAgentContext"]
