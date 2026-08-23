"""AgentScope 模型事件到 Yuxi 用量协议的归一与聚合。"""

from __future__ import annotations

from dataclasses import dataclass, field


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
    current_model: str | None = None
    reply_models: dict[str, str] = field(default_factory=dict)
    calls: list[tuple[str, dict]] = field(default_factory=list)
    context: dict = field(default_factory=dict)
    pending_model_usages: dict[str, dict] = field(default_factory=dict)

    def observe(self, event: dict) -> None:
        """消费单个事件；无用量语义的事件被忽略。"""
        event_type = str(event.get("type") or "").upper()
        if event_type == "CUSTOM":
            value = event.get("value") if isinstance(event.get("value"), dict) else {}
            if event.get("name") == "token_context":
                self.context.update(value)
            elif event.get("name") == "context_compression" and "summary_active" in value:
                self.context["summary_active"] = bool(value["summary_active"])
            elif event.get("name") == "model_usage":
                reply_id = str(value.get("reply_id") or "")
                input_tokens = max(int(value.get("input_tokens") or 0), 0)
                output_tokens = max(int(value.get("output_tokens") or 0), 0)
                cache_creation = max(int(value.get("cache_creation_input_tokens") or 0), 0)
                cache_read = max(int(value.get("cache_read_input_tokens") or 0), 0)
                total_input = input_tokens + cache_creation + cache_read
                cache_observed = cache_creation > 0 or cache_read > 0
                self.pending_model_usages[reply_id] = {
                    **_total(total_input, output_tokens),
                    "cache_observed_input_tokens": total_input if cache_observed else 0,
                    "cache_read_input_tokens": cache_read,
                    "cache_creation_input_tokens": cache_creation,
                    "cache_observed_call_count": int(cache_observed),
                }
            return
        if event_type == "MODEL_CALL_START":
            self.current_model = str(event.get("model_name") or self.configured_model_spec or "unknown_model")
            reply_id = str(event.get("reply_id") or "")
            if reply_id:
                self.reply_models[reply_id] = self.current_model
            return
        if event_type != "MODEL_CALL_END":
            return
        reply_id = str(event.get("reply_id") or "")
        model_name = (
            self.reply_models.pop(reply_id, None)
            or self.current_model
            or self.configured_model_spec
            or "unknown_model"
        )
        usage = self.pending_model_usages.pop(reply_id, None) or _total(
            max(int(event.get("input_tokens") or 0), 0),
            max(int(event.get("output_tokens") or 0), 0),
        )
        self.calls.append((model_name, usage))

    def snapshot(self, *, complete: bool) -> dict:
        """生成一轮 Run 用量；complete 表示是否观察到正常收束。"""
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
            "complete": complete,
            **self.context,
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
