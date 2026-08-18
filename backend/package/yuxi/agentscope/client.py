"""agentscope service 的异步 HTTP 客户端。

网关侧（api 进程/测试进程）通过它调用 worker 容器内运行的 agentscope
service；身份以 X-User-ID 头透传，接入统一认证后在网关注入。
"""

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import httpx

from yuxi.utils import logger


class AgentScopeServiceError(RuntimeError):
    """agentscope service 返回非成功状态。"""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class WorkspaceFileListing:
    """一次 workspace 递归枚举的条目及完整性。"""

    items: list[dict]
    truncated: bool = False


def _sniff_image_media_type(b64: str) -> str:
    """按 base64 前缀嗅探图片 MIME 类型（存储侧只存裸 base64 无类型）。"""
    head = b64[:10]
    if head.startswith("/9j/"):
        return "image/jpeg"
    if head.startswith("R0lGOD"):
        return "image/gif"
    if head.startswith("UklGR"):
        return "image/webp"
    return "image/png"


class AgentScopeServiceClient:
    """薄客户端：不缓存业务状态，映射与幂等由调用方（runner/投影层）负责。"""

    def __init__(self, base_url: str, timeout: float = 60.0):
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def _headers(self, uid: str) -> dict:
        return {"X-User-ID": quote(uid)}

    async def _request(self, method: str, path: str, uid: str, **kwargs) -> httpx.Response:
        async with httpx.AsyncClient(base_url=self._base_url, timeout=self._timeout) as client:
            resp = await client.request(method, path, headers=self._headers(uid), **kwargs)
        if resp.status_code >= 400:
            raise AgentScopeServiceError(
                f"{method} {path} 失败：{resp.status_code} {resp.text[:300]}",
                status_code=resp.status_code,
            )
        return resp

    async def create_agent(self, uid: str, agent_request: dict) -> str:
        """按投影载荷创建智能体记录，返回 agent_id。幂等由映射层保证。"""
        resp = await self._request("POST", "/agent/", uid, json=agent_request)
        return resp.json()["agent_id"]

    async def update_agent(self, uid: str, agent_id: str, agent_request: dict) -> None:
        """替换已有 Agent 的名称、系统提示词和 ReAct 配置。"""
        await self._request("PATCH", f"/agent/{agent_id}", uid, json=agent_request)

    async def create_credential(self, uid: str, data: dict) -> str:
        """创建凭据（data 为 credential 判别联合的 dict 形式），返回 credential_id。"""
        resp = await self._request("POST", "/credential/", uid, json={"data": data})
        return resp.json()["credential_id"]

    async def create_session(self, uid: str, agent_id: str, chat_model_config: dict) -> str:
        """创建会话并绑定模型配置，返回 session_id。"""
        resp = await self._request(
            "POST",
            "/sessions/",
            uid,
            json={"agent_id": agent_id, "chat_model_config": chat_model_config},
        )
        return resp.json()["session_id"]

    async def delete_session(self, uid: str, agent_id: str, session_id: str) -> None:
        """删除测试或回滚场景中的会话。"""
        await self._request(
            "DELETE",
            f"/sessions/{session_id}",
            uid,
            params={"agent_id": agent_id},
        )

    async def delete_agent(self, uid: str, agent_id: str) -> None:
        """删除测试或回滚场景中的 AgentScope agent。"""
        await self._request("DELETE", f"/agent/{agent_id}", uid)

    async def delete_credential(self, uid: str, credential_id: str) -> None:
        """删除测试或回滚场景中的 AgentScope credential。"""
        await self._request("DELETE", f"/credential/{credential_id}", uid)

    async def add_workspace_skill(self, uid: str, agent_id: str, session_id: str, skill_path: str) -> None:
        """把已授权 Skill 安装到指定会话 workspace。"""
        await self._request(
            "POST",
            "/workspace/skill",
            uid,
            params={"agent_id": agent_id, "session_id": session_id},
            json={"skill_path": skill_path},
        )

    async def list_workspace_skills(self, uid: str, agent_id: str, session_id: str) -> list[str]:
        """列出会话工作区当前安装的 Skill 名称。"""
        resp = await self._request(
            "GET",
            "/workspace/skill",
            uid,
            params={"agent_id": agent_id, "session_id": session_id},
        )
        return [str(item["name"]) for item in resp.json()]

    async def remove_workspace_skill(
        self,
        uid: str,
        agent_id: str,
        session_id: str,
        skill_name: str,
    ) -> None:
        """从会话工作区移除一个已取消配置的 Skill。"""
        await self._request(
            "DELETE",
            f"/workspace/skill/{quote(skill_name, safe='')}",
            uid,
            params={"agent_id": agent_id, "session_id": session_id},
        )

    async def upload_workspace_file(
        self,
        uid: str,
        agent_id: str,
        session_id: str,
        *,
        source_path: str,
        destination: str,
    ) -> str:
        """上传一个已落盘附件到指定会话 workspace。"""
        path = Path(source_path)
        if not path.is_file():
            raise FileNotFoundError(f"附件文件不存在: {path.name}")
        with path.open("rb") as stream:
            resp = await self._request(
                "POST",
                "/yuxi/workspace/file",
                uid,
                params={
                    "agent_id": agent_id,
                    "session_id": session_id,
                    "destination": destination,
                },
                files={"file": (path.name, stream, "application/octet-stream")},
            )
        return resp.json()["path"]

    async def set_permission_mode(self, uid: str, agent_id: str, session_id: str, mode: str) -> None:
        """设置会话权限模式（如 accept_edits，跳过文件写工具的人工确认）。"""
        await self._request(
            "PATCH",
            f"/sessions/{session_id}",
            uid,
            params={"agent_id": agent_id},
            json={"permission_mode": mode},
        )

    async def update_session_model(
        self,
        uid: str,
        agent_id: str,
        session_id: str,
        chat_model_config: dict,
    ) -> None:
        """替换已有会话的模型配置。"""
        await self._request(
            "PATCH",
            f"/sessions/{session_id}",
            uid,
            params={"agent_id": agent_id},
            json={"chat_model_config": chat_model_config},
        )

    async def trigger_chat(
        self,
        uid: str,
        agent_id: str,
        session_id: str,
        text: str,
        image_content: str | None = None,
    ) -> None:
        """触发一轮对话（fire-and-forget），事件经 stream 端点消费。

        image_content 为可选的裸 base64 图片，与文本一起作为多模态输入。
        """
        content: list[dict] = [{"type": "text", "text": text}]
        if image_content:
            content.append(
                {
                    "type": "data",
                    "source": {
                        "type": "base64",
                        "data": image_content,
                        "media_type": _sniff_image_media_type(image_content),
                    },
                }
            )
        msg = {"role": "user", "name": "user", "content": content}
        await self._request(
            "POST",
            "/chat/",
            uid,
            json={"agent_id": agent_id, "session_id": session_id, "input": msg},
        )

    async def resume_confirm(
        self,
        uid: str,
        agent_id: str,
        session_id: str,
        *,
        reply_id: str,
        tool_calls: list[dict],
        confirmed: list[bool],
    ) -> None:
        """恢复审批挂起的运行：每个工具调用保留自己的审批决定。"""
        if len(tool_calls) != len(confirmed):
            raise ValueError("工具调用数量与审批决定数量不一致")
        confirm_results = [
            {"confirmed": decision, "tool_call": call} for call, decision in zip(tool_calls, confirmed, strict=True)
        ]
        input_event = {
            "type": "USER_CONFIRM_RESULT",
            "reply_id": reply_id,
            "confirm_results": confirm_results,
        }
        await self._request(
            "POST",
            "/chat/",
            uid,
            json={"agent_id": agent_id, "session_id": session_id, "input": input_event},
        )

    async def resume_external_execution(
        self,
        uid: str,
        agent_id: str,
        session_id: str,
        *,
        reply_id: str,
        tool_calls: list[dict],
        answer: object,
    ) -> None:
        """把用户回答作为外部工具结果恢复挂起回复。"""
        if not tool_calls:
            raise ValueError("外部执行恢复缺少工具调用")
        answer_text = answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False)
        execution_results = [
            {
                "id": call.get("id"),
                "name": call.get("name"),
                "output": [{"type": "text", "text": answer_text}],
                "state": "success",
            }
            for call in tool_calls
        ]
        input_event = {
            "type": "EXTERNAL_EXECUTION_RESULT",
            "reply_id": reply_id,
            "execution_results": execution_results,
        }
        await self._request(
            "POST",
            "/chat/",
            uid,
            json={"agent_id": agent_id, "session_id": session_id, "input": input_event},
        )

    async def interrupt_session(self, uid: str, agent_id: str, session_id: str) -> None:
        """请求中断运行中或审批挂起中的会话（幂等）。"""
        await self._request(
            "POST",
            f"/sessions/{session_id}/interrupt",
            uid,
            params={"agent_id": agent_id},
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

    async def get_session(self, uid: str, agent_id: str, session_id: str) -> dict:
        """读取指定 Session 的持久化配置与轻量状态。"""
        resp = await self._request(
            "GET",
            "/sessions/",
            uid,
            params={"agent_id": agent_id},
        )
        for view in resp.json().get("sessions", []):
            session = view.get("session", {})
            if session.get("id") == session_id:
                return session
        raise AgentScopeServiceError(f"Session {session_id} 不存在", status_code=404)

    async def list_workspace_files(
        self,
        uid: str,
        agent_id: str,
        session_id: str,
        *,
        root: str = "",
        max_entries: int = 500,
    ) -> WorkspaceFileListing:
        """递归列出会话 workspace 条目，并显式报告数量截断。"""
        pending = [root]
        items: list[dict] = []
        truncated = False
        while pending:
            if len(items) >= max_entries:
                truncated = True
                break
            path = pending.pop(0)
            resp = await self._request(
                "GET",
                "/workspace/directories",
                uid,
                params={"agent_id": agent_id, "session_id": session_id, "path": path},
            )
            listing = resp.json()
            base = str(listing.get("path") or "").rstrip("/")
            for entry in listing.get("entries", []):
                if len(items) >= max_entries:
                    truncated = True
                    break
                item = dict(entry)
                item["path"] = f"{base}/{entry['name']}"
                items.append(item)
                if item.get("is_dir"):
                    pending.append(item["path"])
        if pending:
            truncated = True
        return WorkspaceFileListing(items=items, truncated=truncated)

    async def list_workspace_directory(
        self,
        uid: str,
        agent_id: str,
        session_id: str,
        path: str,
    ) -> dict:
        """列出会话 workspace 的一个目录层级。"""
        resp = await self._request(
            "GET",
            "/workspace/directories",
            uid,
            params={"agent_id": agent_id, "session_id": session_id, "path": path},
        )
        return resp.json()

    async def read_workspace_file(
        self,
        uid: str,
        agent_id: str,
        session_id: str,
        path: str,
        *,
        max_bytes: int = 25 * 1024 * 1024,
    ) -> bytes:
        """读取一个会话文件，并在服务进程限制最大响应体。"""
        resp = await self._request(
            "GET",
            "/workspace/files",
            uid,
            params={"agent_id": agent_id, "session_id": session_id, "path": path},
        )
        if len(resp.content) > max_bytes:
            raise ValueError("workspace 文件超过 25 MB")
        return resp.content

    async def delete_workspace_output(
        self,
        uid: str,
        agent_id: str,
        session_id: str,
        path: str,
    ) -> None:
        """删除会话 workspace 的一个产出文件或目录。"""
        await self._request(
            "DELETE",
            "/yuxi/workspace/output",
            uid,
            params={"agent_id": agent_id, "session_id": session_id, "path": path},
        )

    async def stream_events(
        self, uid: str, agent_id: str, session_id: str, read_timeout: float = 180.0
    ) -> AsyncIterator[dict]:
        """订阅会话事件流：先回放 replay log，再接 live 事件，直到连接关闭。

        以 REPLY_END 事件作为一轮对话的终止信号由调用方判断。
        """
        url = f"{self._base_url}/sessions/{session_id}/stream"
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=read_timeout)) as client:
            async with client.stream("GET", url, params={"agent_id": agent_id}, headers=self._headers(uid)) as resp:
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode(errors="replace")[:300]
                    raise AgentScopeServiceError(f"GET stream 失败：{resp.status_code} {body}")
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    try:
                        yield json.loads(line[len("data: ") :])
                    except ValueError:
                        logger.warning(f"忽略无法解析的事件行：{line[:120]}")
