"""Tests for the cooperative cancellation API.

Pure-flag mechanics (cancel / is_cancel_pending / clear_cancel /
_consume_cancel) need no Stata; they live here. End-to-end tests of
"cancel + execute → cancelled RunResult" live in tests/test_runner.py
under the stata_required marker.
"""

from __future__ import annotations

import threading

import pytest

from stata_code.core import runner


@pytest.fixture(autouse=True)
def _isolate_cancel_state():
    """Each test starts with no pending cancels and ends the same way."""
    with runner._cancel_lock:
        runner._cancel_pending.clear()
    yield
    with runner._cancel_lock:
        runner._cancel_pending.clear()


class TestCancelMechanics:
    def test_cancel_sets_pending(self):
        assert runner.is_cancel_pending("main") is False
        assert runner.cancel("main") is True
        assert runner.is_cancel_pending("main") is True

    def test_cancel_is_idempotent(self):
        assert runner.cancel("main") is True
        # Second cancel for the same session returns False.
        assert runner.cancel("main") is False
        assert runner.is_cancel_pending("main") is True

    def test_cancel_isolates_per_session(self):
        runner.cancel("alpha")
        assert runner.is_cancel_pending("alpha") is True
        assert runner.is_cancel_pending("beta") is False

    def test_clear_cancel_drops_pending(self):
        runner.cancel("main")
        assert runner.clear_cancel("main") is True
        assert runner.is_cancel_pending("main") is False
        # Idempotent: calling clear when nothing is pending is a no-op.
        assert runner.clear_cancel("main") is False

    def test_consume_cancel_pops_once(self):
        runner.cancel("main")
        assert runner._consume_cancel("main") is True
        assert runner._consume_cancel("main") is False
        assert runner.is_cancel_pending("main") is False

    def test_thread_safety(self):
        """Concurrent cancels for the same session yield exactly one True."""
        results: list[bool] = []
        lock = threading.Lock()

        def hit() -> None:
            r = runner.cancel("main")
            with lock:
                results.append(r)

        threads = [threading.Thread(target=hit) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # Exactly one of the 20 threads got True; the rest got False.
        assert sum(results) == 1
        assert runner.is_cancel_pending("main") is True


class TestMcpCancelTool:
    """The MCP `cancel_session` tool dispatches without needing Stata."""

    def test_dispatch_first_call_was_pending_false(self):
        import asyncio

        from mcp.types import CallToolResult  # noqa: F401  (import gate)

        from stata_code.mcp.server import _dispatch

        out = asyncio.run(_dispatch("cancel_session", {"session_id": "main"}))
        assert out.isError is False
        body = out.structuredContent
        # killed_worker is False when no subprocess worker exists for this
        # session_id in the default pool (no prior `stata_run` for "main").
        assert body == {
            "session_id": "main",
            "was_pending": False,
            "is_pending": True,
            "killed_worker": False,
        }

    def test_dispatch_second_call_was_pending_true(self):
        import asyncio

        from stata_code.mcp.server import _dispatch

        asyncio.run(_dispatch("cancel_session", {"session_id": "main"}))
        out = asyncio.run(_dispatch("cancel_session", {"session_id": "main"}))
        body = out.structuredContent
        assert body == {
            "session_id": "main",
            "was_pending": True,
            "is_pending": True,
            "killed_worker": False,
        }

    def test_dispatch_default_session_id(self):
        import asyncio

        from stata_code.mcp.server import _dispatch

        out = asyncio.run(_dispatch("cancel_session", {}))
        body = out.structuredContent
        assert body["session_id"] == "main"


def test_cancel_tool_listed_in_registry():
    from stata_code.mcp.server import _tool_definitions

    names = {t.name for t in _tool_definitions()}
    assert "cancel_session" in names
