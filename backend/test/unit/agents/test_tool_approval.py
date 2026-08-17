import pytest

from yuxi.agents.tool_approval import (
    normalize_tool_approval_mode,
)


def test_unknown_tool_approval_mode_is_rejected():
    with pytest.raises(ValueError, match="不支持的 tool_approval_mode"):
        normalize_tool_approval_mode("unknown")
