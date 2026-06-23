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
    recovery_for,
    suggestions_for,
)
from stata_code.core.estimation import (
    build_estimation_from_returns,
    build_estimation_result,
)
from stata_code.core.provenance import build_provenance, build_reproducible_do
from stata_code.core.runner import (
    execute,
    get_graph,
    get_log,
    list_sessions,
    reset_session,
)
from stata_code.core.schema import (
    Backend,
    Coefficient,
    DatasetInfo,
    ErrorContext,
    ErrorInfo,
    ErrorKind,
    EstimationResult,
    GraphFormat,
    GraphInfo,
    IncludeGraphs,
    LogFileInfo,
    LogInfo,
    Matrix,
    Provenance,
    Recovery,
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
    "recovery_for",
    "suggestions_for",
    "build_estimation_result",
    "build_estimation_from_returns",
    "build_provenance",
    "build_reproducible_do",
    "RunResult",
    "ErrorKind",
    "ErrorInfo",
    "ErrorContext",
    "Suggestion",
    "Recovery",
    "Provenance",
    "Coefficient",
    "EstimationResult",
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
