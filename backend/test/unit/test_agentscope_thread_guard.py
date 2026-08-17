"""AgentScope cutover 与 steer 中断边界测试。"""

from unittest.mock import AsyncMock

import pytest

from yuxi.agentscope import thread_guard

pytestmark = pytest.mark.unit


def test_cutoff_is_required_and_valid(monkeypatch):
    monkeypatch.delenv("AGENTSCOPE_LEGACY_CUTOFF", raising=False)
    with pytest.raises(RuntimeError, match="未配置"):
        thread_guard._legacy_cutoff()

    monkeypatch.setenv("AGENTSCOPE_LEGACY_CUTOFF", "not-a-time")
    with pytest.raises(RuntimeError, match="ISO"):
        thread_guard._legacy_cutoff()


async def test_steer_interrupt_failure_is_not_silenced(monkeypatch):
    mapping = type(
        "Mapping",
        (),
        {"agentscope_agent_id": "a", "agentscope_session_id": "s"},
    )()
    monkeypatch.setattr(thread_guard, "get_thread_session", AsyncMock(return_value=mapping))
    interrupt = AsyncMock(side_effect=RuntimeError("service down"))
    monkeypatch.setattr(thread_guard.AgentScopeServiceClient, "interrupt_session", interrupt)

    with pytest.raises(RuntimeError, match="service down"):
        await thread_guard.interrupt_thread_session(AsyncMock(), uid="u", agent_slug="agent", thread_id="thread")
