"""agentscope service 的异步 HTTP 客户端。

网关侧（api 进程/测试进程）通过它调用 worker 容器内运行的 agentscope
service；身份以 X-User-ID 头透传，接入统一认证后在网关注入。
"""

import json
from collections.abc import AsyncIterator
from urllib.parse import quote

import httpx

from yuxi.utils import logger


class AgentScopeServiceError(RuntimeError):
    """agentscope service 返回非成功状态。"""


class AgentScopeServiceClient:
    """薄客户端：不缓存业务状态，映射与幂等由调用方（runner/投影层）负责。"""

    def __init__(self, base_url: str, timeout: float = 60.0):
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def _headers(self, uid: str) -> dict:
        return {"X-User-ID": quote(uid)}

    async def _request(self, method: str, path: str, uid: str, **kwargs) -> httpx.Response:
        async with httpx.AsyncClient(
            base_url=self._base_url, timeout=self._timeout
        ) as client:
            resp = await client.request(method, path, headers=self._headers(uid), **kwargs)
        if resp.status_code >= 400:
            raise AgentScopeServiceError(
                f"{method} {path} 失败：{resp.status_code} {resp.text[:300]}"
            )
        return resp

    async def create_agent(self, uid: str, agent_request: dict) -> str:
        """按投影载荷创建智能体记录，返回 agent_id。幂等由映射层保证。"""
        resp = await self._request("POST", "/agent/", uid, json=agent_request)
        return resp.json()["agent_id"]

    async def create_credential(self, uid: str, data: dict) -> str:
        """创建凭据（data 为 credential 判别联合的 dict 形式），返回 credential_id。"""
        resp = await self._request("POST", "/credential/", uid, json={"data": data})
        return resp.json()["credential_id"]

    async def create_session(
        self, uid: str, agent_id: str, chat_model_config: dict
    ) -> str:
        """创建会话并绑定模型配置，返回 session_id。"""
        resp = await self._request(
            "POST",
            "/sessions/",
            uid,
            json={"agent_id": agent_id, "chat_model_config": chat_model_config},
        )
        return resp.json()["session_id"]

    async def set_permission_mode(
        self, uid: str, agent_id: str, session_id: str, mode: str
    ) -> None:
        """设置会话权限模式（如 accept_edits，跳过文件写工具的人工确认）。"""
        await self._request(
            "PATCH",
            f"/sessions/{session_id}",
            uid,
            params={"agent_id": agent_id},
            json={"permission_mode": mode},
        )

    async def trigger_chat(
        self, uid: str, agent_id: str, session_id: str, text: str
    ) -> None:
        """触发一轮对话（fire-and-forget），事件经 stream 端点消费。"""
        msg = {
            "role": "user",
            "name": "user",
            "content": [{"type": "text", "text": text}],
        }
        await self._request(
            "POST",
            "/chat/",
            uid,
            json={"agent_id": agent_id, "session_id": session_id, "input": msg},
        )

    async def list_messages(self, uid: str, agent_id: str, session_id: str) -> list:
        """读回会话消息历史（运行结束时已持久化）。"""
        resp = await self._request(
            "GET",
            f"/sessions/{session_id}/messages",
            uid,
            params={"agent_id": agent_id, "limit": 50},
        )
        return resp.json()["messages"]

    async def stream_events(
        self, uid: str, agent_id: str, session_id: str, read_timeout: float = 180.0
    ) -> AsyncIterator[dict]:
        """订阅会话事件流：先回放 replay log，再接 live 事件，直到连接关闭。

        以 REPLY_END 事件作为一轮对话的终止信号由调用方判断。
        """
        url = f"{self._base_url}/sessions/{session_id}/stream"
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=read_timeout)) as client:
            async with client.stream(
                "GET", url, params={"agent_id": agent_id}, headers=self._headers(uid)
            ) as resp:
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode(errors="replace")[:300]
                    raise AgentScopeServiceError(
                        f"GET stream 失败：{resp.status_code} {body}"
                    )
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    try:
                        yield json.loads(line[len("data: "):])
                    except ValueError:
                        logger.warning(f"忽略无法解析的事件行：{line[:120]}")
