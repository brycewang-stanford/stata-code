"""stata_code — agent-native Stata bridge.

Public API::

    from stata_code import run, RunResult, get_log, get_graph, list_sessions

    r = run("regress mpg weight")
    if r.ok:
        print(r.results.e.scalars["r2"])
        for g in r.graphs:
            print(g.ref)
    else:
        print(r.error.kind, r.error.message)
        for s in r.error.suggestions:
            print("hint:", s.action)

The result envelope, multi-session model, error taxonomy, and token-economy
defaults are all defined in ``SCHEMA.md`` (the normative contract).
"""

from __future__ import annotations

from typing import Literal

from stata_code.core._pool import (
    get_default_pool,
    pool_execute,
    pool_stata_info,
    shutdown_default_pool,
)
from stata_code.core._runtime import PystataNotAvailable
from stata_code.core.errors import classify_rc, suggestions_for
from stata_code.core.runner import (
    RefNotFound,
    get_graph,
    get_log,
    get_matrix,
)
from stata_code.core.schema import (
    Backend,
    DatasetInfo,
    ErrorContext,
    ErrorInfo,
    ErrorKind,
    GraphFormat,
    GraphInfo,
    IncludeGraphs,
    LogFileInfo,
    LogInfo,
    Matrix,
    ResultsInfo,
    RunResult,
    StataEdition,
    StataInfo,
    StataReturns,
    StataWarning,
    Suggestion,
    VariableInfo,
)


def run(
    code: str,
    *,
    session_id: str = "main",
    log_lines_head: int = 20,
    log_lines_tail: int = 20,
    include_full_log: bool = False,
    include_graphs: Literal["ref", "inline", "none"] = "ref",
    graph_format: Literal["png", "svg", "pdf"] = "png",
    include_dataset_variables: bool = True,
    timeout_ms: int | None = 600_000,
    persist_log_files: bool = False,
    persist_generated_files: bool = True,
    origin_path: str | None = None,
    origin_kind: str | None = None,
    origin_label: str | None = None,
    origin_cell_id: str | None = None,
    use_origin_workdir: bool = True,
    working_dir: str | None = None,
) -> RunResult:
    """Run Stata code through the subprocess-backed public API.

    The package-level API uses the same hard-timeout, stdout-isolated backend
    as the MCP server. The lower-level in-process runner remains available as
    ``stata_code.core.runner.execute`` for callers that explicitly need it.
    """
    return pool_execute(
        code,
        session_id=session_id,
        log_lines_head=log_lines_head,
        log_lines_tail=log_lines_tail,
        include_full_log=include_full_log,
        include_graphs=include_graphs,
        graph_format=graph_format,
        include_dataset_variables=include_dataset_variables,
        timeout_ms=timeout_ms,
        persist_log_files=persist_log_files,
        persist_generated_files=persist_generated_files,
        origin_path=origin_path,
        origin_kind=origin_kind,
        origin_label=origin_label,
        origin_cell_id=origin_cell_id,
        use_origin_workdir=use_origin_workdir,
        working_dir=working_dir,
    )


# Backward-compatible package-level name. The direct in-process runner is still
# available from ``stata_code.core.runner``.
execute = run


def list_sessions() -> list[dict[str, object]]:
    """Enumerate subprocess-backed public API sessions."""
    return get_default_pool().list_session_info()


def reset_session(session_id: str = "main") -> dict[str, object]:
    """Drop one subprocess-backed session by terminating its worker."""
    return {
        "session_id": session_id,
        "dropped_frame": get_default_pool().reset_session(session_id),
    }


def cancel(session_id: str = "main") -> bool:
    """Request cancellation for a public API session.

    Returns ``True`` when this call registered a new cancellation request,
    ``False`` when one was already pending (idempotent).

    Only affects the subprocess-pool path used by ``stata_code.run()``. The
    in-process runner exposes its own independent cancellation domain via
    ``stata_code.core.runner.cancel`` — calling this function does not
    short-circuit a direct ``core.runner.execute()`` invocation.

    If the worker is currently running, the worker process is killed as
    part of the request; the in-flight run terminates with an error of
    ``kind="cancelled"``.
    """
    registered, _killed_worker = get_default_pool().request_cancel(session_id)
    return registered


def clear_cancel(session_id: str = "main") -> bool:
    """Clear a pending public API cancellation request.

    Returns ``True`` if a pending cancel was cleared, ``False`` otherwise.
    Affects only the subprocess-pool path (see :func:`cancel`).
    """
    return get_default_pool().clear_cancel(session_id)


def is_cancel_pending(session_id: str = "main") -> bool:
    """Whether the public API will cancel the next run for this session.

    Reflects only the subprocess-pool path (see :func:`cancel`).
    """
    return get_default_pool().is_cancel_pending(session_id)


def is_available() -> bool:
    """Return whether Stata can be initialized without touching caller stdout.

    This package-level check goes through the subprocess pool. The lower-level
    ``stata_code.core._runtime.is_available`` still checks the in-process
    runtime directly.
    """
    try:
        pool_stata_info()
    except Exception:  # noqa: BLE001
        return False
    return True


__version__ = "0.6.3"

__all__ = [
    # Primary entry points
    "run",
    "execute",
    "RunResult",
    # Auxiliary tools
    "get_log",
    "get_graph",
    "get_matrix",
    "list_sessions",
    "reset_session",
    "cancel",
    "clear_cancel",
    "is_cancel_pending",
    "pool_execute",
    "shutdown_default_pool",
    # Availability check
    "is_available",
    "PystataNotAvailable",
    "RefNotFound",
    # Schema enums and component types
    "Backend",
    "DatasetInfo",
    "ErrorContext",
    "ErrorInfo",
    "ErrorKind",
    "GraphFormat",
    "GraphInfo",
    "IncludeGraphs",
    "LogInfo",
    "LogFileInfo",
    "Matrix",
    "ResultsInfo",
    "StataEdition",
    "StataInfo",
    "StataReturns",
    "StataWarning",
    "Suggestion",
    "VariableInfo",
    # Error helpers
    "classify_rc",
    "suggestions_for",
]
