"""AgentScope 模型事件到 Yuxi 用量协议的归一与聚合。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from agentscope.model import AnthropicChatModel, GeminiChatModel, OpenAIChatModel

CacheInputMode = Literal["additive", "inclusive", "unknown"]


def cache_input_mode_for_model(model: object) -> CacheInputMode:
    """按 AgentScope 模型适配器确定缓存输入 Token 口径。"""
    if isinstance(model, AnthropicChatModel):
        return "additive"
    if isinstance(model, (OpenAIChatModel, GeminiChatModel)):
        return "inclusive"
    return "unknown"


def _total(input_tokens: int = 0, output_tokens: int = 0) -> dict[str, int]:
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
    }


def _model_bucket(
    model_name: str,
    usage: dict,
    call_count: int,
    *,
    cache_observed_input_tokens: int = 0,
    cache_read_input_tokens: int = 0,
    cache_creation_input_tokens: int = 0,
    cache_observed_call_count: int = 0,
) -> dict:
    """构造前端可稳定消费、且不虚构缓存数据的模型分桶。"""
    cache_hit_ratio = (
        cache_read_input_tokens / cache_observed_input_tokens
        if cache_observed_input_tokens
        else None
    )
    return {
        "model": {
            "configured_model_spec": model_name,
            "configured_model_id": model_name,
            "response_model_ids": [model_name],
        },
        "usage": usage,
        "model_call_count": call_count,
        "cache_observed_input_tokens": cache_observed_input_tokens,
        "cache_read_input_tokens": cache_read_input_tokens,
        "cache_creation_input_tokens": cache_creation_input_tokens,
        "cache_observed_call_count": cache_observed_call_count,
        "cache_hit_ratio": cache_hit_ratio,
    }


@dataclass
class UsageAccumulator:
    """按 AgentScope 模型调用事件累计一轮真实 token 用量。"""

    configured_model_spec: str | None = None
    native_cache_input_mode: CacheInputMode = "unknown"
    current_model: str | None = None
    reply_models: dict[str, str] = field(default_factory=dict)
    reply_cache_modes: dict[str, CacheInputMode] = field(default_factory=dict)
    reply_call_counts: dict[str, int] = field(default_factory=dict)
    active_call_keys: dict[str, str] = field(default_factory=dict)
    calls: list[tuple[str, dict]] = field(default_factory=list)
    context: dict = field(default_factory=dict)
    pending_model_usages: dict[str, dict] = field(default_factory=dict)
    pending_native_usages: dict[str, dict] = field(default_factory=dict)
    completed_reply_ids: set[str] = field(default_factory=set)

    def _call_key(self, reply_id: str) -> str:
        """返回 reply 当前模型调用键；一次 ReAct reply 可包含多次调用。"""
        return self.active_call_keys.get(reply_id, reply_id)

    @staticmethod
    def _normalize_usage(
        *,
        raw_input_tokens: int,
        output_tokens: int,
        cache_creation_input_tokens: int,
        cache_read_input_tokens: int,
        cache_input_mode: CacheInputMode,
    ) -> dict:
        """把 provider 原始计数归一为 Yuxi 的总输入和缓存分母。"""
        raw_input = max(int(raw_input_tokens or 0), 0)
        output = max(int(output_tokens or 0), 0)
        cache_creation = max(int(cache_creation_input_tokens or 0), 0)
        cache_read = max(int(cache_read_input_tokens or 0), 0)
        total_input = raw_input + cache_creation + cache_read if cache_input_mode == "additive" else raw_input
        cache_observed = cache_input_mode != "unknown" and (cache_creation > 0 or cache_read > 0)
        return {
            **_total(total_input, output),
            "raw_input_tokens": raw_input,
            "cache_input_mode": cache_input_mode,
            "cache_observed_input_tokens": total_input if cache_observed else 0,
            "cache_read_input_tokens": cache_read if cache_observed else 0,
            "cache_creation_input_tokens": cache_creation if cache_observed else 0,
            "cache_observed_call_count": int(cache_observed),
        }

    def _finalize_reply(self, reply_id: str) -> None:
        """按完整自定义用量优先规则累计一个 reply。"""
        if reply_id in self.completed_reply_ids:
            return
        custom = self.pending_model_usages.get(reply_id)
        native = self.pending_native_usages.get(reply_id)
        usage = custom if custom and custom.get("complete") else native
        if usage is None:
            return
        model_name = self.reply_models.get(reply_id) or self.configured_model_spec or "unknown_model"
        self.calls.append((model_name, usage))
        self.completed_reply_ids.add(reply_id)
        self.pending_model_usages.pop(reply_id, None)
        self.pending_native_usages.pop(reply_id, None)

    def observe(self, event: dict) -> None:
        """消费单个事件；无用量语义的事件被忽略。"""
        event_type = str(event.get("type") or "").upper()
        if event_type == "CUSTOM":
            value = event.get("value") if isinstance(event.get("value"), dict) else {}
            if event.get("name") == "token_context":
                self.context.update(value)
                reply_id = str(value.get("reply_id") or "")
                call_key = self._call_key(reply_id)
                mode = str(value.get("cache_input_mode") or "unknown")
                if call_key and mode in {"additive", "inclusive", "unknown"}:
                    self.reply_cache_modes[call_key] = mode
            elif event.get("name") == "context_compression" and "summary_active" in value:
                self.context["summary_active"] = bool(value["summary_active"])
            elif event.get("name") == "model_usage":
                reply_id = str(value.get("reply_id") or "")
                call_key = self._call_key(reply_id)
                mode = str(value.get("cache_input_mode") or "unknown")
                cache_input_mode: CacheInputMode = (
                    mode if mode in {"additive", "inclusive", "unknown"} else "unknown"
                )
                self.reply_cache_modes[call_key] = cache_input_mode
                usage = self._normalize_usage(
                    raw_input_tokens=value.get("raw_input_tokens", value.get("input_tokens", 0)),
                    output_tokens=value.get("output_tokens", 0),
                    cache_creation_input_tokens=value.get("cache_creation_input_tokens", 0),
                    cache_read_input_tokens=value.get("cache_read_input_tokens", 0),
                    cache_input_mode=cache_input_mode,
                )
                usage["complete"] = bool(value.get("complete"))
                self.pending_model_usages[call_key] = usage
                if usage["complete"] and call_key in self.pending_native_usages:
                    self._finalize_reply(call_key)
            return
        if event_type == "MODEL_CALL_START":
            reply_id = str(event.get("reply_id") or "")
            self.current_model = str(event.get("model_name") or self.configured_model_spec or "unknown_model")
            if reply_id:
                call_index = self.reply_call_counts.get(reply_id, 0) + 1
                self.reply_call_counts[reply_id] = call_index
                call_key = f"{reply_id}:{call_index}"
                self.active_call_keys[reply_id] = call_key
                self.reply_models[call_key] = self.current_model
            return
        if event_type != "MODEL_CALL_END":
            return
        reply_id = str(event.get("reply_id") or "")
        if reply_id and reply_id not in self.active_call_keys:
            return
        call_key = self.active_call_keys.pop(reply_id, "")
        if not call_key:
            call_key = f"native:{len(self.calls)}:{len(self.pending_native_usages)}"
            self.reply_models[call_key] = self.current_model or self.configured_model_spec or "unknown_model"
        cache_input_mode = self.reply_cache_modes.get(call_key, self.native_cache_input_mode)
        self.pending_native_usages[call_key] = self._normalize_usage(
            raw_input_tokens=event.get("input_tokens", 0),
            output_tokens=event.get("output_tokens", 0),
            cache_creation_input_tokens=event.get("cache_creation_input_tokens", 0),
            cache_read_input_tokens=event.get("cache_input_tokens", 0),
            cache_input_mode=cache_input_mode,
        )
        self._finalize_reply(call_key)

    def snapshot(self, *, complete: bool) -> dict:
        """生成一轮 Run 用量；complete 表示是否观察到正常收束。"""
        for reply_id, usage in list(self.pending_model_usages.items()):
            if usage.get("complete"):
                self._finalize_reply(reply_id)
        totals = _total()
        model_totals: dict[str, dict] = {}
        model_counts: dict[str, int] = {}
        model_cache: dict[str, dict[str, int]] = {}
        for model_name, usage in self.calls:
            totals["input_tokens"] += usage["input_tokens"]
            totals["output_tokens"] += usage["output_tokens"]
            totals["total_tokens"] += usage["total_tokens"]
            bucket = model_totals.setdefault(model_name, _total())
            for key in totals:
                bucket[key] += usage[key]
            model_counts[model_name] = model_counts.get(model_name, 0) + 1
            cache = model_cache.setdefault(
                model_name,
                {
                    "cache_observed_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                    "cache_observed_call_count": 0,
                },
            )
            for key in cache:
                cache[key] += max(int(usage.get(key) or 0), 0)
        models = {
            model_name: _model_bucket(
                model_name,
                usage,
                model_counts[model_name],
                **model_cache[model_name],
            )
            for model_name, usage in model_totals.items()
        }
        latest = None
        if self.calls:
            model_name, usage = self.calls[-1]
            latest = {"model_name": model_name, "usage": usage}
        return {
            "schema_version": 1,
            "available": bool(self.calls),
            **self.context,
            "complete": complete,
            **totals,
            "llm_input_tokens": self.context.get(
                "llm_input_tokens",
                self.calls[-1][1]["input_tokens"] if self.calls else 0,
            ),
            "run": {"total": totals, "models": models},
            "latest": latest,
        }


def aggregate_thread_usage(run_usages: list[dict]) -> dict:
    """把同一线程的 Run envelope 聚合为 Token 卡片线程视图。"""
    totals = _total()
    models: dict[str, dict] = {}
    for envelope in run_usages:
        run = envelope.get("run") if isinstance(envelope, dict) else None
        run_total = (run or {}).get("total") if isinstance(run, dict) else None
        if not isinstance(run_total, dict):
            run_total = envelope if isinstance(envelope, dict) else {}
        for key in totals:
            totals[key] += max(int(run_total.get(key) or 0), 0)
        for model_name, bucket in ((run or {}).get("models") or {}).items():
            target = models.setdefault(model_name, _model_bucket(model_name, _total(), 0))
            source_usage = bucket.get("usage") or {}
            for key in totals:
                target["usage"][key] += max(int(source_usage.get(key) or 0), 0)
            target["model_call_count"] += max(int(bucket.get("model_call_count") or 0), 0)
            for key in (
                "cache_observed_input_tokens",
                "cache_read_input_tokens",
                "cache_creation_input_tokens",
                "cache_observed_call_count",
            ):
                target[key] += max(int(bucket.get(key) or 0), 0)
            target["cache_hit_ratio"] = (
                target["cache_read_input_tokens"] / target["cache_observed_input_tokens"]
                if target["cache_observed_input_tokens"]
                else None
            )
    return {"total": totals, "models": models}


def token_usage_view(latest_usage: dict | None, run_usages: list[dict]) -> dict | None:
    """组合最近 Run 的上下文视图与线程累计值。"""
    if not latest_usage:
        return None
    summary_active = bool(latest_usage.get("summary_active")) or any(
        bool(usage.get("summary_active"))
        for usage in run_usages
        if isinstance(usage, dict)
    )
    return {
        **latest_usage,
        "summary_active": summary_active,
        "thread": aggregate_thread_usage(run_usages),
    }
