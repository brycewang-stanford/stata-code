"""Unified Stata bridge — single entry point for all frontends.

Usage::

    from stata_code import run

    result = run("summarize mpg, detail")
    print(result.stdout)      # Stata log output
    print(result.results)     # r() and e() values as flat dict
    print(result.graphs)      # StataGraph objects

The module auto-selects the best available backend:
- ``pystata`` (Stata 17+) — preferred, in-process, full return-value access
- console fallback (Stata 11+) — subprocess, works on older Stata
"""

from __future__ import annotations

from typing import Any

from stata_code.core.result import StataResult, StataGraph
from stata_code.core.version import detect_stata
from stata_code.core.pystata_adapter import PystataAdapter
from stata_code.core.console_fallback import ConsoleFallback

__all__ = ["run", "run_code", "get_adapter", "StataResult", "StataGraph"]
__version__ = "0.1.0"

# ─────────────────────────────────────────────────────────────────────────────
# Module-level singleton for the auto-selected adapter
# ─────────────────────────────────────────────────────────────────────────────

_adapter: PystataAdapter | ConsoleFallback | None = None


def get_adapter() -> PystataAdapter | ConsoleFallback:
    """
    Lazily instantiates and returns the best available Stata adapter.

    Priority:
    1. PystataAdapter — if pystata is importable and Stata 17+ detected
    2. ConsoleFallback — if a Stata binary is found on the system

    Returns
    -------
    PystataAdapter or ConsoleFallback

    Raises
    ------
    RuntimeError if no Stata installation is detected.
    """
    global _adapter
    if _adapter is not None:
        return _adapter

    version_info = detect_stata()

    if version_info.supports_pystata:
        try:
            adapter = PystataAdapter()
            if adapter.is_available:
                _adapter = adapter
                return _adapter
        except Exception:
            pass  # fall through to console fallback

    # Fall back to console mode
    adapter = ConsoleFallback()
    if adapter.is_available:
        _adapter = adapter
        return _adapter

    raise RuntimeError(
        "No Stata installation detected. "
        "Install Stata 17+ and pystata, or ensure Stata is on your PATH. "
        "See https://www.stata.com/python/pystata18/"
    )


def run(
    code: str,
    *,
    capture_graphs: bool = True,
    capture_log: bool = True,
    timeout: float | None = 120.0,
    config: dict[str, Any] | None = None,
) -> StataResult:
    """
    Execute Stata ``code`` and return a unified ``StataResult``.

    This is the primary user-facing API. All frontends (Jupyter kernel,
    MCP server, VSCode extension) call this function under the hood.

    Parameters
    ----------
    code: Stata command(s) to execute. Can include newlines; multiple
        commands separated by ``;`` (Stata's delimiter).
    capture_graphs: If True, capture graph files created during execution.
    capture_log: If True, capture the complete Stata log.
    timeout: Seconds before raising TimeoutError. None = no timeout.
    config: Optional backend-specific config dict. For PystataAdapter:
        - ``graph_format``: output format (default "png")
        - ``graphs_dir``: directory to scan for graph files (default ".")

    Returns
    -------
    StataResult with fields: stdout, log, results (r()/e()), graphs, error,
    return_code, stata_version, elapsed_seconds, warnings.
    """
    adapter = get_adapter()
    return adapter.run(
        code,
        capture_graphs=capture_graphs,
        capture_log=capture_log,
        timeout=timeout,
    )


# Alias for users who prefer run_code()
run_code = run