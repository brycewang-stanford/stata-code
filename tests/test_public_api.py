"""Tests for the package-level public API."""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest

from stata_code.core._runtime import is_available

pytestmark = [
    pytest.mark.stata_required,
    pytest.mark.skipif(
        not is_available(), reason="pystata / Stata 17+ not available"
    ),
]


def test_package_run_does_not_steal_process_stdout() -> None:
    script = textwrap.dedent(
        """
        from stata_code import run, shutdown_default_pool

        result = run("display 2 + 2", include_full_log=True, include_graphs="none")
        print(f"ok={result.ok} log={result.log.head!r}")
        shutdown_default_pool()
        """
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        text=True,
        capture_output=True,
    )
    assert "ok=True log='4'" in completed.stdout


def test_package_run_enforces_timeout() -> None:
    from stata_code import run, shutdown_default_pool
    from stata_code.core.schema import ErrorKind

    try:
        result = run("sleep 30000", session_id="public_timeout", timeout_ms=1500)
        assert result.ok is False
        assert result.rc == -2
        assert result.error is not None
        assert result.error.kind is ErrorKind.TIMEOUT
    finally:
        shutdown_default_pool()
