"""stata_code core — schema, errors, runner.

Most consumers import from ``stata_code`` directly. This module exposes the
internals for advanced use (e.g., type-only imports, custom adapters).
"""

from stata_code.core._runtime import (
    PystataNotAvailable,
    PystataRuntime,
    get_runtime,
    is_available,
)
from stata_code.core.errors import (
    RC_LABEL,
    RC_TO_KIND,
    classify_rc,
    label_for_rc,
    suggestions_for,
)
from stata_code.core.runner import (
    execute,
    get_graph,
    get_log,
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

__all__ = [
    "execute",
    "get_graph",
    "get_log",
    "list_sessions",
    "reset_session",
    "PystataRuntime",
    "PystataNotAvailable",
    "is_available",
    "get_runtime",
    "RC_TO_KIND",
    "RC_LABEL",
    "classify_rc",
    "label_for_rc",
    "suggestions_for",
    "RunResult",
    "ErrorKind",
    "ErrorInfo",
    "ErrorContext",
    "Suggestion",
    "LogInfo",
    "LogFileInfo",
    "ResultsInfo",
    "StataReturns",
    "Matrix",
    "DatasetInfo",
    "VariableInfo",
    "GraphInfo",
    "GraphFormat",
    "IncludeGraphs",
    "StataInfo",
    "StataEdition",
    "Backend",
    "StataWarning",
]
