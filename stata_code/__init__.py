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

from stata_code.core._runtime import PystataNotAvailable, is_available
from stata_code.core.errors import classify_rc, suggestions_for
from stata_code.core.runner import (
    cancel,
    clear_cancel,
    execute,
    get_graph,
    get_log,
    get_matrix,
    is_cancel_pending,
    list_sessions,
    reset_session,
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

# Convenience alias: `run(...)` == `execute(...)`.
run = execute

__version__ = "0.5.0"

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
    # Availability check
    "is_available",
    "PystataNotAvailable",
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
