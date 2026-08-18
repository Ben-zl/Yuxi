"""AgentScope cutover 与 steer 中断边界测试。"""

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
