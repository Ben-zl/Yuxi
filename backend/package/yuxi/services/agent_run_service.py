"""AgentRun lifecycle service.

This module owns the durable ``AgentRun`` contract: validating the run scope,
persisting the input message, creating the run row, enqueueing worker execution,
streaming run events, loading final results and requesting cancellation.

Keep source-specific orchestration outside this file. Normal chat, external
invocation and subagent tools may all create AgentRun records, but each caller
should translate its own request shape into this module's public run APIs first.
The worker then executes every run through the same queue and ``chat_service``
runtime path, so this module must not depend on agent-call, evaluation or
subagent presentation details.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Literal

from arq.constants import (
    abort_jobs_ss,
    default_queue_name,
    in_progress_key_prefix,
    job_key_prefix,
    result_key_prefix,
    retry_key_prefix,
)
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from yuxi.agents.buildin import agent_manager
from yuxi.agents.tool_approval import DEFAULT_TOOL_APPROVAL_MODE, normalize_tool_approval_mode
from yuxi.config.options import system_options
from yuxi.models.providers.cache import ModelInfo, model_cache
from yuxi.models.providers.repository import get_model_provider_by_resource_id
from yuxi.models.providers.service import resolve_api_key
from yuxi.repositories.agent_repository import AgentRepository
from yuxi.repositories.agent_run_output_repository import AgentRunOutputRepository
from yuxi.repositories.agent_run_repository import TERMINAL_RUN_STATUSES, AgentRunRepository
from yuxi.repositories.conversation_repository import ConversationRepository
from yuxi.services.input_message_service import (
    AgentRunInputMessage,
    build_resume_input_message,
)
from yuxi.services.agent_run_manifest_service import (
    RUN_MANIFEST_INPUT_KEY,
    build_submission_manifest_result,
    compute_manifest_fingerprint,
)
from yuxi.services.langfuse_service import get_trace_url_by_id_async
from yuxi.services.run_queue_service import (
    build_run_event_envelope,
    get_arq_pool,
    get_last_run_stream_seq,
    list_recent_run_stream_events,
    list_run_stream_events,
    normalize_after_seq,
    publish_cancel_signal,
)
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import Message, User
from yuxi.utils.datetime_utils import utc_now_naive
from yuxi.utils.hash_utils import hash_id
from yuxi.utils.logging_config import logger
from yuxi.utils.sse_utils import (
    SSE_HEARTBEAT_SECONDS,
    SSE_MAX_CONNECTION_MINUTES,
    SSE_POLL_INTERVAL_SECONDS,
    format_heartbeat,
    format_sse,
)

RUN_PROGRESS_RECENT_EVENT_SCAN_LIMIT = 100
RUN_PROGRESS_MESSAGE_LIMIT = 3
RUN_PROGRESS_CONTENT_MAX_CHARS = 800


def _resolve_agent_run_request_id(
    *,
    meta: dict,
    run_type: Literal["chat", "resume"],
    resume: object | None,
    created_by_run_id: str | None,
) -> str:
    raw_request_id = meta.get("request_id")
    if raw_request_id:
        return str(raw_request_id)
    if run_type == "resume":
        resume_key = json.dumps(resume, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        return hash_id("resume:", f"{created_by_run_id}:{resume_key}", length=64)
    return str(uuid.uuid4())


class AgentRunWaitTimeout(Exception):
    """等待结束但 run 尚未进入终态。"""

    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result
        status = str(result.get("status") or "unknown")
        run_id = str(result.get("agent_run_id") or result.get("run_id") or "")
        super().__init__(f"agent run {run_id} is still {status} after waiting")


class AgentRunWaitUnavailable(RuntimeError):
    """同步等待依赖暂时不可用，不能归类为运行超时。"""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def load_agent_run_context(agent_item, agent_backend):
    """用 Agent 配置的 context 片段实例化并填充运行上下文，供 run 解析器读取配置字段。"""
    context = agent_backend.context_schema()
    config_json = getattr(agent_item, "config_json", None) or {}
    config_context = config_json.get("context") if isinstance(config_json, dict) else {}
    if isinstance(config_context, dict):
        context.update_from_dict(config_context)
    return context


_load_agent_context = load_agent_run_context


async def resolve_agent_run_model_spec(
    requested_model: str | None,
    configured_model: str | None,
    db: AsyncSession | None = None,
    user: User | None = None,
) -> str:
    """按请求、Agent 配置、系统默认的顺序解析并校验聊天模型。"""
    model_spec = next(
        (
            candidate.strip()
            for candidate in (requested_model, configured_model)
            if isinstance(candidate, str) and candidate.strip()
        ),
        None,
    )
    if model_spec is None:
        model_spec = str((await system_options.get(db))["default_model"]).strip()

    info = model_cache.get_model_info(model_spec)
    if info is None:
        canonical_spec = model_cache.canonicalize_spec(model_spec)
        info = model_cache.get_model_info(canonical_spec) if canonical_spec else None
    if info is None and db is not None and user is not None and ":" in model_spec:
        resource_id, model_id = model_spec.split(":", 1)
        from yuxi.models.providers.repository import get_model_provider_for_user

        provider = await get_model_provider_for_user(db, resource_id, user)
        if provider is not None and provider.is_enabled:
            configured_model = next(
                (
                    item
                    for item in (provider.enabled_models or [])
                    if isinstance(item, dict) and item.get("id") == model_id
                ),
                None,
            )
            if configured_model is not None and configured_model.get("type", "chat") == "chat":
                info = ModelInfo(
                    provider_id=provider.provider_id,
                    resource_id=provider.resource_id,
                    model_id=model_id,
                    model_type="chat",
                    display_name=configured_model.get("display_name", model_id),
                    api_key=resolve_api_key(provider) or "",
                    base_url=configured_model.get("base_url_override") or provider.base_url,
                    provider_type=provider.provider_type,
                    headers=dict(provider.headers_json or {}),
                    extra=dict(provider.extra_json or {}),
                    request_body_overrides=dict(configured_model.get("request_body_overrides") or {}),
                    input_modalities=tuple(configured_model.get("input_modalities") or ()),
                )
    if not info or info.model_type != "chat":
        raise HTTPException(status_code=422, detail=f"未找到可用聊天模型: '{model_spec}'")
    if user is not None and db is not None and hasattr(info, "resource_id"):
        from yuxi.models.providers.repository import get_model_provider_for_user

        provider = await get_model_provider_for_user(db, getattr(info, "resource_id", None) or info.provider_id, user)
        if provider is None or not provider.is_enabled:
            raise HTTPException(status_code=422, detail=f"模型资源不可访问: '{model_spec}'")
    return getattr(info, "spec", model_spec)


def resolve_agent_run_tool_approval_mode(requested_mode: str | None, configured_mode: str | None) -> str:
    """解析本次 run 的工具审批模式：显式覆盖优先，否则使用 Agent 配置与默认值。"""
    source = requested_mode if requested_mode is not None else configured_mode or DEFAULT_TOOL_APPROVAL_MODE
    try:
        return normalize_tool_approval_mode(source)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


async def resolve_agent_run_config(
    model_spec: str | None,
    tool_approval_mode: str | None,
    agent_item,
    agent_backend,
    db: AsyncSession | None = None,
    user: User | None = None,
) -> tuple[str, str]:
    """一次性解析 model_spec 与 tool_approval_mode，共享同一份运行上下文。"""
    context = load_agent_run_context(agent_item, agent_backend)
    resolved_model_spec = await resolve_agent_run_model_spec(
        model_spec,
        getattr(context, "model", None),
        db,
        user,
    )
    resolved_tool_approval_mode = resolve_agent_run_tool_approval_mode(
        tool_approval_mode,
        getattr(context, "tool_approval_mode", None),
    )
    return resolved_model_spec, resolved_tool_approval_mode


async def validate_agent_run_input_modalities(
    input_message: AgentRunInputMessage,
    model_spec: str,
    db: AsyncSession | None = None,
) -> None:
    """拒绝把图片提交给明确声明为纯文本输入的模型。"""
    if not input_message.image_content:
        return
    info = model_cache.get_model_info(model_spec)
    modalities = tuple(getattr(info, "input_modalities", ()) or ())
    resource_id = getattr(info, "resource_id", None)
    model_id = getattr(info, "model_id", None)
    if not modalities and resource_id and model_id and db is not None:
        provider = await get_model_provider_by_resource_id(db, resource_id)
        configured_model = (
            next(
                (model for model in provider.enabled_models or [] if model.get("id") == model_id),
                None,
            )
            if provider is not None
            else None
        )
        modalities = tuple((configured_model or {}).get("input_modalities") or ())
    if modalities and "image" not in modalities:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "model_image_input_unsupported",
                "message": f"当前模型 {model_spec} 不支持图像输入，请选择支持图像输入的模型",
            },
        )


def _build_run_response(run) -> dict:
    return {
        "run_id": run.id,
        "thread_id": run.conversation_thread_id,
        "status": run.status,
        "request_id": run.request_id,
        "stream_url": f"/api/agent/runs/{run.id}/events",
    }


def _validate_resume_input(resume: object) -> None:
    if not isinstance(resume, dict):
        return
    if "answer" in resume:
        answer = resume.get("answer")
        if answer is None or answer == {} or answer == [] or (isinstance(answer, str) and not answer.strip()):
            raise HTTPException(status_code=422, detail="answer 不能为空")
    if "decisions" not in resume:
        return
    decisions = resume.get("decisions")
    if not isinstance(decisions, list) or not decisions:
        raise HTTPException(status_code=422, detail="decisions 必须是非空数组")
    for decision in decisions:
        if not isinstance(decision, dict) or decision.get("type") not in {"approve", "reject"}:
            raise HTTPException(status_code=422, detail="decision.type 只支持 approve 或 reject")


def _compact_message_dict(message: dict) -> dict:
    compact = {
        key: message[key] for key in ("id", "role", "content", "type", "message_type") if message.get(key) is not None
    }
    extra_metadata = message.get("extra_metadata")
    if isinstance(extra_metadata, dict) and extra_metadata.get("attachments"):
        compact["extra_metadata"] = {"attachments": extra_metadata["attachments"]}
    return compact


def _compact_semantic_stream_event(stream_event: dict) -> dict:
    event_type = stream_event.get("type")
    if event_type == "message_delta":
        return {
            key: stream_event[key]
            for key in ("type", "message_id", "content", "reasoning_content", "additional_reasoning_content")
            if stream_event.get(key)
        }

    if event_type in {"tool_call", "tool_call_delta"}:
        compact = {
            key: stream_event[key]
            for key in ("type", "message_id", "tool_call_id", "name", "args", "args_delta")
            if stream_event.get(key) is not None and stream_event.get(key) != ""
        }
        if stream_event.get("index"):
            compact["index"] = stream_event["index"]
        return compact

    return {key: value for key, value in stream_event.items() if key not in {"thread_id", "namespace"}}


def _compact_tool_stream_event(event: dict) -> dict:
    compact = {key: event[key] for key in ("method",) if event.get(key)}
    data = event.get("data")
    if isinstance(data, dict):
        compact_data = {
            key: data[key]
            for key in ("event", "tool_call_id", "tool_name", "output", "error")
            if data.get(key) is not None and data.get(key) != ""
        }
        if compact_data:
            compact["data"] = compact_data
    return compact


def _compact_stream_chunk(chunk: dict) -> dict:
    compact = {
        key: chunk[key]
        for key in (
            "status",
            "run_id",
            "message",
            "error_type",
            "error_message",
            "retryable",
            "job_try",
            "questions",
            "approval",
            "interrupt_info",
            "source",
            "agent_state",
            "compression",
        )
        if chunk.get(key) is not None and chunk.get(key) != ""
    }
    if isinstance(chunk.get("msg"), dict):
        compact["msg"] = _compact_message_dict(chunk["msg"])
    if isinstance(chunk.get("stream_event"), dict):
        compact["stream_event"] = _compact_semantic_stream_event(chunk["stream_event"])
    if isinstance(chunk.get("event"), dict):
        compact["event"] = _compact_tool_stream_event(chunk["event"])
    return compact


def _request_id_from_chunk(chunk: object) -> str | None:
    if not isinstance(chunk, dict):
        return None
    request_id = chunk.get("request_id")
    if isinstance(request_id, str) and request_id:
        return request_id
    msg = chunk.get("msg")
    extra_metadata = msg.get("extra_metadata") if isinstance(msg, dict) else None
    if isinstance(extra_metadata, dict):
        request_id = extra_metadata.get("request_id")
        if isinstance(request_id, str) and request_id:
            return request_id
    return None


def _request_id_from_payload(payload: object) -> str | None:
    if not isinstance(payload, dict):
        return None
    request_id = payload.get("request_id")
    if isinstance(request_id, str) and request_id:
        return request_id
    request_id = _request_id_from_chunk(payload.get("chunk"))
    if request_id:
        return request_id
    items = payload.get("items")
    if isinstance(items, list):
        for item in items:
            request_id = _request_id_from_chunk(item)
            if request_id:
                return request_id
    return None


def _compact_run_event_payload(event_type: str, payload: dict | None) -> dict:
    if not isinstance(payload, dict):
        return {}

    if event_type == "messages":
        compact: dict = {}
        if isinstance(payload.get("items"), list):
            compact["items"] = [
                _compact_stream_chunk(item) if isinstance(item, dict) else item for item in payload["items"]
            ]
        if isinstance(payload.get("chunk"), dict):
            compact["chunk"] = _compact_stream_chunk(payload["chunk"])
        return compact

    compact = {key: value for key, value in payload.items() if key not in {"chunk", "request_id"}}
    if isinstance(payload.get("chunk"), dict):
        compact["chunk"] = _compact_stream_chunk(payload["chunk"])
    return compact


def _is_empty_agent_state(agent_state: object) -> bool:
    if not isinstance(agent_state, dict):
        return False
    return all(not value for value in agent_state.values())


def _compact_run_event_envelope(envelope: dict) -> dict | None:
    event_type = str(envelope.get("event") or "")
    payload = envelope.get("payload")
    if event_type == "metadata":
        compact = {key: envelope[key] for key in ("run_id", "thread_id") if key in envelope}
        compact["payload"] = {
            key: payload[key] for key in ("run_type", "source") if isinstance(payload, dict) and key in payload
        }
        return compact
    if event_type == "custom" and isinstance(payload, dict) and payload.get("name") == "yuxi.agent_state":
        state = payload.get("agent_state")
        chunk = payload.get("chunk") if isinstance(payload.get("chunk"), dict) else {}
        if _is_empty_agent_state(state) or _is_empty_agent_state(chunk.get("agent_state")):
            return None

    compact = {key: envelope[key] for key in ("run_id", "thread_id") if key in envelope}
    request_id = _request_id_from_payload(payload)
    if request_id:
        compact["request_id"] = request_id
    compact["payload"] = _compact_run_event_payload(event_type, payload)
    return compact


def _progress_message_from_chunk(chunk: dict, *, seq: str) -> dict | None:
    """把单个消息 chunk 转成 status 可展示的一条进度。"""
    stream_event = chunk.get("stream_event")
    if not isinstance(stream_event, dict):
        return None
    stream_type = stream_event.get("type")
    message_id = str(stream_event.get("message_id") or "").strip()

    content = ""
    kind = ""
    if stream_type == "message_delta":
        content = (
            stream_event.get("content")
            or stream_event.get("reasoning_content")
            or stream_event.get("additional_reasoning_content")
            or ""
        )
        kind = "assistant_message" if stream_event.get("content") else "assistant_reasoning"
    elif stream_type in {"tool_call", "tool_call_delta"}:
        tool_name = str(stream_event.get("name") or stream_event.get("tool_call_id") or "工具").strip()
        content = f"调用工具 {tool_name}" if stream_type == "tool_call" else f"正在准备工具 {tool_name}"
        kind = stream_type
    else:
        return None

    content = str(content).strip()
    if not content:
        return None
    if len(content) > RUN_PROGRESS_CONTENT_MAX_CHARS:
        content = "..." + content[-RUN_PROGRESS_CONTENT_MAX_CHARS:]

    base = {"seq": seq}
    if message_id:
        base["message_id"] = message_id
    tool_call_id = str(stream_event.get("tool_call_id") or "").strip()
    if tool_call_id:
        base["tool_call_id"] = tool_call_id
    return {**base, "kind": kind, "content": content}


async def get_agent_run_progress(run_id: str, *, message_limit: int = RUN_PROGRESS_MESSAGE_LIMIT) -> dict:
    """读取适合 status 轮询返回的轻量运行进度快照。"""
    try:
        events = await list_recent_run_stream_events(run_id, limit=RUN_PROGRESS_RECENT_EVENT_SCAN_LIMIT)
    except Exception as e:
        logger.warning(f"Failed to read run progress events for run {run_id}: {e}")
        return {"last_seq": "0-0", "messages": []}

    last_seq = str(events[0]["seq"]) if events else "0-0"
    limit = max(1, int(message_limit or RUN_PROGRESS_MESSAGE_LIMIT))
    messages = []

    for event in events:
        envelope = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        if event.get("event_type") != "messages" and envelope.get("event") != "messages":
            continue
        payload = envelope.get("payload")
        if not isinstance(payload, dict):
            continue

        chunks = []
        if isinstance(payload.get("chunk"), dict):
            chunks.append(payload["chunk"])
        if isinstance(payload.get("items"), list):
            chunks.extend(item for item in payload["items"] if isinstance(item, dict))

        for chunk in reversed(chunks):
            message = _progress_message_from_chunk(chunk, seq=str(event.get("seq") or ""))
            if message:
                messages.append(message)
            if len(messages) >= limit:
                return {"last_seq": last_seq, "messages": list(reversed(messages))}

    return {"last_seq": last_seq, "messages": list(reversed(messages))}


async def _resolve_resume_department(db: AsyncSession, *, current_uid: str, created_by_run_id: str | None) -> int:
    """查原运行所属部门并对当前账号在该部门重新授权；历史无部门运行拒绝恢复。"""
    from yuxi.repositories.agent_run_repository import AgentRunRepository
    from yuxi.repositories.user_repository import UserRepository
    from yuxi.services.department_context_service import (
        DepartmentContextError,
        resolve_department_context,
    )

    if not created_by_run_id:
        raise HTTPException(status_code=422, detail="恢复请求缺少原运行标识")
    origin_run = await AgentRunRepository(db).get_run(created_by_run_id)
    if origin_run is None:
        raise HTTPException(status_code=404, detail="原运行不存在")
    if origin_run.uid != current_uid:
        raise HTTPException(status_code=403, detail="只能恢复自己的运行")
    if origin_run.department_id is None:
        raise HTTPException(status_code=409, detail="原运行缺少部门归属，无法恢复")
    owner = await UserRepository().get_by_uid_with_db(db, current_uid)
    if owner is None:
        raise HTTPException(status_code=401, detail="账号不存在或已删除")
    try:
        context = await resolve_department_context(
            db, user_id=owner.id, department_id=origin_run.department_id, strict=True
        )
    except DepartmentContextError as exc:
        raise HTTPException(status_code=403, detail=f"原部门上下文无效: {exc}") from exc
    if context.department_id is None:
        raise HTTPException(status_code=403, detail="原部门上下文无效")
    return origin_run.department_id


async def create_agent_run_view(
    *,
    input_message: AgentRunInputMessage | None,
    agent_slug: str,
    thread_id: str,
    meta: dict,
    current_uid: str,
    db: AsyncSession,
    model_spec: str | None = None,
    tool_approval_mode: str | None = None,
    resume: object | None = None,
    created_by_run_id: str | None = None,
    department_id: int | None = None,
    source: str | None = None,
    channel: str | None = None,
    external_id: str | None = None,
    origin_metadata: dict[str, Any] | None = None,
) -> dict:
    """创建 chat/resume run 的 HTTP 入口，输入正文由 Message 承载，run 只登记运行元数据。"""
    meta = meta or {}
    if input_message is None and resume is None:
        raise HTTPException(status_code=422, detail="input_message 或 resume 不能为空")
    if resume is not None:
        _validate_resume_input(resume)

    run_type = "resume" if resume is not None else "chat"
    # 恢复使用原运行所属部门并重新校验当前账号在该部门的有效成员身份；
    # 页面当前部门不覆盖原部门；缺少部门归属的历史运行拒绝恢复
    if run_type == "resume":
        resume_department_id = await _resolve_resume_department(
            db, current_uid=current_uid, created_by_run_id=created_by_run_id
        )
        department_id = resume_department_id
    run_created_by_id = created_by_run_id if run_type == "resume" else None
    request_id = _resolve_agent_run_request_id(
        meta=meta,
        run_type=run_type,
        resume=resume,
        created_by_run_id=run_created_by_id,
    )

    scope = await prepare_agent_run_creation_scope(
        agent_slug=agent_slug,
        conversation_thread_id=thread_id,
        current_uid=current_uid,
        db=db,
        request_id=request_id,
        run_type=run_type,
        agent_kind="main",
        created_by_run_id=run_created_by_id,
    )
    if scope.existing_run:
        if scope.existing_run.status == "pending":
            await _commit_and_enqueue(db, scope.existing_run.id)
        return _build_run_response(scope.existing_run)

    submission_manifest = None
    if run_type == "resume":
        resolved_model_spec = scope.parent_run.input_payload["model_spec"]
        # 旧版本固化的 input_payload 没有 tool_approval_mode，回退默认值以兼容历史 interrupted run。
        resolved_tool_approval_mode = scope.parent_run.input_payload.get(
            "tool_approval_mode", DEFAULT_TOOL_APPROVAL_MODE
        )
    else:
        resolved_model_spec, resolved_tool_approval_mode = await resolve_agent_run_config(
            model_spec,
            tool_approval_mode,
            scope.agent_item,
            scope.agent_backend,
            db,
            user=scope.current_user,
        )

    if run_type == "resume":
        submission_manifest = None
    else:
        submission_manifest = await build_submission_manifest_result(
            agent_item=scope.agent_item,
            agent_backend=scope.agent_backend,
            user=scope.current_user,
            db=db,
            model_spec=resolved_model_spec,
            tool_approval_mode=resolved_tool_approval_mode,
            run_type=run_type,
            thread_id=thread_id,
        )

    run_input_message = _prepare_run_input_message(
        run_type=run_type,
        input_message=input_message,
        resume=resume,
        request_id=request_id,
        model_spec=resolved_model_spec,
        tool_approval_mode=resolved_tool_approval_mode,
        meta=meta,
    )
    await validate_agent_run_input_modalities(run_input_message, resolved_model_spec, db)

    persisted_input_message = await create_agent_run_input_message(
        db=db,
        conversation_id=scope.conversation.id,
        request_id=request_id,
        input_message=run_input_message,
    )
    input_payload = {
        "model_spec": resolved_model_spec,
        "tool_approval_mode": resolved_tool_approval_mode,
    }
    if run_type == "resume" and scope.parent_run is not None:
        parent_manifest = getattr(scope.parent_run, "manifest", None)
        parent_fingerprint = getattr(scope.parent_run, "manifest_fingerprint", None)
        if isinstance(parent_manifest, dict) and isinstance(parent_fingerprint, str):
            input_payload[RUN_MANIFEST_INPUT_KEY] = parent_manifest
            input_payload["_run_manifest_fingerprint"] = parent_fingerprint
        else:
            raise HTTPException(status_code=409, detail="父 Run 缺少可继承的提交时运行清单")
    elif submission_manifest is not None:
        input_payload[RUN_MANIFEST_INPUT_KEY] = submission_manifest.manifest
        input_payload["_run_manifest_fingerprint"] = compute_manifest_fingerprint(submission_manifest.manifest)
    if run_type == "resume" and scope.parent_run is not None:
        if source is None:
            source = getattr(scope.parent_run, "source", None) or "chat"
        if channel is None:
            channel = getattr(scope.parent_run, "channel", None) or "web"
        if external_id is None:
            external_id = getattr(scope.parent_run, "external_id", None)
        if origin_metadata is None:
            origin_metadata = getattr(scope.parent_run, "origin_metadata", None) or {}
    else:
        source = source or "chat"
        channel = channel or "web"

    run, created = await persist_agent_run_record(
        agent_slug=agent_slug,
        conversation_thread_id=thread_id,
        current_uid=current_uid,
        db=db,
        request_id=request_id,
        conversation_id=scope.conversation.id,
        run_type=run_type,
        input_payload=input_payload,
        persisted_input_message=persisted_input_message,
        created_by_run_id=run_created_by_id,
        source=source,
        channel=channel,
        external_id=external_id,
        origin_metadata=origin_metadata,
        department_id=department_id,
    )
    if created:
        await _commit_and_enqueue(db, run.id)

    return _build_run_response(run)


async def _commit_and_enqueue(db: AsyncSession, run_id: str) -> None:
    await db.commit()
    await enqueue_agent_run(run_id)


@dataclass(frozen=True)
class AgentRunCreationScope:
    """run 创建前置校验后的数据库作用域，避免和 Agent runtime context 混淆。"""

    conversation: Any
    agent_item: Any
    agent_backend: Any
    existing_run: Any | None
    current_user: User
    parent_run: Any | None = None


def _prepare_run_input_message(
    *,
    run_type: Literal["chat", "resume"],
    input_message: AgentRunInputMessage | None,
    resume: object | None,
    request_id: str,
    model_spec: str,
    meta: dict,
    tool_approval_mode: str | None = None,
) -> AgentRunInputMessage:
    metadata: dict[str, Any] = {"request_id": request_id}
    if attachment_file_ids := (meta.get("attachment_file_ids") or []):
        metadata["attachment_file_ids"] = attachment_file_ids
    if source := meta.get("source"):
        metadata["source"] = source
    if isinstance(meta.get("agent_invocation_meta"), dict):
        metadata["agent_invocation_meta"] = meta["agent_invocation_meta"]
    if run_type == "chat":
        if input_message is None:
            raise HTTPException(status_code=422, detail="input_message 不能为空")
        if raw_message := input_message.raw_message():
            metadata["raw_message"] = raw_message
        if tool_approval_mode is not None:
            metadata["tool_approval_mode"] = tool_approval_mode  # already normalized by resolve_agent_run_config
        return input_message.with_metadata(metadata)

    metadata["resume"] = resume
    metadata["source"] = "ask_user_question_resume"
    return build_resume_input_message(resume).with_metadata(metadata)


def _same_run_request_scope(
    run,
    *,
    uid: str,
    agent_slug: str,
    conversation_thread_id: str,
    run_type: str,
    created_by_run_id: str | None = None,
    subagent_thread_relation_id: int | None = None,
) -> bool:
    """判断幂等命中的 run 是否确实属于同一次语义创建请求。"""
    return (
        run.uid == str(uid)
        and run.agent_slug == agent_slug
        and run.conversation_thread_id == conversation_thread_id
        and run.run_type == run_type
        and run.created_by_run_id == created_by_run_id
        and getattr(run, "subagent_thread_relation_id", None) == subagent_thread_relation_id
    )


def _run_busy_exception(*, active_run, agent_slug: str, conversation_thread_id: str) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "code": "run_busy",
            "message": "该智能体线程正在运行，请等待、查询或取消当前运行后再继续",
            "active_run_id": active_run.id,
            "active_run_status": active_run.status,
            "agent_slug": agent_slug,
            "thread_id": conversation_thread_id,
        },
    )


async def create_agent_run_input_message(
    *,
    db: AsyncSession,
    conversation_id: int,
    request_id: str,
    input_message: AgentRunInputMessage,
    delivery_status: str = "complete",
) -> Message:
    """先落库输入消息；run 创建后再回填 run_id，避免 Message 外键先指向不存在的 run。"""
    message = Message(
        conversation_id=conversation_id,
        role="user",
        content=input_message.content,
        message_type=input_message.message_type,
        image_content=input_message.image_content,
        request_id=request_id,
        delivery_status=delivery_status,
        extra_metadata=input_message.extra_metadata,
    )
    db.add(message)
    await db.flush()
    return message


async def persist_agent_run_record(
    *,
    agent_slug: str,
    conversation_thread_id: str,
    runtime_scope_id: str | None = None,
    current_uid: str,
    db: AsyncSession,
    request_id: str,
    conversation_id: int,
    run_type: str,
    input_payload: dict,
    persisted_input_message: Message,
    created_by_run_id: str | None = None,
    subagent_thread_relation_id: int | None = None,
    source: str = "chat",
    channel: str = "web",
    external_id: str | None = None,
    origin_metadata: dict[str, Any] | None = None,
    department_id: int | None = None,
) -> tuple[Any, bool]:
    """登记一条 AgentRun 并绑定已创建的输入消息，返回是否为本次新建。"""
    run_id = str(uuid.uuid4())
    try:
        async with db.begin_nested():
            run = await AgentRunRepository(db).create_run(
                run_id=run_id,
                conversation_thread_id=conversation_thread_id,
                runtime_scope_id=runtime_scope_id,
                agent_slug=agent_slug,
                uid=str(current_uid),
                request_id=request_id,
                input_payload=input_payload,
                source=source,
                channel=channel,
                external_id=external_id,
                origin_metadata=origin_metadata,
                conversation_id=conversation_id,
                department_id=department_id,
                created_by_run_id=created_by_run_id,
                subagent_thread_relation_id=subagent_thread_relation_id,
                run_type=run_type,
                input_message_id=persisted_input_message.id,
            )
            persisted_input_message.run_id = run_id
            manifest = input_payload.get(RUN_MANIFEST_INPUT_KEY)
            if manifest is not None:
                run.manifest = manifest
                run.manifest_fingerprint = compute_manifest_fingerprint(manifest)
                run.manifest_recorded_at = utc_now_naive()
            await db.flush()
    except IntegrityError:
        run_repo = AgentRunRepository(db)
        existing = await run_repo.get_run_by_request_id(request_id)
        if existing and _same_run_request_scope(
            existing,
            uid=str(current_uid),
            agent_slug=agent_slug,
            conversation_thread_id=conversation_thread_id,
            run_type=run_type,
            created_by_run_id=created_by_run_id,
            subagent_thread_relation_id=subagent_thread_relation_id,
        ):
            await db.delete(persisted_input_message)
            await db.flush()
            return existing, False
        active_run = await run_repo.get_active_run_by_thread_for_user(
            agent_slug=agent_slug,
            conversation_thread_id=conversation_thread_id,
            uid=str(current_uid),
        )
        if active_run:
            raise _run_busy_exception(
                active_run=active_run,
                agent_slug=agent_slug,
                conversation_thread_id=conversation_thread_id,
            )
        raise HTTPException(status_code=409, detail="request_id 冲突")

    return run, True


async def prepare_agent_run_creation_scope(
    *,
    agent_slug: str,
    conversation_thread_id: str,
    current_uid: str,
    db: AsyncSession,
    request_id: str,
    run_type: Literal["chat", "resume", "subagent"],
    agent_kind: Literal["main", "subagent"],
    created_by_run_id: str | None = None,
    subagent_thread_relation_id: int | None = None,
) -> AgentRunCreationScope:
    """校验 run 创建作用域，加载对话、智能体、后端和幂等状态，并拒绝同线程并发写入。"""
    if not conversation_thread_id:
        raise HTTPException(status_code=422, detail="conversation_thread_id 不能为空")

    conversation = await ConversationRepository(db).lock_conversation_by_thread_id(conversation_thread_id)
    if not conversation or conversation.uid != str(current_uid) or conversation.status == "deleted":
        raise HTTPException(status_code=404, detail="对话线程不存在")
    # Conversation.agent_id 是历史字段名，实际保存的是 Agent.slug。
    if conversation.agent_id != agent_slug:
        raise HTTPException(status_code=409, detail="已有线程已绑定智能体，不能切换")

    user_result = await db.execute(select(User).where(User.uid == str(current_uid)))
    current_user = user_result.scalar_one_or_none()
    if not current_user:
        raise HTTPException(status_code=404, detail="用户不存在")

    agent_repo = AgentRepository(db)
    agent_item = await agent_repo.get_visible_by_slug(slug=agent_slug, user=current_user, kind=agent_kind)
    if not agent_item:
        raise HTTPException(status_code=404, detail="智能体不存在")

    agent_backend = agent_manager.get_agent(agent_item.backend_id)
    if not agent_backend:
        raise HTTPException(status_code=404, detail=f"智能体后端 {agent_item.backend_id} 不存在")

    run_repo = AgentRunRepository(db)
    existing = await run_repo.get_run_by_request_id(request_id)
    if existing and existing.uid != str(current_uid):
        raise HTTPException(status_code=409, detail="request_id 冲突")
    if existing and not _same_run_request_scope(
        existing,
        uid=str(current_uid),
        agent_slug=agent_slug,
        conversation_thread_id=conversation_thread_id,
        run_type=run_type,
        created_by_run_id=created_by_run_id,
        subagent_thread_relation_id=subagent_thread_relation_id,
    ):
        raise HTTPException(status_code=409, detail="request_id 冲突")
    parent_run = None
    if run_type == "resume":
        if not created_by_run_id:
            raise HTTPException(status_code=422, detail="created_by_run_id 不能为空")
        if not existing:
            parent_run = await run_repo.get_run_for_user(created_by_run_id, str(current_uid))
            if (
                not parent_run
                or parent_run.conversation_thread_id != conversation_thread_id
                or parent_run.agent_slug != agent_slug
            ):
                raise HTTPException(status_code=404, detail="被恢复的运行任务不存在")
            if parent_run.status != "interrupted":
                raise HTTPException(status_code=409, detail="只有 interrupted run 可以恢复")
            latest_run = await run_repo.get_latest_chat_or_resume_run(
                uid=str(current_uid),
                agent_slug=agent_slug,
                conversation_thread_id=conversation_thread_id,
            )
            if latest_run and latest_run.id != parent_run.id:
                raise HTTPException(
                    status_code=409,
                    detail={"code": "resume_superseded", "message": "中断运行已被后续运行超越"},
                )
            parent_payload = parent_run.input_payload
            if not isinstance(parent_payload, dict) or not parent_payload.get("model_spec"):
                raise HTTPException(status_code=409, detail="被恢复的运行任务缺少模型快照")
    if not existing:
        active_run = await run_repo.get_active_run_by_thread_for_user(
            agent_slug=agent_slug,
            conversation_thread_id=conversation_thread_id,
            uid=str(current_uid),
        )
        if active_run:
            raise _run_busy_exception(
                active_run=active_run,
                agent_slug=agent_slug,
                conversation_thread_id=conversation_thread_id,
            )
    return AgentRunCreationScope(
        conversation=conversation,
        agent_item=agent_item,
        agent_backend=agent_backend,
        existing_run=existing,
        current_user=current_user,
        parent_run=parent_run,
    )


async def enqueue_agent_run(run_id: str) -> None:
    """把已持久化的 run 投递到后台 worker 队列。"""
    queue = await get_arq_pool()
    await queue.enqueue_job("process_agent_run", run_id, _job_id=f"run:{run_id}")


async def reenqueue_agent_run(run_id: str) -> None:
    """清理确定性 ARQ Job 的陈旧状态并重新投递，用于 worker 启动恢复。"""
    queue = await get_arq_pool()
    job_id = f"run:{run_id}"

    async with queue.pipeline(transaction=True) as pipeline:
        pipeline.delete(
            f"{job_key_prefix}{job_id}",
            f"{result_key_prefix}{job_id}",
            f"{retry_key_prefix}{job_id}",
            f"{in_progress_key_prefix}{job_id}",
        )
        pipeline.zrem(default_queue_name, job_id)
        pipeline.zrem(abort_jobs_ss, job_id)
        await pipeline.execute()

    await queue.enqueue_job("process_agent_run", run_id, _job_id=job_id)


async def get_agent_run_view(*, run_id: str, current_uid: str, db: AsyncSession) -> dict:
    repo = AgentRunRepository(db)
    run = await repo.get_run_for_user(run_id, str(current_uid))
    if not run:
        raise HTTPException(status_code=404, detail="运行任务不存在")
    return {"run": run.to_dict()}


async def get_agent_run_result(*, run_id: str, current_uid: str, db: AsyncSession) -> dict:
    """加载某个 run 的最终结果（状态/输出/Langfuse trace/错误），供 chat/eval/cron 等统一复用。"""
    run = await AgentRunRepository(db).get_run_for_user(run_id, str(current_uid))
    if not run:
        return {
            "status": "failed",
            "agent_run_id": run_id,
            "output": "",
            "error": {"type": "run_not_found", "message": "运行任务不存在"},
        }

    output_message = None
    if run.conversation_id is not None:
        output_message = await AgentRunOutputRepository(db).get_output_message(
            run_id=run.id,
            conversation_id=run.conversation_id,
            output_message_id=run.output_message_id,
            allow_legacy_fallback=run.status == "completed",
        )
    output_metadata = (
        output_message.extra_metadata if output_message and isinstance(output_message.extra_metadata, dict) else {}
    )

    payload: dict[str, Any] = {
        "status": run.status,
        "output": output_message.content if output_message else "",
        "agent_slug": run.agent_slug,
        "thread_id": run.conversation_thread_id,
        "conversation_id": run.conversation_id,
        "agent_run_id": run.id,
        "request_id": run.request_id,
        "final_message_id": output_message.id if output_message else None,
        "langfuse_trace_id": output_metadata.get("langfuse_trace_id"),
        "token_usage": getattr(run, "token_usage", None) or {},
    }
    if run.error_type or run.error_message:
        payload["error"] = {"type": run.error_type, "message": run.error_message}
    return payload


async def get_agent_run_langfuse_link(*, run_id: str, current_uid: str, db: AsyncSession) -> dict:
    """按用户可见 Run 的权威输出绑定解析 Langfuse 跳转地址。"""
    result = await get_agent_run_result(run_id=run_id, current_uid=current_uid, db=db)
    if result.get("error", {}).get("type") == "run_not_found":
        raise HTTPException(status_code=404, detail="运行任务不存在")

    trace_id = result.get("langfuse_trace_id")
    if not isinstance(trace_id, str) or not trace_id.strip():
        return {"run_id": run_id, "available": False, "reason": "trace_not_available"}

    # 远端项目解析可能等待数秒，先结束只读事务并归还数据库连接。
    await db.commit()
    trace_url = await get_trace_url_by_id_async(trace_id)
    if not trace_url:
        return {"run_id": run_id, "available": False, "reason": "langfuse_unavailable"}

    return {"run_id": run_id, "available": True, "url": trace_url}


async def load_agent_run_result(*, run_id: str, current_uid: str) -> dict:
    """自开独立会话读取 run 结果，用于流结束/后台调用等请求会话已不可用的场景。"""
    async with pg_manager.get_async_session_context() as db:
        return await get_agent_run_result(run_id=run_id, current_uid=current_uid, db=db)


async def await_agent_run_result(*, run_id: str, current_uid: str) -> dict:
    """阻塞至 run 终结并返回最终结果，供 cron 等 in-process 调用。

    复用有限事件流 ``stream_agent_run_events``：它在 run 终结或超时后自然结束，
    因此排空即等待，无需额外轮询。等待上限继承事件流内部的 ``SSE_MAX_CONNECTION_MINUTES``。
    如果等待结束后 run 仍非终态，抛出 ``AgentRunWaitTimeout``，避免调用方把非终态误当最终结果。
    """
    async for _ in stream_agent_run_events(
        run_id=run_id,
        after_seq="0-0",
        current_uid=current_uid,
        verbose=False,
        raise_on_error=True,
    ):
        pass
    result = await load_agent_run_result(run_id=run_id, current_uid=current_uid)
    if str(result.get("status") or "") not in TERMINAL_RUN_STATUSES:
        raise AgentRunWaitTimeout(result)
    return result


async def request_cancel_agent_run(
    *,
    run_id: str,
    current_uid: str,
    db: AsyncSession,
    cascade_children: bool = False,
):
    """请求取消一个 run，并可同时向仍活跃的子 run 发布取消信号。"""
    repo = AgentRunRepository(db)
    run, cancelled_ids, cancelled_interrupted = await repo.request_cancel_execution_tree(
        run_id=run_id,
        uid=str(current_uid),
        cascade_descendants=cascade_children,
    )
    if run is None:
        raise HTTPException(status_code=404, detail="运行任务不存在")
    await db.commit()
    pending_clear_error = None
    if getattr(run, "conversation_thread_id", None):
        from yuxi.agentscope.thread_guard import clear_pending_confirm

        try:
            await clear_pending_confirm(
                run.conversation_thread_id,
                expected_run_id=run.id,
                include_legacy=cancelled_interrupted,
            )
        except Exception as exc:
            logger.warning("取消审批缓存清理失败 run={} type={}", run.id, type(exc).__name__)
            pending_clear_error = exc
    from yuxi.agentscope.team_lifecycle import interrupt_team_worker_runs

    try:
        await interrupt_team_worker_runs(uid=str(current_uid), run_ids=cancelled_ids)
    except Exception as exc:
        # 取消事实已经提交；worker 中断属于尽力而为的外部信号，失败时仍需继续发布 ARQ 取消信号。
        logger.warning("转发 Team worker 取消信号失败：%s", exc)
    await asyncio.gather(*(publish_cancel_signal(cid) for cid in cancelled_ids))
    if pending_clear_error is not None:
        raise HTTPException(status_code=503, detail="取消已提交，审批缓存清理暂不可用；请重试")
    if cancelled_interrupted or (
        run.status == "cancelled" and run.error_type == "cancelled" and run.error_message == "对话已在执行前取消"
    ):
        from yuxi.services.agent_request_queue_service import dispatch_next_request

        await dispatch_next_request(
            uid=str(current_uid),
            agent_slug=run.agent_slug,
            thread_id=run.conversation_thread_id,
        )
    return run


async def cancel_agent_run_view(*, run_id: str, current_uid: str, db: AsyncSession) -> dict:
    """HTTP 取消入口：取消父 run 时默认级联取消活跃子 run。"""
    run = await request_cancel_agent_run(run_id=run_id, current_uid=current_uid, db=db, cascade_children=True)
    return {"run": run.to_dict() if run else None}


async def stream_agent_run_events(
    *,
    run_id: str,
    after_seq: str,
    current_uid: str,
    verbose: bool = True,
    raise_on_error: bool = False,
) -> AsyncIterator[str]:
    """按 SSE 格式读取 run 事件流；终结事件缺失时根据数据库状态补发 end。"""
    started_at = utc_now_naive()
    last_heartbeat_ts = started_at

    last_seq = normalize_after_seq(after_seq)

    try:
        while True:
            try:
                async with pg_manager.get_async_session_context() as db:
                    repo = AgentRunRepository(db)
                    run = await repo.get_run_for_user(run_id, str(current_uid))
                    if not run:
                        if raise_on_error:
                            raise AgentRunWaitUnavailable("run_not_found")
                        yield format_sse({"run_id": run_id, "message": "运行任务不存在"}, event="error")
                        return
            except asyncio.CancelledError:
                raise
            except AgentRunWaitUnavailable:
                raise
            except Exception as e:
                logger.warning(f"Run SSE DB error for run {run_id}: {e}")
                if raise_on_error:
                    raise AgentRunWaitUnavailable("db_error") from None
                yield format_sse(
                    {
                        "run_id": run_id,
                        "message": "运行事件流暂时不可用，请重连",
                        "reason": "db_error",
                    },
                    event="error",
                )
                return

            try:
                events = await list_run_stream_events(run_id, after_seq=last_seq, limit=200)
            except Exception as e:
                logger.warning(f"Run SSE redis error for run {run_id}: {e}")
                if raise_on_error:
                    raise AgentRunWaitUnavailable("redis_error") from None
                yield format_sse(
                    {
                        "run_id": run_id,
                        "message": "运行事件流暂时不可用，请重连",
                        "reason": "redis_error",
                    },
                    event="error",
                )
                return

            emitted_terminal = False
            for event in events:
                seq = str(event.get("seq") or "0-0")
                last_seq = seq
                event_type = event.get("event_type") or "message"
                envelope = event.get("payload") or {}
                if event_type == "end":
                    payload = envelope.get("payload") if isinstance(envelope, dict) else None
                    event_status = payload.get("status") if isinstance(payload, dict) else None
                    accepted_statuses = {run.status, "error"} if run.status == "failed" else {run.status}
                    if (
                        run.status not in TERMINAL_RUN_STATUSES
                        or bool(getattr(run, "runtime_cleanup_pending", False))
                        or event_status not in accepted_statuses
                    ):
                        continue
                if not verbose and isinstance(envelope, dict):
                    envelope = _compact_run_event_envelope(envelope)
                    if envelope is None:
                        continue
                yield format_sse(envelope, event=event_type, event_id=seq)
                if event_type == "end":
                    emitted_terminal = True

            if emitted_terminal:
                return

            if (
                run.status in TERMINAL_RUN_STATUSES
                and not bool(getattr(run, "runtime_cleanup_pending", False))
                and not events
            ):
                terminal_seq = last_seq
                if terminal_seq in {"", "0-0"}:
                    terminal_seq = await get_last_run_stream_seq(run_id)
                if terminal_seq in {"", "0-0"}:
                    terminal_seq = None
                terminal_envelope = build_run_event_envelope(
                    run_id=run_id,
                    thread_id=run.conversation_thread_id,
                    event_type="end",
                    payload={"status": run.status, "request_id": run.request_id},
                    created_at=utc_now_naive().isoformat(),
                )
                if not verbose:
                    terminal_envelope = _compact_run_event_envelope(terminal_envelope)
                yield format_sse(
                    terminal_envelope,
                    event="end",
                    event_id=terminal_seq,
                )
                return

            now = utc_now_naive()
            elapsed_seconds = (now - started_at).total_seconds()
            heartbeat_elapsed = (now - last_heartbeat_ts).total_seconds()
            if heartbeat_elapsed >= SSE_HEARTBEAT_SECONDS:
                yield format_heartbeat()
                last_heartbeat_ts = now

            if elapsed_seconds >= SSE_MAX_CONNECTION_MINUTES * 60:
                return

            await asyncio.sleep(SSE_POLL_INTERVAL_SECONDS)
    except asyncio.CancelledError:
        raise


async def get_active_run_by_thread(*, thread_id: str, current_uid: str, db: AsyncSession) -> dict:
    """读取线程当前仍需前端关注的最近一个 chat/resume run。"""
    from yuxi.storage.postgres.models_business import AgentRun

    # 线程内的 run 是串行的，最近一条 run 即代表线程当前状态。
    # 已被回复的 interrupted run 会被更晚创建的 resume run 取代，因此不会再被当作待处理中断返回。
    result = await db.execute(
        select(AgentRun)
        .where(
            AgentRun.conversation_thread_id == thread_id,
            AgentRun.uid == str(current_uid),
            AgentRun.run_type.in_(["chat", "resume"]),
        )
        .order_by(AgentRun.created_at.desc())
        .limit(1)
    )
    run = result.scalar_one_or_none()
    if run and run.status in ("pending", "running", "cancel_requested", "interrupted"):
        return {"run": run.to_dict()}
    return {"run": None}
