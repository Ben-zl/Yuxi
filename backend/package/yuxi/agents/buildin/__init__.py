"""AgentScope 迁移后的静态 Agent backend 注册表。"""

from yuxi.agents.buildin.chatbot import ChatbotAgent
from yuxi.agents.buildin.subagent import SubAgentBackend
from yuxi.utils.singleton import SingletonMeta


class AgentManager(metaclass=SingletonMeta):
    """管理端 backend 注册表，不持有会话或运行时 graph。"""

    def __init__(self):
        self._backends = {backend.id: backend for backend in (ChatbotAgent(), SubAgentBackend())}

    def get_agent(self, agent_id: str, **_kwargs):
        return self._backends.get(agent_id)

    def get_agents(self) -> list:
        return list(self._backends.values())

    async def get_agents_info(self, include_configurable_items: bool = True) -> list[dict]:
        return [
            await backend.get_info(include_configurable_items=include_configurable_items)
            for backend in self.get_agents()
        ]

    async def reload_all(self) -> None:
        """静态元数据无需重载；保留管理调用的异步契约。"""


agent_manager = AgentManager()

__all__ = ["agent_manager"]
