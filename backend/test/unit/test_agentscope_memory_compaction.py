"""ReMe Daily 近重复归并测试。"""

from unittest.mock import AsyncMock

import frontmatter
import pytest

from yuxi.agentscope.memory_compaction import compact_daily_memories, sanitize_daily_runtime_state

pytestmark = pytest.mark.unit


def _write_samples(root):
    """写入与真实问题等价但不依赖生产目录的三张 Daily。"""
    daily = root / "daily" / "2026-09-01"
    daily.mkdir(parents=True)
    (daily / "request-a.md").write_text(
        "---\nname: xingsha-island-pc-perf-data-retrieval\nsession_id: session-a\n---\n"
        "# 星砂岛 PC-分支-性能监控 2026.8.28 性能数据获取\n"
        "用户请求获取 2026.8.28 的性能数据，任务已派发，结果仍 pending。\n",
        encoding="utf-8",
    )
    (daily / "request-b.md").write_text(
        "---\nname: starsandisland-pc-perf-monitor-data-request\nsession_id: session-b\n---\n"
        "# 星砂岛 PC-分支-性能监控 数据获取请求\n"
        "AUTOMATION_PROJECT_ID=starsandisland，AUTOMATION_USER_ID=2325，数据周期 2026.8.28。\n",
        encoding="utf-8",
    )
    (daily / "mixed.md").write_text(
        "---\nname: starsand-island-pc-performance-analysis\nsession_id: session-c\n---\n"
        "# 星砂岛 PC 性能监控与分析\n"
        "AUTOMATION_PROJECT_ID=starsandisland，AUTOMATION_USER_ID=2325，获取 2026.8.28 性能数据。\n"
        "## 基础性能分析任务（大规模DIY）\n"
        "用例 ci=13740 在 #246 周期卡顿、低 FPS，等待瓶颈定位与优化建议。\n",
        encoding="utf-8",
    )
    return daily


async def test_compaction_splits_real_shape_into_two_canonical_topics(tmp_path):
    """三个近重复来源应归并为数据获取和 DIY 分析两个 canonical 主题。"""
    daily = _write_samples(tmp_path)
    maintain_index = AsyncMock()

    result = await compact_daily_memories(
        tmp_path,
        date="2026-09-01",
        maintain_index=maintain_index,
    )

    assert result.changed is True
    assert result.canonical_count == 2
    canonical = [frontmatter.load(path) for path in daily.glob("yuxi-canonical-*.md")]
    assert {post["name"] for post in canonical} == {
        "星砂岛 PC 性能监控数据获取",
        "星砂岛大规模 DIY 性能分析",
    }
    retrieval = next(post for post in canonical if post["name"] == "星砂岛 PC 性能监控数据获取")
    analysis = next(post for post in canonical if post["name"] == "星砂岛大规模 DIY 性能分析")
    assert len(retrieval["yuxi_source_paths"]) == 3
    assert len(analysis["yuxi_source_paths"]) == 1
    for name in ("request-a.md", "request-b.md", "mixed.md"):
        assert frontmatter.load(daily / name)["yuxi_memory_status"] == "archived_duplicate"
    maintain_index.assert_awaited_once_with("2026-09-01")


async def test_compaction_is_idempotent_and_preserves_source_session_metadata(tmp_path):
    """重复扫描不新增 canonical，且来源 session_id 始终保留。"""
    daily = _write_samples(tmp_path)
    maintain_index = AsyncMock()

    first = await compact_daily_memories(tmp_path, date="2026-09-01", maintain_index=maintain_index)
    second = await compact_daily_memories(tmp_path, date="2026-09-01", maintain_index=maintain_index)

    assert first.changed is True
    assert second.changed is False
    assert len(list(daily.glob("yuxi-canonical-*.md"))) == 2
    assert frontmatter.load(daily / "request-a.md")["session_id"] == "session-a"
    assert maintain_index.await_count == 1


async def test_compaction_rolls_back_files_when_reindex_fails(tmp_path):
    """索引失败必须恢复原文件，不能留下半归并状态。"""
    daily = _write_samples(tmp_path)
    originals = {path.name: path.read_text(encoding="utf-8") for path in daily.glob("*.md")}
    maintain_index = AsyncMock(side_effect=RuntimeError("reindex failed"))

    with pytest.raises(RuntimeError, match="reindex failed"):
        await compact_daily_memories(tmp_path, date="2026-09-01", maintain_index=maintain_index)

    assert not list(daily.glob("yuxi-canonical-*.md"))
    assert {path.name: path.read_text(encoding="utf-8") for path in daily.glob("*.md")} == originals


async def test_compaction_leaves_low_confidence_daily_untouched(tmp_path):
    """没有稳定键或高置信重复证据时不得自动归并。"""
    daily = tmp_path / "daily" / "2026-09-01"
    daily.mkdir(parents=True)
    (daily / "preference.md").write_text("用户喜欢清淡饮食。", encoding="utf-8")
    (daily / "meeting.md").write_text("明天下午参加项目会议。", encoding="utf-8")
    maintain_index = AsyncMock()

    result = await compact_daily_memories(tmp_path, date="2026-09-01", maintain_index=maintain_index)

    assert result.changed is False
    assert result.canonical_count == 0
    maintain_index.assert_not_awaited()


async def test_compaction_generalizes_project_and_data_period_stable_keys(tmp_path):
    """后续项目和周期的重复请求也应按稳定键归并，而非只修当前样本。"""
    daily = tmp_path / "daily" / "2026-09-02"
    daily.mkdir(parents=True)
    for name, user_id in (("first", "1001"), ("second", "1001")):
        (daily / f"{name}.md").write_text(
            "---\n"
            f"session_id: {name}-session\n"
            "---\n"
            "请求获取 alpha 项目 PC-分支-性能监控的性能数据，"
            f"AUTOMATION_PROJECT_ID=alpha，AUTOMATION_USER_ID={user_id}，数据周期 2026.9.1。\n",
            encoding="utf-8",
        )

    result = await compact_daily_memories(
        tmp_path,
        date="2026-09-02",
        maintain_index=AsyncMock(),
    )

    canonical_paths = list(daily.glob("yuxi-canonical-*.md"))
    assert result.canonical_count == 1
    assert len(canonical_paths) == 1
    canonical = frontmatter.load(canonical_paths[0])
    assert canonical["name"] == "alpha PC 性能监控数据获取"
    assert canonical["yuxi_topic_key"] == "pc-performance-retrieval:alpha:2026-09-01"
    assert len(canonical["yuxi_source_paths"]) == 2


async def test_compaction_archives_single_new_daily_against_existing_canonical(tmp_path):
    """新日期只有一张来源时，命中历史 canonical 也必须归档并追加来源。"""
    old_daily = tmp_path / "daily" / "2026-09-01"
    new_daily = tmp_path / "daily" / "2026-09-02"
    old_daily.mkdir(parents=True)
    new_daily.mkdir(parents=True)
    canonical = old_daily / "yuxi-canonical-starsand-island-pc-performance-retrieval.md"
    canonical.write_text(
        "---\n"
        "name: 星砂岛 PC 性能监控数据获取\n"
        "description: 稳定主题\n"
        "yuxi_memory_status: canonical\n"
        "yuxi_topic_key: starsand-island-pc-performance-retrieval\n"
        "yuxi_source_paths:\n"
        "  - daily/2026-09-01/source-a.md\n"
        "---\n"
        "稳定的性能监控数据获取主题。\n",
        encoding="utf-8",
    )
    source = new_daily / "new-request.md"
    source.write_text(
        "---\nname: new-request\nsession_id: session-new\n---\n"
        "请求获取星砂岛 PC-分支-性能监控数据，"
        "AUTOMATION_PROJECT_ID=starsandisland，数据周期 2026.8.28。\n",
        encoding="utf-8",
    )

    result = await compact_daily_memories(tmp_path, date="2026-09-02", maintain_index=AsyncMock())

    assert result.changed is True
    assert result.canonical_count == 1
    assert frontmatter.load(source)["yuxi_memory_status"] == "archived_duplicate"
    updated = frontmatter.load(canonical)
    assert updated["yuxi_source_paths"] == [
        "daily/2026-09-01/source-a.md",
        "daily/2026-09-02/new-request.md",
    ]


def test_daily_runtime_state_is_removed_but_stable_request_is_preserved(tmp_path):
    """写后治理必须移除团队派发和等待状态，同时保留稳定请求与 Session 元数据。"""
    daily = tmp_path / "daily" / "2026-09-02"
    daily.mkdir(parents=True)
    path = daily / "request.md"
    path.write_text(
        "---\n"
        "name: request\n"
        "session_id: session-1\n"
        "description: 用户请求 alpha 性能数据，项目 ID 为 alpha，已创建团队并委派子智能体，正在等待结果回报。\n"
        "---\n"
        "# alpha 性能数据请求\n\n"
        "- 请求参数：\n"
        "  - AUTOMATION_PROJECT_ID=alpha\n"
        "## 执行状态\n"
        "- 已创建团队并委派给自动化分析子智能体。\n"
        "- 尚未返回结果，之后将交付报告。\n"
        "## 备注\n"
        "- 可复用稳定标识：AUTOMATION_USER_ID=1001。\n"
        "- 可复用流程：创建团队→委派子智能体。\n",
        encoding="utf-8",
    )

    assert sanitize_daily_runtime_state(tmp_path, date="2026-09-02") is True

    cleaned = path.read_text(encoding="utf-8")
    assert "AUTOMATION_PROJECT_ID=alpha" in cleaned
    assert "  - AUTOMATION_PROJECT_ID=alpha" in cleaned
    assert "AUTOMATION_USER_ID=1001" in cleaned
    assert "项目 ID 为 alpha" in cleaned
    assert "session_id: session-1" in cleaned
    assert "创建团队" not in cleaned
    assert "委派" not in cleaned
    assert "等待结果" not in cleaned
    assert "尚未返回" not in cleaned
    assert "之后将交付" not in cleaned
    assert "执行状态" not in cleaned
