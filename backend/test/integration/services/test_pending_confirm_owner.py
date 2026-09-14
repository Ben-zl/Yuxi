"""线程挂起审批只可由所属 Run 清理。"""

from __future__ import annotations

from uuid import uuid4

import pytest

from yuxi.agentscope.thread_guard import clear_pending_confirm, load_pending_confirm, store_pending_confirm

pytestmark = pytest.mark.integration


async def test_pending_confirm_clear_requires_matching_run_owner() -> None:
    """旧 Run 的清理不得删除后来覆盖的审批事件。"""
    thread_id = f"pending-owner-{uuid4().hex}"
    try:
        await store_pending_confirm(thread_id, {"reply_id": "new-reply"}, run_id="new-run")
        await clear_pending_confirm(thread_id, expected_run_id="old-run")
        assert (await load_pending_confirm(thread_id))["reply_id"] == "new-reply"
        await clear_pending_confirm(thread_id, expected_run_id="new-run")
        assert await load_pending_confirm(thread_id) is None
    finally:
        await clear_pending_confirm(thread_id)
