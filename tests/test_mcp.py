"""Tests for the v0.2 MCP server (rewired to the v1.0 runner pipeline)."""

from __future__ import annotations

import asyncio
import json

import pytest

pytest.importorskip("mcp", reason="mcp package not installed")

from mcp.types import ImageContent, TextContent  # noqa: E402

from stata_code.core._runtime import is_available  # noqa: E402

_real_stata = is_available()


# ─────────────────────────────────────────────────────────────────────────────
# Tool-registry surface (no Stata required)
# ─────────────────────────────────────────────────────────────────────────────


class TestToolRegistry:
    def test_lists_expected_tools(self):
        from stata_code.mcp.server import _tool_definitions

        names = {t.name for t in _tool_definitions()}
        assert {
            "stata_run",
            "stata_info",
            "get_log",
            "get_graph",
            "get_matrix",
            "list_sessions",
            "reset_session",
        }.issubset(names)

    def test_stata_run_schema_requires_code(self):
        from stata_code.mcp.server import _tool_definitions

        run = next(t for t in _tool_definitions() if t.name == "stata_run")
        schema = run.inputSchema
        assert schema["type"] == "object"
        assert "code" in schema["properties"]
        assert "code" in schema["required"]
        # Token-economy options are exposed
        assert "include_graphs" in schema["properties"]
        assert "include_full_log" in schema["properties"]

    def test_get_graph_schema_requires_ref(self):
        from stata_code.mcp.server import _tool_definitions

        gg = next(t for t in _tool_definitions() if t.name == "get_graph")
        assert "ref" in gg.inputSchema["required"]


# ─────────────────────────────────────────────────────────────────────────────
# Dispatch surface (no Stata required for non-execution branches)
# ─────────────────────────────────────────────────────────────────────────────


class TestDispatch:
    def test_unknown_tool_returns_text_error(self):
        from stata_code.mcp.server import _dispatch

        out = asyncio.run(_dispatch("nonexistent_tool", {}))
        assert len(out) == 1
        assert isinstance(out[0], TextContent)
        assert "Unknown tool" in out[0].text

    def test_get_log_unknown_ref_returns_error_text(self):
        from stata_code.mcp.server import _dispatch

        out = asyncio.run(_dispatch("get_log", {"ref": "log://does-not-exist"}))
        assert len(out) == 1
        assert isinstance(out[0], TextContent)
        assert "Unknown ref" in out[0].text

    def test_get_graph_unknown_ref_returns_error_text(self):
        from stata_code.mcp.server import _dispatch

        out = asyncio.run(_dispatch("get_graph", {"ref": "graph://no/0"}))
        assert len(out) == 1
        assert "Unknown ref" in out[0].text

    def test_get_matrix_unknown_ref_returns_error_text(self):
        from stata_code.mcp.server import _dispatch

        out = asyncio.run(_dispatch("get_matrix", {"ref": "matrix://no/r/M"}))
        assert len(out) == 1
        assert "Unknown ref" in out[0].text

    def test_get_matrix_known_ref_returns_payload(self):
        """Roundtrip: stash a payload via _refs and let dispatch deliver it."""
        from stata_code.core import _refs
        from stata_code.mcp.server import _dispatch

        ref = "matrix://test-fake/e/M"
        _refs.put(
            ref,
            {"rows": ["y1"], "cols": ["x1", "_cons"], "values": [[0.5, 1.0]]},
        )
        try:
            out = asyncio.run(_dispatch("get_matrix", {"ref": ref}))
        finally:
            _refs.discard(ref)
        assert len(out) == 1
        assert isinstance(out[0], TextContent)
        body = json.loads(out[0].text)
        assert body == {
            "rows": ["y1"],
            "cols": ["x1", "_cons"],
            "values": [[0.5, 1.0]],
        }

    def test_stata_run_missing_code_returns_error_json(self):
        from stata_code.mcp.server import _dispatch

        out = asyncio.run(_dispatch("stata_run", {}))
        assert len(out) == 1
        body = json.loads(out[0].text)
        assert "error" in body


# ─────────────────────────────────────────────────────────────────────────────
# End-to-end with real Stata
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.stata_required
@pytest.mark.skipif(not _real_stata, reason="Stata not available")
class TestEndToEnd:
    def test_stata_run_returns_run_result_json(self):
        from stata_code.mcp.server import _dispatch

        out = asyncio.run(_dispatch("stata_run", {"code": 'display "hello mcp"'}))
        assert len(out) == 1
        assert isinstance(out[0], TextContent)
        body = json.loads(out[0].text)
        assert body["ok"] is True
        assert body["schema_version"] == "1.0"
        assert "hello mcp" in body["log"]["head"]

    def test_stata_run_typed_error(self):
        from stata_code.mcp.server import _dispatch

        # Ensure data is loaded so the error is "variable not found" rather
        # than "no variables defined" (state-dependent across tests).
        asyncio.run(_dispatch("stata_run", {"code": "sysuse auto, clear"}))
        out = asyncio.run(_dispatch("stata_run", {"code": "summarize mpgg"}))
        body = json.loads(out[0].text)
        assert body["ok"] is False
        assert body["error"]["kind"] == "varname_not_found"
        assert body["error"]["varname"] == "mpgg"

    def test_stata_info_reports_available(self):
        from stata_code.mcp.server import _dispatch

        out = asyncio.run(_dispatch("stata_info", {}))
        body = json.loads(out[0].text)
        assert body["available"] is True
        assert body["schema_version"] == "1.0"
        assert body["backend"] == "pystata"

    def test_get_log_after_truncation(self):
        from stata_code.mcp.server import _dispatch

        run = asyncio.run(
            _dispatch(
                "stata_run",
                {
                    "code": (
                        "forvalues i = 1/50 {\n"
                        '  display "row=`i\'"\n'
                        "}"
                    ),
                    "log_lines_head": 5,
                    "log_lines_tail": 5,
                },
            )
        )
        body = json.loads(run[0].text)
        assert body["log"]["truncated"] is True
        ref = body["log"]["ref"]

        out = asyncio.run(_dispatch("get_log", {"ref": ref}))
        full = json.loads(out[0].text)
        assert full["lines_total"] >= 50
        assert "row=1" in full["text"]
        assert "row=50" in full["text"]

    def test_get_graph_returns_image_content(self):
        from stata_code.mcp.server import _dispatch

        # Clean slate
        asyncio.run(_dispatch("stata_run", {"code": "graph drop _all"}))
        asyncio.run(_dispatch("stata_run", {"code": "sysuse auto, clear"}))
        run = asyncio.run(
            _dispatch(
                "stata_run",
                {"code": "scatter price mpg, name(g_mcp)"},
            )
        )
        body = json.loads(run[0].text)
        assert len(body["graphs"]) == 1
        ref = body["graphs"][0]["ref"]

        out = asyncio.run(_dispatch("get_graph", {"ref": ref}))
        assert len(out) == 1
        assert isinstance(out[0], ImageContent)
        assert out[0].mimeType == "image/png"
        # b64 decodes to PNG header
        import base64

        raw = base64.b64decode(out[0].data)
        assert raw[:4] == b"\x89PNG"

    def test_list_and_reset_sessions(self):
        from stata_code.mcp.server import _dispatch

        # Create a session
        asyncio.run(
            _dispatch(
                "stata_run",
                {"code": "sysuse auto, clear", "session_id": "mcp_test"},
            )
        )
        out = asyncio.run(_dispatch("list_sessions", {}))
        sessions = json.loads(out[0].text)
        by_id = {s["session_id"]: s for s in sessions}
        assert "mcp_test" in by_id
        # Pool aggregator round-trips to the worker, so n_obs reflects the
        # real Stata state (auto.dta has 74 obs) rather than the legacy 0
        # placeholder.
        mcp_entry = by_id["mcp_test"]
        assert mcp_entry["n_obs"] == 74
        # Non-main session_id maps to a same-named frame.
        assert mcp_entry["frame"] == "mcp_test"

        # Reset
        out = asyncio.run(_dispatch("reset_session", {"session_id": "mcp_test"}))
        result = json.loads(out[0].text)
        assert result["dropped_frame"] is True

        out = asyncio.run(_dispatch("list_sessions", {}))
        sessions = json.loads(out[0].text)
        ids = {s["session_id"] for s in sessions}
        assert "mcp_test" not in ids
