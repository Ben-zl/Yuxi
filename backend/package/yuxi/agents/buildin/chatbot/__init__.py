"""通用智能助手 backend 元数据。"""

from yuxi.agents.base import BaseAgent
from yuxi.agents.buildin.chatbot.context import ChatBotContext


class ChatbotAgent(BaseAgent):
    """通用智能助手的管理面定义。"""

    name = "智能助手"
    description = "基础的对话机器人，可以回答问题，可在配置中启用需要的工具。"
    capabilities = ["file_upload", "files"]
    context_schema = ChatBotContext


__all__ = ["ChatBotContext", "ChatbotAgent"]
