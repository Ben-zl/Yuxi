"""知识库工具可见性与 LITE 门控单测（迁移工单 07）。"""

from types import SimpleNamespace

import pytest

from yuxi.agentscope import tools

pytestmark = pytest.mark.unit


def _summary(kb_id: str, name: str) -> SimpleNamespace:
    return SimpleNamespace(kb_id=kb_id, name=name, description=f"{name} 描述", kb_type="milvus")


@pytest.fixture
def kb_manager(monkeypatch):
    """替换知识库管理器的用户查询，返回固定可见集。"""
    summaries = [_summary("kb-1", "产品手册"), _summary("kb-2", "内部规范")]

    async def fake_get_databases_by_uid(uid: str):
        return summaries if uid == "u1" else []

    import yuxi.knowledge.runtime as runtime

    monkeypatch.setattr(runtime.knowledge_base, "get_databases_by_uid", fake_get_databases_by_uid)
    return runtime.knowledge_base


async def test_visible_knowledge_bases_filters_by_user_and_session(kb_manager):
    visible = await tools._visible_knowledge_bases("u1", None)
    assert [kb["kb_id"] for kb in visible] == ["kb-1", "kb-2"]

    # 会话启用集按 kb_id 交集
    visible = await tools._visible_knowledge_bases("u1", ["kb-2"])
    assert [kb["kb_id"] for kb in visible] == ["kb-2"]

    # 其他用户无可见知识库
    assert await tools._visible_knowledge_bases("u2", None) == []


async def test_target_visibility_check(kb_manager):
    assert await tools._check_target_visible("u1", None, "kb-1") is None
    error = await tools._check_target_visible("u1", ["kb-2"], "kb-1")
    assert "不可见" in error
    assert "不存在" in await tools._check_target_visible("u2", None, "kb-1")


async def test_lite_mode_skips_kb_tools(monkeypatch, kb_manager):
    monkeypatch.setenv("LITE_MODE", "true")
    monkeypatch.setattr(tools, "_kb_initialize_attempted", False)
    assert await tools.build_kb_tools(uid="u1", knowledge_slugs=None) == []


async def test_build_kb_tools_returns_toolset(monkeypatch, kb_manager):
    monkeypatch.delenv("LITE_MODE", raising=False)
    monkeypatch.setattr(tools, "_kb_initialize_attempted", True)
    toolset = await tools.build_kb_tools(uid="u1", knowledge_slugs=None)
    names = {t.name for t in toolset}
    assert {"list_kbs", "query_kb", "open_kb_document"} <= names
