"""Agent 管理面的静态 backend 元数据。"""

from yuxi.agents.context import BaseContext, resolve_agent_resource_options


class BaseAgent:
    """只描述可配置能力；实际执行统一由 AgentScope 服务承担。"""

    name = "base_agent"
    description = "base_agent"
    capabilities: list[str] = []
    metadata: dict = {}
    context_schema: type[BaseContext] = BaseContext

    @property
    def id(self) -> str:
        return self.__class__.__name__

    async def get_info(
        self,
        include_configurable_items: bool = True,
        user_role: str | None = None,
        db=None,
        user=None,
    ) -> dict:
        """返回管理端展示所需的 backend 信息和动态资源选项。"""
        configurable_items = {}
        if include_configurable_items:
            configurable_items = self.context_schema.get_configurable_items(user_role=user_role)
            if db is not None and user is not None:
                resource_fields = {
                    item["kind"]
                    for item in configurable_items.values()
                    if item.get("kind") in {"tools", "knowledges", "mcps", "skills", "subagents"}
                }
                options = await resolve_agent_resource_options(resource_fields, db=db, user=user)
                for item in configurable_items.values():
                    if item.get("kind") in options:
                        item["options"] = options[item["kind"]]

        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "metadata": dict(self.metadata),
            "configurable_items": configurable_items,
            "capabilities": list(self.capabilities),
        }
