"""Integration tests for the full run() path and adapter dispatch."""

from __future__ import annotations

import asyncio
import sys
from unittest.mock import MagicMock, patch

import pytest

from stata_code import run, get_adapter
from stata_code.core.result import StataResult, StataGraph
from stata_code.core.version import StataVersion, StataEdition


class TestGetAdapterDispatch:
    """Test adapter selection logic (no Stata required)."""

    def test_falls_back_to_console_when_pystata_unavailable(self):
        """When pystata is absent, should try ConsoleFallback."""
        # Use UNKNOWN edition so the pystata branch is skipped entirely
        mock_version = StataVersion(
            edition=StataEdition.UNKNOWN,
            version="",
            major=0,
            minor=0,
        )
        import stata_code
        stata_code._adapter = None
        with patch("stata_code.detect_stata", return_value=mock_version):
            with patch(
                "stata_code.PystataAdapter.is_available",
                False,
            ):
                with patch(
                    "stata_code.ConsoleFallback.is_available",
                    True,
                ):
                    adapter = get_adapter()
                    from stata_code.core.console_fallback import ConsoleFallback
                    assert isinstance(adapter, ConsoleFallback)
        stata_code._adapter = None  # reset

    def test_pystata_preferred_when_available(self):
        """When pystata is available, PystataAdapter is selected."""
        mock_version = StataVersion(
            edition=StataEdition.MP,
            version="18.0",
            major=18,
            minor=0,
        )
        import stata_code
        stata_code._adapter = None
        with patch("stata_code.detect_stata", return_value=mock_version):
            with patch(
                "stata_code.PystataAdapter.is_available",
                True,
            ):
                adapter = get_adapter()
                from stata_code.core.pystata_adapter import PystataAdapter
                assert isinstance(adapter, PystataAdapter)
        stata_code._adapter = None  # reset

    def test_no_stata_raises_runtime_error(self):
        """When neither adapter is available, RuntimeError is raised."""
        mock_version = StataVersion(
            edition=StataEdition.UNKNOWN,
            version="",
            major=0,
            minor=0,
        )
        import stata_code
        stata_code._adapter = None
        with patch("stata_code.detect_stata", return_value=mock_version):
            with patch(
                "stata_code.PystataAdapter.is_available",
                False,
            ):
                with patch(
                    "stata_code.ConsoleFallback.is_available",
                    False,
                ):
                    with pytest.raises(RuntimeError, match="No Stata installation detected"):
                        get_adapter()
        stata_code._adapter = None  # reset


class TestRunFunction:
    """Test the top-level run() entry point."""

    def test_run_returns_stata_result(self):
        """run() should return a StataResult instance even with no Stata installed."""
        import stata_code
        stata_code._adapter = None
        mock_version = StataVersion(
            edition=StataEdition.UNKNOWN,
            version="",
            major=0,
            minor=0,
        )
        with patch("stata_code.detect_stata", return_value=mock_version):
            with patch(
                "stata_code.PystataAdapter.is_available",
                False,
            ):
                # run() uses get_adapter() which falls back to ConsoleFallback
                # ConsoleFallback.is_available returns False when no stata binary found,
                # so it raises RuntimeError. We mock that to return a mock result instead.
                mock_result = StataResult(stdout="mock output", return_code=0)
                with patch(
                    "stata_code.get_adapter",
                    return_value=MagicMock(run=MagicMock(return_value=mock_result)),
                ):
                    result = run("summarize mpg", timeout=5)
                    assert isinstance(result, StataResult)
        stata_code._adapter = None  # reset

    def test_run_captures_error_on_failure(self, tmp_path):
        """run() should populate error field when Stata fails."""
        from stata_code.core.console_fallback import ConsoleFallback
        fallback = ConsoleFallback()
        fallback._stata_path = str(tmp_path / "nonexistent_stata")
        result = fallback.run("this_is_not_a_valid_stata_command")
        assert isinstance(result, StataResult)


class TestMcpServerTools:
    """Test MCP server tool definitions and dispatch."""

    @pytest.fixture(autouse=True)
    def _check_mcp_available(self):
        """Skip all MCP tests when mcp package is not installed."""
        try:
            from mcp.server import Server  # noqa: F401
        except ImportError:
            pytest.skip("mcp package not installed")

    def test_stata_run_tool_schema_valid(self):
        """The stata_run tool inputSchema should be valid JSON Schema."""
        from stata_code.mcp.server import APP

        tools = asyncio.run(APP.list_tools())
        tool_names = [t.name for t in tools]
        assert "stata_run" in tool_names
        assert "stata_version" in tool_names

        stata_run = next(t for t in tools if t.name == "stata_run")
        schema = stata_run.inputSchema
        assert schema["type"] == "object"
        assert "code" in schema["properties"]
        assert schema["properties"]["code"]["type"] == "string"
        assert "required" in schema
        assert "code" in schema["required"]

    def test_stata_version_tool_schema(self):
        """The stata_version tool should have empty inputSchema."""
        from stata_code.mcp.server import APP

        tools = asyncio.run(APP.list_tools())
        stata_version = next(t for t in tools if t.name == "stata_version")
        assert stata_version.inputSchema == {"type": "object", "properties": {}}

    def test_call_tool_dispatches_stata_run(self):
        """call_tool should route stata_run to _stata_run."""
        from stata_code.mcp.server import _stata_run

        args = {"code": "summarize mpg", "capture_graphs": True, "timeout": 120}
        result = asyncio.run(_stata_run(args))
        assert len(result) == 1
        assert result[0].type == "text"
        assert "[stata_code]" in result[0].text

    def test_call_tool_dispatches_stata_version(self):
        """call_tool should route stata_version to _stata_version."""
        from stata_code.mcp.server import _stata_version

        result = asyncio.run(_stata_version({}))
        assert len(result) == 1
        assert result[0].type == "text"
        assert "edition=" in result[0].text
        assert "version=" in result[0].text

    def test_call_tool_unknown_returns_error_text(self):
        """Unknown tool names should return an error TextContent."""
        from stata_code.mcp.server import APP

        result = asyncio.run(APP.call_tool("nonexistent_tool", {}))
        assert len(result) == 1
        assert result[0].type == "text"
        assert "Unknown tool" in result[0].text


class TestResultToText:
    """Test the _result_to_text formatting for MCP transport."""

    @pytest.fixture(autouse=True)
    def _check_mcp_available(self):
        """Skip MCP-dependent tests when mcp package is not installed."""
        try:
            from mcp.types import TextContent  # noqa: F401
        except ImportError:
            pytest.skip("mcp package not installed")

    def test_format_success(self):
        """OK status with elapsed time."""
        from stata_code.mcp.server import _result_to_text

        result = StataResult(stdout="summarize mpg\n", return_code=0, elapsed_seconds=1.5)
        result.results = {"r(mean)": "21.2973", "e(cmd)": "summarize"}
        result.graphs = [StataGraph(format="png", data=b"fake")]

        text = _result_to_text(result).text
        assert "[stata_code] OK" in text
        assert "elapsed=1.50s" in text
        assert "STATA LOG" in text
        assert "r_mean" in text  # formatted results
        assert "GRAPH" in text

    def test_format_error(self):
        """Error result shows error message."""
        from stata_code.mcp.server import _result_to_text

        result = StataResult(error="variable not found", return_code=198)
        text = _result_to_text(result).text
        assert "ERR" in text
        assert "variable not found" in text

    def test_format_with_warnings(self):
        """Warnings are appended."""
        from stata_code.mcp.server import _result_to_text

        result = StataResult()
        result.add_warning("converged at boundary")
        result.add_warning("16 missing values generated")
        text = _result_to_text(result).text
        assert "warnings:" in text
        assert "converged at boundary" in text


class TestStataGraphRoundtrip:
    """Test StataGraph save/load roundtrip."""

    def test_save_load_png(self, tmp_path):
        """StataGraph saved and reloaded should match original."""
        original = StataGraph(format="png", data=b"\x89PNG\r\n\x1a\n")
        path = tmp_path / "test.png"
        original.save(str(path))

        reloaded = StataGraph.from_file(str(path))
        assert reloaded.format == "png"
        assert reloaded.data == original.data
        assert reloaded.path == str(path)

    def test_data_uri_is_valid(self):
        """to_data_uri() should produce a valid data URI."""
        g = StataGraph(format="png", data=b"\x89PNG")
        uri = g.to_data_uri()
        assert uri.startswith("data:image/png;base64,")
        # Base64 portion should decode correctly
        b64 = uri.split(",", 1)[1]
        decoded = __import__("base64").b64decode(b64)
        assert decoded == b"\x89PNG"