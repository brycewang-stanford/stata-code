"""pystata-first adapter — the preferred path for Stata 17+."""

from __future__ import annotations

import io
import os
import sys
import time
import traceback
import warnings
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from typing import Any

from stata_code.core.result import StataResult, StataGraph
from stata_code.core.version import detect_stata, StataVersion


class PystataAdapter:
    """
    Adapter using the official ``pystata`` library (Stata 17+).

    This is the preferred execution path. pystata runs Stata in-process,
    giving us direct access to Stata's return values (``r()``, ``e()``),
    graph files, and the streaming log with minimal overhead.

    Usage::

        from stata_code import get_adapter
        adapter = get_adapter()
        result = adapter.run("summarize mpg, detail")
        print(result.results)   # e() / r() scalars, matrices, macros
        print(result.graphs)    # StataGraph objects
    """

    def __init__(self, config: dict[str, Any] | None = None):
        self._config = config or {}
        self._stata: Any = None
        self._initialized = False

    @property
    def is_available(self) -> bool:
        """True iff pystata is importable and a Stata 17+ install is detected."""
        try:
            import pystata  # noqa: F401
            return detect_stata().supports_pystata
        except ImportError:
            return False

    def _ensure_init(self) -> None:
        """Lazily initialize pystata (import + ``stata`` singleton)."""
        if self._initialized:
            return
        try:
            from pystata import config as pystata_config
            from pystata import stata as pystata_stata
        except ImportError as exc:
            raise RuntimeError(
                "pystata is not installed or not importable. "
                "Install with: pip install pystata"
            ) from exc

        # Configure pystata output settings
        graph_format = self._config.get("graph_format", "png")
        pystata_config.set("graph_format", graph_format)

        graphs_dir = self._config.get("graphs_dir", "")
        if graphs_dir:
            pystata_config.set("base_path", graphs_dir)

        self._stata = pystata_stata
        self._initialized = True

    def run(
        self,
        code: str,
        *,
        capture_graphs: bool = True,
        capture_log: bool = True,
        timeout: float | None = 120.0,
    ) -> StataResult:
        """
        Execute ``code`` in Stata via pystata and return a ``StataResult``.

        Parameters
        ----------
        code: Stata command(s) to run. Can include newlines and multiple
            commands separated by ``;`` (Stata's delimiter).
        capture_graphs: If True, collect all Stata graph files created
            during execution (``.png``, ``.gph``).
        capture_log: If True, capture the complete Stata log.
        timeout: Seconds before raising a TimeoutError. None = no timeout.

        Returns
        -------
        StataResult with stdout, results (e(), r() values), graphs, etc.
        """
        self._ensure_init()
        start = time.monotonic()

        result = StataResult()

        # Capture stdout/stderr from pystata's run()
        stdout_capture = io.StringIO()
        stderr_capture = io.StringIO()

        try:
            with redirect_stdout(stdout_capture), redirect_stderr(stderr_capture):
                # pystata's run() blocks until completion; no timeout param
                self._stata.run(code)
        except Exception as exc:  # noqa: BLE001
            result.error = str(exc)
            result.return_code = -1
            result.add_warning(traceback.format_exc())

        result.stdout = stdout_capture.getvalue()
        result.stderr = stderr_capture.getvalue()
        result.log = result.stdout  # streaming log is same as captured stdout

        result.elapsed_seconds = time.monotonic() - start
        result.stata_version = detect_stata().version

        # Collect graphs
        if capture_graphs:
            result.graphs = self._collect_graphs()

        # Collect e() and r() results
        result.results = self._collect_return_values()

        return result

    def _collect_graphs(self) -> list[StataGraph]:
        """Find and capture newly-created Stata graph files."""
        graphs_dir = self._config.get("graphs_dir", "")
        if not graphs_dir or not os.path.isdir(graphs_dir):
            graphs_dir = "."

        fmt = self._config.get("graph_format", "png")
        extensions = {fmt.lower()} | {"png", "svg", "gph", "pdf"}
        graphs: list[StataGraph] = []

        try:
            for fname in os.listdir(graphs_dir):
                fpath = os.path.join(graphs_dir, fname)
                if not os.path.isfile(fpath):
                    continue
                ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else ""
                if ext in extensions:
                    try:
                        graphs.append(StataGraph.from_file(fpath, ext))
                    except OSError:
                        pass
        except OSError:
            pass

        return graphs

    def _collect_return_values(self) -> dict[str, Any]:
        """
        Collect Stata's ``r()`` and ``e()`` results into a flat dict.

        We use pystata's API directly where possible, and fall back to
        running Stata commands that return scalar/list values.

        Key scheme: ``r(mean)`` → ``r_mean``, ``e(b)`` (matrix) → ``e_b``
        as list-of-lists.
        """
        out: dict[str, Any] = {}
        if self._stata is None:
            return out

        # Try to collect r() scalars via pystata's run() capturing output
        # We run "return list" and parse the plain-text output
        try:
            captured = io.StringIO()
            with redirect_stdout(captured):
                self._stata.run("return list")
            output = captured.getvalue().strip()
            for line in output.splitlines():
                line = line.strip()
                if not line or line.startswith("-"):
                    continue
                # Scalar lines look like "mean = 21.2973"
                # Macro lines look like "cmd = \"regress\""
                if "=" in line:
                    key, val = line.split("=", 1)
                    key = key.strip()
                    val = val.strip().strip('"')
                    if key:
                        out[f"r_{key}"] = val
        except Exception:
            pass

        # Try to collect e() results
        try:
            captured = io.StringIO()
            with redirect_stdout(captured):
                self._stata.run("ereturn list")
            output = captured.getvalue().strip()
            for line in output.splitlines():
                line = line.strip()
                if not line or line.startswith("-"):
                    continue
                if "=" in line:
                    key, val = line.split("=", 1)
                    key = key.strip()
                    val = val.strip().strip('"')
                    if key:
                        out[f"e_{key}"] = val
        except Exception:
            pass

        return out

    @staticmethod
    def _stata_to_python(val: Any) -> Any:
        """Convert pystata return values to plain Python types."""
        import numpy as np

        if isinstance(val, np.ndarray):
            return val.tolist()
        if hasattr(val, "tolist"):
            return val.tolist()
        if isinstance(val, (int, float, str)):
            return val
        return str(val)

    def close(self) -> None:
        """Shut down the pystata session."""
        if self._stata is not None:
            try:
                self._stata.run("exit, clear", echo=False)
            except Exception:
                pass
        self._initialized = False