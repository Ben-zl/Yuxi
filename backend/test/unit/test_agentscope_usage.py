"""AgentScope token 用量归一与线程聚合测试。"""

from yuxi.agentscope.usage import UsageAccumulator, token_usage_view


def test_usage_accumulator_groups_calls_by_real_model():
    accumulator = UsageAccumulator(configured_model_spec="provider:configured")
    for event in (
        {"type": "MODEL_CALL_START", "model_name": "response-model"},
        {"type": "MODEL_CALL_END", "input_tokens": 10, "output_tokens": 3},
        {"type": "MODEL_CALL_START", "model_name": "response-model"},
        {"type": "MODEL_CALL_END", "input_tokens": 12, "output_tokens": 4},
    ):
        accumulator.observe(event)

    usage = accumulator.snapshot(complete=True)
    assert usage["complete"] is True
    assert usage["run"]["total"] == {"input_tokens": 22, "output_tokens": 7, "total_tokens": 29}
    assert usage["run"]["models"]["response-model"]["model_call_count"] == 2
    assert usage["latest"]["usage"]["total_tokens"] == 16


def test_token_usage_view_aggregates_thread_without_inventing_cache_metrics():
    first = UsageAccumulator("model-a")
    first.observe({"type": "MODEL_CALL_END", "input_tokens": 5, "output_tokens": 2})
    second = UsageAccumulator("model-a")
    second.observe({"type": "MODEL_CALL_END", "input_tokens": 7, "output_tokens": 3})
    latest = second.snapshot(complete=True)

    view = token_usage_view(latest, [first.snapshot(complete=True), latest])

    assert view["run"]["total"]["total_tokens"] == 10
    assert view["thread"]["total"] == {"input_tokens": 12, "output_tokens": 5, "total_tokens": 17}
    bucket = view["thread"]["models"]["model-a"]
    assert bucket["model_call_count"] == 2
    assert bucket["cache_observed_call_count"] == 0


def test_usage_accumulator_merges_context_snapshot_and_summary_state():
    accumulator = UsageAccumulator("model-a")
    accumulator.observe(
        {
            "type": "CUSTOM",
            "name": "token_context",
            "value": {
                "llm_input_tokens": 90,
                "context_window": 1000,
                "summary_trigger_tokens": 800,
                "summary_active": False,
            },
        }
    )
    accumulator.observe(
        {
            "type": "CUSTOM",
            "name": "context_compression",
            "value": {"status": "completed", "summary_active": True},
        }
    )
    accumulator.observe({"type": "MODEL_CALL_END", "input_tokens": 100, "output_tokens": 20})

    usage = accumulator.snapshot(complete=True)
    assert usage["llm_input_tokens"] == 90
    assert usage["context_window"] == 1000
    assert usage["summary_trigger_tokens"] == 800
    assert usage["summary_active"] is True
