"""Tests for the Stata Jupyter kernel (rewired to v1.0 runner pipeline)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

from stata_code.core.schema import (
    Backend,
    DatasetInfo,
    ErrorContext,
    ErrorInfo,
    ErrorKind,
    LogInfo,
    RunResult,
    StataEdition,
    StataInfo,
    Suggestion,
    VariableInfo,
)


def _make_run_result(*, ok: bool = True, **overrides) -> RunResult:
    base: dict = {
        "ok": ok,
        "rc": 0 if ok else 111,
        "session_id": "main",
        "request_id": "test-req",
        "started_at": "2026-04-30T00:00:00.000Z",
        "elapsed_ms": 1,
        "stata": StataInfo(
            version="18.0", edition=StataEdition.MP, backend=Backend.PYSTATA
        ),
    }
    base.update(overrides)
    if not ok and base.get("error") is None:
        base["error"] = ErrorInfo(
            kind=ErrorKind.VARNAME_NOT_FOUND,
            rc=base["rc"],
            message="variable mpgg not found",
            varname="mpgg",
            line=1,
            context=ErrorContext(before=[], failing="summarize mpgg", after=[]),
            suggestions=[Suggestion(action="Did you mean `mpg`?", command="describe")],
        )
    return RunResult(**base)


class TestStataKernelClass:
    """Test StataKernel without requiring a live Jupyter connection."""

    def test_kernel_has_correct_language_info(self):
        from stata_code.kernel import StataKernel

        ki = StataKernel.language_info
        assert ki["name"] == "stata"
        assert ki["file_extension"] == ".do"
        assert ki["mimetype"] == "text/x-stata"

    def test_kernel_protocol_version(self):
        from stata_code.kernel import StataKernel

        assert StataKernel.protocol_version == "5.3"
        assert StataKernel.implementation == "stata_code.kernel"

    def test_do_execute_returns_error_on_missing_ipykernel(self):
        from stata_code.kernel import kernel

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

    def test_do_execute_calls_runner_and_routes_ok(self):
        """do_execute uses runner.execute and reports status=ok on success."""
        from stata_code.kernel import kernel as kernel_module

        original = kernel_module._HAS_IPYKERNEL
        kernel_module._HAS_IPYKERNEL = True
        mock_result = _make_run_result(
            ok=True,
            log=LogInfo(head="summarize mpg\n", tail="", lines_total=1, bytes_total=15),
        )
        try:
            from stata_code.kernel import StataKernel

            kb = StataKernel()
            with patch(
                "stata_code.kernel.kernel.execute", return_value=mock_result
            ) as mock_exec:
                reply = kb.do_execute("summarize mpg", silent=True)
            mock_exec.assert_called_once()
            # code is positional; defaults are tuned for Jupyter
            assert "summarize mpg" in mock_exec.call_args.args[0]
            assert mock_exec.call_args.kwargs["include_full_log"] is True
            assert mock_exec.call_args.kwargs["include_graphs"] == "inline"
            assert reply["status"] == "ok"
        finally:
            kernel_module._HAS_IPYKERNEL = original

    def test_do_execute_handles_error_result(self):
        """When runner reports an error, do_execute returns status=error with the typed kind."""
        from stata_code.kernel import kernel as kernel_module

        original = kernel_module._HAS_IPYKERNEL
        kernel_module._HAS_IPYKERNEL = True
        mock_result = _make_run_result(ok=False)
        try:
            from stata_code.kernel import StataKernel

            kb = StataKernel()
            with patch(
                "stata_code.kernel.kernel.execute", return_value=mock_result
            ):
                reply = kb.do_execute("summarize mpgg", silent=True)
            assert reply["status"] == "error"
            assert "varname_not_found" in reply["ename"]
            assert "mpgg" in reply["evalue"]
            # Suggestion surfaces in the traceback
            assert any("Did you mean" in line for line in reply["traceback"])
        finally:
            kernel_module._HAS_IPYKERNEL = original

    def test_do_execute_suppresses_pure_command_echo(self):
        """A cell with no textual output (e.g. a graph) must not stream the
        echoed source back — that read as a useless repeat of the code."""
        from stata_code.kernel import kernel as kernel_module

        original = kernel_module._HAS_IPYKERNEL
        kernel_module._HAS_IPYKERNEL = True
        echo_only = LogInfo(
            head='\n. * 3) fit\n. twoway (scatter price mpg) (lfit price mpg)\n\n. \n',
            tail="",
            lines_total=5,
            bytes_total=60,
        )
        mock_result = _make_run_result(ok=True, log=echo_only)
        try:
            from stata_code.kernel import StataKernel

            kb = StataKernel()
            streamed: list[tuple[str, str]] = []
            with patch.object(
                kb, "_stream", side_effect=lambda n, t: streamed.append((n, t))
            ):
                with patch(
                    "stata_code.kernel.kernel.execute", return_value=mock_result
                ):
                    reply = kb.do_execute(
                        "* 3) fit\ntwoway (scatter price mpg) (lfit price mpg)",
                        silent=False,
                    )
            assert reply["status"] == "ok"
            assert not any(name == "stdout" for name, _ in streamed)
        finally:
            kernel_module._HAS_IPYKERNEL = original

    def test_do_execute_streams_output_without_echo(self):
        """Genuine command output is streamed, but the leading `. cmd` echo is
        stripped from it."""
        from stata_code.kernel import kernel as kernel_module

        original = kernel_module._HAS_IPYKERNEL
        kernel_module._HAS_IPYKERNEL = True
        mixed = LogInfo(
            head="\n. summarize price\n\n    price |   74\n\n. \n",
            tail="",
            lines_total=6,
            bytes_total=40,
        )
        mock_result = _make_run_result(ok=True, log=mixed)
        try:
            from stata_code.kernel import StataKernel

            kb = StataKernel()
            streamed: list[tuple[str, str]] = []
            with patch.object(
                kb, "_stream", side_effect=lambda n, t: streamed.append((n, t))
            ):
                with patch(
                    "stata_code.kernel.kernel.execute", return_value=mock_result
                ):
                    kb.do_execute("summarize price", silent=False)
            stdout = [t for n, t in streamed if n == "stdout"]
            assert len(stdout) == 1
            assert ". summarize price" not in stdout[0]
            assert "price |   74" in stdout[0]
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
        assert all(not m.startswith(" ") for m in matches)

    def test_do_complete_includes_last_result_variables(self):
        """Completion should surface variables from the last run's dataset."""
        from stata_code.kernel import StataKernel

        kb = StataKernel()
        kb._last_result = _make_run_result(
            dataset=DatasetInfo(
                n_obs=10,
                n_vars=2,
                variables=[
                    VariableInfo(name="mpg", type="int", label="Mileage"),
                    VariableInfo(name="make", type="str18", label="Make"),
                ],
            )
        )
        result = kb.do_complete("mp", 2)
        assert result["status"] == "ok"
        assert "mpg" in result["matches"]

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

    def test_do_inspect_returns_variable_metadata(self):
        """do_inspect should prefer last-result variable metadata."""
        from stata_code.kernel import StataKernel

        kb = StataKernel()
        kb._last_result = _make_run_result(
            dataset=DatasetInfo(
                n_obs=10,
                n_vars=1,
                variables=[VariableInfo(name="mpg", type="int", label="Mileage")],
            )
        )
        result = kb.do_inspect("summarize mpg", cursor_pos=13)
        assert result["status"] == "ok"
        assert result["found"] is True
        assert "Variable `mpg`" in result["documentation"]
        assert "Mileage" in result["documentation"]

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

    def test_install_kernel_writes_user_kernel_spec(self, tmp_path):
        """install_kernel should generate a valid user kernelspec."""
        from stata_code.kernel.kernel import install_kernel

        installed: list[dict] = []

        class DummyKernelSpecManager:
            def install_kernel_spec(
                self,
                source_dir: str,
                *,
                kernel_name: str,
                user: bool,
                replace: bool,
            ) -> str:
                spec = json.loads((Path(source_dir) / "kernel.json").read_text())
                installed.append(
                    {
                        "kernel_name": kernel_name,
                        "user": user,
                        "replace": replace,
                        "spec": spec,
                    }
                )
                return str(tmp_path / "kernels" / kernel_name)

        with patch.object(sys, "executable", str(tmp_path / "python")):
            with patch(
                "jupyter_client.kernelspec.KernelSpecManager",
                return_value=DummyKernelSpecManager(),
            ):
                install_kernel(user=True)

        assert installed == [
            {
                "kernel_name": "stata",
                "user": True,
                "replace": True,
                "spec": {
                    "argv": [
                        str(tmp_path / "python"),
                        "-m",
                        "stata_code.kernel",
                        "-f",
                        "{connection_file}",
                    ],
                    "display_name": "Stata",
                    "language": "stata",
                    "metadata": {"debugger": False},
                },
            }
        ]

    def test_install_kernel_copies_bundled_logos(self, tmp_path):
        """install_kernel should copy bundled logo assets into the kernelspec."""
        from stata_code.kernel.kernel import ASSETS_DIR, install_kernel

        captured_files: dict[str, bytes] = {}

        class DummyKernelSpecManager:
            def install_kernel_spec(
                self,
                source_dir: str,
                *,
                kernel_name: str,
                user: bool,
                replace: bool,
            ) -> str:
                for child in Path(source_dir).iterdir():
                    if child.is_file():
                        captured_files[child.name] = child.read_bytes()
                return str(tmp_path / "kernels" / kernel_name)

        with patch(
            "jupyter_client.kernelspec.KernelSpecManager",
            return_value=DummyKernelSpecManager(),
        ):
            install_kernel(user=True)

        for asset in ASSETS_DIR.iterdir():
            if asset.is_file():
                assert asset.name in captured_files, f"missing {asset.name}"
                assert captured_files[asset.name] == asset.read_bytes()

    def test_install_kernel_system_not_user(self, tmp_path):
        """system=True should pass user=False to Jupyter's installer."""
        from stata_code.kernel.kernel import install_kernel

        flags: list[bool] = []

        class DummyKernelSpecManager:
            def install_kernel_spec(
                self,
                source_dir: str,
                *,
                kernel_name: str,
                user: bool,
                replace: bool,
            ) -> str:
                flags.append(user)
                return str(tmp_path / "kernels" / kernel_name)

        with patch(
            "jupyter_client.kernelspec.KernelSpecManager",
            return_value=DummyKernelSpecManager(),
        ):
            install_kernel(system=True)

        assert flags == [False]


class TestCommandEchoStripping:
    """Unit tests for `_strip_command_echo` (pure, no Stata required)."""

    def test_pure_echo_graph_cell_becomes_empty(self):
        from stata_code.kernel.kernel import _strip_command_echo

        log = "\n. * 3) fit\n. twoway (scatter price mpg) (lfit price mpg)\n\n. \n"
        assert _strip_command_echo(log) == ""

    def test_wrapped_continuation_lines_stripped(self):
        from stata_code.kernel.kernel import _strip_command_echo

        # Stata wraps a long command onto a `> ` continuation line.
        log = (
            '\n. twoway scatter price mpg, title("A long title that wraps\n'
            '> onto another line")\n\n. \n'
        )
        assert _strip_command_echo(log) == ""

    def test_real_output_preserved_echo_removed(self):
        from stata_code.kernel.kernel import _strip_command_echo

        log = "\n. summarize price\n\n    price |   74\n\n. \n"
        out = _strip_command_echo(log)
        assert ". summarize" not in out
        assert out == "    price |   74"

    def test_consecutive_blank_lines_collapsed(self):
        from stata_code.kernel.kernel import _strip_command_echo

        assert _strip_command_echo("line1\n\n\n\nline2") == "line1\n\nline2"

    def test_log_without_echo_unchanged(self):
        from stata_code.kernel.kernel import _strip_command_echo

        log = "    Variable |  Obs\n       price |   74"
        assert _strip_command_echo(log) == log

    def test_missing_value_dot_output_not_stripped(self):
        from stata_code.kernel.kernel import _strip_command_echo

        # A bare "." (e.g. `display .`) is real output, not an echoed prompt
        # (which is always ". " — dot-space). It must survive.
        assert _strip_command_echo(".") == "."


# NOTE: TestStataGraphDataUri was removed in v0.2. The legacy `StataGraph`
# dataclass (with .to_base64() / .to_data_uri()) is gone; the v1.0 `GraphInfo`
# schema returns refs by default and inline base64 only when explicitly
# requested. See tests/test_runner.py::TestGraphCapture for the new behavior.
