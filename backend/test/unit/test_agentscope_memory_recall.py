"""Yuxi ReMe 召回候选治理测试。"""

import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import frontmatter
import pytest
from agentscope.message import AssistantMsg, UserMsg

from yuxi.agentscope.memory import AUTO_MEMORY_EXTRACTION_HINT, YuxiReMeCore

pytestmark = pytest.mark.unit


def test_auto_memory_extraction_hint_rejects_runtime_state_and_inference():
    """提取提示必须禁止运行态和测试代号的环境推断。"""
    assert "pending" in AUTO_MEMORY_EXTRACTION_HINT
    assert "派发" in AUTO_MEMORY_EXTRACTION_HINT
    assert "等待" in AUTO_MEMORY_EXTRACTION_HINT
    assert "承诺交付" in AUTO_MEMORY_EXTRACTION_HINT
    assert "不得推断" in AUTO_MEMORY_EXTRACTION_HINT


async def test_auto_memory_governance_archives_cross_date_source_and_reindexes(tmp_path, monkeypatch):
    """auto_memory 写入后必须先治理同主题来源，再刷新完整索引。"""
    old_daily = tmp_path / "daily" / "2026-09-01"
    new_daily = tmp_path / "daily" / "2026-09-02"
    old_daily.mkdir(parents=True)
    new_daily.mkdir(parents=True)
    canonical = old_daily / "yuxi-canonical-alpha.md"
    canonical.write_text(
        "---\n"
        "name: 稳定主题\n"
        "yuxi_memory_status: canonical\n"
        "yuxi_topic_key: pc-performance-retrieval:alpha:2026-09-01\n"
        "yuxi_source_paths: []\n"
        "---\n稳定事实。\n",
        encoding="utf-8",
    )
    source = new_daily / "new.md"
    source.write_text(
        "---\nname: new\n---\n"
        "请求获取 alpha 项目 PC-分支-性能监控数据，"
        "AUTOMATION_PROJECT_ID=alpha，数据周期 2026.9.1。\n",
        encoding="utf-8",
    )
    calls = []
    monkeypatch.setattr(
        "yuxi.agentscope.memory.shanghai_now",
        lambda: datetime(2026, 9, 2, 12, 0, 0),
    )

    async def run_job(name, **kwargs):
        calls.append((name, kwargs))
        if name == "search":
            return SimpleNamespace(success=True, metadata={"results": []})
        return SimpleNamespace(success=True, metadata={})

    core = YuxiReMeCore(
        app=SimpleNamespace(run_job=run_job, close=AsyncMock()),
        workspace_dir=tmp_path,
        top_k=5,
    )
    agent = SimpleNamespace(state=SimpleNamespace(session_id="session-1", context=[]))
    inputs = {"inputs": UserMsg("user", "记住 alpha 项目 PC 性能数据")}

    async def next_handler(**_kwargs):
        agent.state.context.extend(
            [
                UserMsg("user", "获取 alpha 项目 2026.9.1 的 PC 性能数据"),
                AssistantMsg(
                    "assistant",
                    "性能数据已确认正常。已创建团队并委派子智能体，正在等待结果回报。之后将交付报告。",
                ),
            ],
        )
        yield "reply"

    assert [item async for item in core.on_reply(agent, inputs, next_handler)] == ["reply"]

    names = [name for name, _kwargs in calls]
    assert names.index("auto_memory") < names.index("reindex")
    auto_memory_kwargs = next(kwargs for name, kwargs in calls if name == "auto_memory")
    assert auto_memory_kwargs["memory_hint"] == AUTO_MEMORY_EXTRACTION_HINT
    assert "hint" not in auto_memory_kwargs
    extraction_input = json.dumps(auto_memory_kwargs["messages"], ensure_ascii=False)
    assert "获取 alpha 项目 2026.9.1" in extraction_input
    assert "性能数据已确认正常" in extraction_input
    assert "创建团队" not in extraction_input
    assert "委派子智能体" not in extraction_input
    assert "等待结果回报" not in extraction_input
    assert "将交付报告" not in extraction_input
    assert frontmatter.load(source)["yuxi_memory_status"] == "archived_duplicate"
    assert "daily/2026-09-02/new.md" in frontmatter.load(canonical)["yuxi_source_paths"]


async def test_recall_filters_archived_and_prefers_digest_then_canonical(tmp_path):
    """归档来源不进入上下文，同路径去重后 Digest 和 canonical 优先。"""
    archived = tmp_path / "daily" / "2026-09-01" / "source.md"
    canonical = tmp_path / "daily" / "2026-09-01" / "canonical.md"
    digest = tmp_path / "digest" / "procedure" / "workflow.md"
    normal = tmp_path / "daily" / "2026-09-01" / "normal.md"
    date_index = tmp_path / "daily" / "2026-09-01.md"
    transcript = tmp_path / "session" / "dialog" / "session-a.jsonl"
    for path in (archived, canonical, digest, normal):
        path.parent.mkdir(parents=True, exist_ok=True)
    transcript.parent.mkdir(parents=True)
    archived.write_text("---\nyuxi_memory_status: archived_duplicate\n---\nOld pending request.\n", encoding="utf-8")
    canonical.write_text(
        "---\nyuxi_memory_status: canonical\nyuxi_topic_key: topic-a\n---\nCanonical fact.\n", encoding="utf-8"
    )
    digest.write_text("Completed reusable procedure.", encoding="utf-8")
    normal.write_text("A running observation.", encoding="utf-8")
    date_index.write_text("Date index containing archived pending summaries.", encoding="utf-8")
    transcript.write_text("Raw transcript.", encoding="utf-8")
    response = SimpleNamespace(
        success=True,
        metadata={
            "results": [
                {"path": "daily/2026-09-01/source.md", "text": "Old pending request.", "scores": {"score": 0.99}},
                {"path": "daily/2026-09-01/normal.md", "text": "A running observation.", "scores": {"score": 0.95}},
                {"path": "daily/2026-09-01/canonical.md", "text": "Canonical fact.", "scores": {"score": 0.80}},
                {
                    "path": "digest/procedure/workflow.md",
                    "text": "Completed reusable procedure.",
                    "scores": {"score": 0.70},
                },
                {"path": "digest/procedure/workflow.md", "text": "Duplicate chunk.", "scores": {"score": 0.60}},
                {
                    "path": "daily/2026-09-01.md",
                    "text": "Date index containing archived pending summaries.",
                    "scores": {"score": 1.0},
                },
                {"path": "session/dialog/session-a.jsonl", "text": "Raw transcript.", "scores": {"score": 1.0}},
            ],
        },
    )
    app = SimpleNamespace(run_job=AsyncMock(return_value=response), close=AsyncMock())
    core = YuxiReMeCore(app=app, workspace_dir=tmp_path, top_k=5)

    memories = await core.search("performance")

    assert memories == ["Completed reusable procedure.", "Canonical fact.", "A running observation."]
    app.run_job.assert_awaited_once_with("search", query="performance", limit=15)


async def test_recall_prefers_terminal_state_within_same_topic(tmp_path):
    """同一主题存在多张非归档卡片时，完成态优先于 running 和 pending。"""
    daily = tmp_path / "daily" / "2026-09-01"
    daily.mkdir(parents=True)
    for name, state in (
        ("pending", "等待结果 pending"),
        ("running", "任务 running"),
        ("completed", "任务已完成 completed"),
    ):
        (daily / f"{name}.md").write_text(
            f"---\nyuxi_topic_key: shared-topic\n---\n{state}\n",
            encoding="utf-8",
        )
    response = SimpleNamespace(
        success=True,
        metadata={
            "results": [
                {"path": f"daily/2026-09-01/{name}.md", "text": state, "scores": {"score": score}}
                for (name, state), score in zip(
                    (
                        ("pending", "等待结果 pending"),
                        ("running", "任务 running"),
                        ("completed", "任务已完成 completed"),
                    ),
                    (0.99, 0.90, 0.70),
                    strict=True,
                )
            ],
        },
    )
    core = YuxiReMeCore(
        app=SimpleNamespace(run_job=AsyncMock(return_value=response), close=AsyncMock()),
        workspace_dir=tmp_path,
        top_k=5,
    )

    assert await core.search("shared") == ["任务已完成 completed"]


async def test_recall_keeps_digest_instead_of_canonical_for_same_stable_keys(tmp_path):
    """Dream Digest 与 canonical 指向同一稳定键时，只保留 Digest。"""
    canonical = tmp_path / "daily" / "2026-09-01" / "canonical.md"
    digest = tmp_path / "digest" / "personal" / "stable.md"
    canonical.parent.mkdir(parents=True)
    digest.parent.mkdir(parents=True)
    canonical.write_text(
        "---\nyuxi_memory_status: canonical\n---\n"
        "星砂岛 PC-分支-性能监控 2026.8.28，AUTOMATION_PROJECT_ID=starsandisland。\n",
        encoding="utf-8",
    )
    digest.write_text(
        "星砂岛 PC-分支-性能监控 2026.8.28，AUTOMATION_PROJECT_ID=starsandisland，AUTOMATION_USER_ID=2325。\n",
        encoding="utf-8",
    )
    response = SimpleNamespace(
        success=True,
        metadata={
            "results": [
                {"path": "daily/2026-09-01/canonical.md", "text": canonical.read_text(), "scores": {"score": 0.9}},
                {"path": "digest/personal/stable.md", "text": digest.read_text(), "scores": {"score": 0.7}},
            ],
        },
    )
    core = YuxiReMeCore(
        app=SimpleNamespace(run_job=AsyncMock(return_value=response), close=AsyncMock()),
        workspace_dir=tmp_path,
        top_k=5,
    )

    memories = await core.search("星砂岛 PC 性能数据")

    assert memories == [digest.read_text(encoding="utf-8").strip()]


async def test_recall_failure_is_exposed_to_scoped_fail_open_boundary(tmp_path):
    """ReMe success=false 应作为异常交给外层 Scoped middleware fail-open。"""
    app = SimpleNamespace(
        run_job=AsyncMock(return_value=SimpleNamespace(success=False, answer="index unavailable")),
        close=AsyncMock(),
    )
    core = YuxiReMeCore(app=app, workspace_dir=tmp_path, top_k=5)

    with pytest.raises(RuntimeError, match="index unavailable"):
        await core.search("query")
