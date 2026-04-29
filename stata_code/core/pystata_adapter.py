"""pystata-first adapter — the preferred path for Stata 17+."""

from __future__ import annotations

import os
import time
import traceback
import warnings
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

        adapter = PystataAdapter()
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
            from pystata import config, stata
        except ImportError as exc:
            raise RuntimeError(
                "pystata is not installed or not importable. "
                "Install with: pip install pystata"
            ) from exc

        # Configure pystata output settings
        config.set(
            "graph_format", self._config.get("graph_format", "png")
        )
        config.set(
            "base_path", self._config.get("graphs_dir", "")
        )

        self._stata = stata
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
            during execution (`.png`, `.gph`).
        capture_log: If True, capture the complete Stata log.
        timeout: Seconds before raising a TimeoutError. None = no timeout.

        Returns
        -------
        StataResult with stdout, results (e(), r() values), graphs, etc.
        """
        self._ensure_init()
        start = time.monotonic()

        result = StataResult()

        try:
            # Run the Stata code; pystata's ``run`` returns after execution
            self._stata.run(code, timeout=timeout)
        except Exception as exc:  # noqa: BLE001
            result.error = str(exc)
            result.return_code = -1
            result.add_warning(traceback.format_exc())

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
            # pystata dumps graphs in the current directory by default
            graphs_dir = "."

        fmt = self._config.get("graph_format", "png")
        extensions = {fmt} | {"png", "svg", "gph", "pdf"}
        graphs: list[StataGraph] = []

        # Look for recently modified graph files
        try:
            for fname in os.listdir(graphs_dir):
                fpath = os.path.join(graphs_dir, fname)
                if not os.path.isfile(fpath):
                    continue
                ext = fname.rsplit(".", 1)[-1] if "." in fname else ""
                if ext.lower() in extensions:
                    graphs.append(StataGraph.from_file(fpath, ext.lower()))
        except OSError:
            pass

        return graphs

    def _collect_return_values(self) -> dict[str, Any]:
        """
        Collect Stata's ``r()`` (returned scalars/macros/matrices) and
        ``e()`` (estimation results) into a flat dict.

        Flattened key scheme: ``r(mean)`` → ``r_mean``,
        ``e(b)`` (matrix) → ``e_b`` (list-of-lists).
        """
        out: dict[str, Any] = {}
        if self._stata is None:
            return out

        try:
            r = self._stata.run("return list", inline=True, echo=False)
            # r is a dict-like StataVector; convert to plain Python types
            for key, val in r.items():
                out[f"r_{key}"] = self._stata_to_python(val)
        except Exception:
            pass

        try:
            e = self._stata.run("ereturn list", inline=True, echo=False)
            for key, val in e.items():
                out[f"e_{key}"] = self._stata_to_python(val)
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
                self._stata.run("exit, clear", inline=True, echo=False)
            except Exception:
                pass
        self._initialized = False
