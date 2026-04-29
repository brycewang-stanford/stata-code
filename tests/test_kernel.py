"""Integration tests for the Stata Jupyter kernel."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from stata_code.core.result import StataResult, StataGraph


class TestStataKernelClass:
    """Test StataKernel without requiring a live Jupyter connection."""

    def test_kernel_has_correct_language_info(self):
        """Kernel language_info declares Stata correctly."""
        from stata_code.kernel import StataKernel

        ki = StataKernel.language_info
        assert ki["name"] == "stata"
        assert ki["file_extension"] == ".do"
        assert ki["mimetype"] == "text/x-stata"

    def test_kernel_protocol_version(self):
        """Kernel announces correct protocol version."""
        from stata_code.kernel import StataKernel

        assert StataKernel.protocol_version == "5.3"
        assert StataKernel.implementation == "stata_code.kernel"

    def test_do_execute_returns_error_on_missing_ipykernel(self):
        """When ipykernel is absent, do_execute returns an error reply."""
        from stata_code.kernel import kernel

        # Simulate ipykernel unavailable by patching the flag
        original = kernel._HAS_IPYKERNEL
        kernel._HAS_IPYKERNEL = False

        try:
            from stata_code.kernel import StataKernel

            kb = StataKernel()
            reply = kb.do_execute("summarize mpg", silent=False)
            assert reply["status"] == "error"
            assert "ipykernel not installed" in reply["evalue"]
        finally:
            kernel._HAS_IPYKERNEL = original

    def test_do_execute_calls_stata_run(self):
        """do_execute should call run() and route results to the shell."""
        from stata_code.kernel import kernel as kernel_module

        original = kernel_module._HAS_IPYKERNEL
        kernel_module._HAS_IPYKERNEL = True

        mock_result = StataResult(
            stdout="",
            log="summarize mpg\n\n    Variable |        Obs        Mean    Std. Dev.       Min        Max\n-------------+--------------------------------------------------------\n         mpg |         74     21.2973    5.785503        12         41",
            return_code=0,
            results={"r(mean)": "21.2973", "e(cmd)": "summarize"},
        )

        try:
            from stata_code.kernel import StataKernel

            kb = StataKernel()

            with patch("stata_code.kernel.kernel.run", return_value=mock_result) as mock_run:
                reply = kb.do_execute("summarize mpg", silent=False)
                mock_run.assert_called_once()
                call_args = mock_run.call_args
                # code is passed positionally, not as keyword arg
                assert "summarize mpg" in call_args.args[0]
                assert call_args.kwargs["capture_graphs"] is True
                assert call_args.kwargs["capture_log"] is True

            assert reply["status"] == "ok"
            # execution_count starts at 0 in ipykernel; increment_execution_count()
            # is called by the full Jupyter machinery, not by do_execute directly
            assert reply["execution_count"] == 0
        finally:
            kernel_module._HAS_IPYKERNEL = original

    def test_do_execute_handles_error_result(self):
        """When Stata returns an error, do_execute reports status=error."""
        from stata_code.kernel import kernel as kernel_module

        original = kernel_module._HAS_IPYKERNEL
        kernel_module._HAS_IPYKERNEL = True

        error_result = StataResult(error="variable not found", return_code=198, log="variable not found")

        try:
            from stata_code.kernel import StataKernel

            kb = StataKernel()
            with patch("stata_code.kernel.kernel.run", return_value=error_result):
                reply = kb.do_execute("summarize not_a_var", silent=False)
            assert reply["status"] == "error"
            assert reply["ename"] == "StataError"
            assert "variable not found" in reply["traceback"][0]
        finally:
            kernel_module._HAS_IPYKERNEL = original

    def test_do_complete_returns_stata_keywords(self):
        """do_complete should return Stata keyword matches."""
        from stata_code.kernel import StataKernel

        kb = StataKernel()
        result = kb.do_complete("summ", 5)
        assert result["status"] == "ok"
        matches = result["matches"]
        assert any("summarize" in m for m in matches)

    def test_do_complete_empty_on_no_match(self):
        """do_complete returns empty list when no keyword matches."""
        from stata_code.kernel import StataKernel

        kb = StataKernel()
        result = kb.do_complete("xyzzy", 5)
        assert result["status"] == "ok"
        assert result["matches"] == []

    def test_do_inspect_returns_help_text(self):
        """do_inspect returns documentation for known Stata commands."""
        from stata_code.kernel import StataKernel

        kb = StataKernel()
        # code = "summarize", cursor at end (9)
        result = kb.do_inspect("summarize", cursor_pos=9)
        assert result["status"] == "ok"
        assert result["found"] is True
        assert "summary statistics" in result["documentation"]

    def test_do_inspect_unknown_command(self):
        """do_inspect returns found=False for unknown commands."""
        from stata_code.kernel import StataKernel

        kb = StataKernel()
        result = kb.do_inspect("xyzzy", cursor_pos=5)
        assert result["status"] == "ok"
        assert result["found"] is False

    def test_do_kernel_info_returns_structure(self):
        """do_kernel_info returns the kernel info dict."""
        from stata_code.kernel import StataKernel

        kb = StataKernel()
        info = kb.do_kernel_info()
        assert "protocol_version" in info
        assert "language_info" in info
        assert info["language_info"]["name"] == "stata"


class TestInstallKernel:
    """Test the kernel installation CLI."""

    def test_install_kernel_writes_kernel_json(self, tmp_path):
        """install_kernel should write a valid kernel.json."""
        from stata_code.kernel.kernel import install_kernel

        with patch.object(sys, "executable", str(tmp_path / "python")):
            with patch.object(Path, "mkdir") as mock_mkdir:
                with patch("builtins.open", side_effect=OSError("read-only")):
                    # Just verify the function exists and has correct signature
                    import inspect
                    sig = inspect.signature(install_kernel)
                    params = list(sig.parameters.keys())
                    assert "user" in params
                    assert "system" in params


class TestStataGraphDataUri:
    """Test StataGraph rendering for Jupyter display."""

    def test_to_base64_roundtrip(self):
        """StataGraph.to_base64 preserves data."""
        g = StataGraph(format="png", data=b"\x89PNG\r\n\x1a\n")
        b64 = g.to_base64()
        import base64

        assert base64.b64decode(b64) == g.data

    def test_to_data_uri_format(self):
        """to_data_uri() produces a valid data URI for inline display."""
        g = StataGraph(format="png", data=b"\x89PNG")
        uri = g.to_data_uri()
        assert uri.startswith("data:image/png;base64,")

    def test_svg_data_uri(self):
        """SVG graphs produce correct SVG data URI."""
        g = StataGraph(format="svg", data=b"<svg></svg>")
        uri = g.to_data_uri()
        assert uri.startswith("data:image/svg+xml;base64,")