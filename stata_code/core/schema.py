"""Pydantic v2 models for the stata_code v1.0 result schema (see SCHEMA.md)."""

from __future__ import annotations

import re
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ─────────────────────────────────────────────────────────────────────────────
# Enums (closed at v1.0; new values are minor-version additive)
# ─────────────────────────────────────────────────────────────────────────────


class ErrorKind(str, Enum):
    SYNTAX = "syntax"
    COMMAND_NOT_FOUND = "command_not_found"
    VARNAME_NOT_FOUND = "varname_not_found"
    INVALID_NAME = "invalid_name"
    TYPE_MISMATCH = "type_mismatch"
    NAME_CONFLICT = "name_conflict"
    NOT_SORTED = "not_sorted"
    CONVERGENCE = "convergence"
    INFEASIBLE = "infeasible"
    ESTIMATION_SAMPLE_EMPTY = "estimation_sample_empty"
    ESTIMATION_FAILURE = "estimation_failure"
    NO_ESTIMATION_RESULTS = "no_estimation_results"
    NO_OBSERVATIONS = "no_observations"
    DATA_IN_MEMORY = "data_in_memory"
    MATRIX_SINGULAR = "matrix_singular"
    MATRIX_CONFORMABILITY = "matrix_conformability"
    MATRIX_MISSING = "matrix_missing"
    FILE_NOT_FOUND = "file_not_found"
    FILE_EXISTS = "file_exists"
    FILE_CORRUPT = "file_corrupt"
    FILE_IO = "file_io"
    NETWORK = "network"
    PERMISSION = "permission"
    ENCODING = "encoding"
    STATA_LIMIT = "stata_limit"
    OUT_OF_MEMORY = "out_of_memory"
    INTERRUPT = "interrupt"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    ADAPTER_CRASH = "adapter_crash"
    UNKNOWN = "unknown"


class StataEdition(str, Enum):
    MP = "MP"
    SE = "SE"
    IC = "IC"
    BE = "BE"
    UNKNOWN = "unknown"


class GraphFormat(str, Enum):
    PNG = "png"
    SVG = "svg"
    PDF = "pdf"


class IncludeGraphs(str, Enum):
    REF = "ref"
    INLINE = "inline"
    NONE = "none"


class Backend(str, Enum):
    PYSTATA = "pystata"
    CONSOLE = "console"


# ─────────────────────────────────────────────────────────────────────────────
# Base config — every model is forward-compat (tolerates unknown fields)
# ─────────────────────────────────────────────────────────────────────────────


class _Base(BaseModel):
    """Base for all schema models; allows unknown fields per §6 forward-compat."""

    model_config = ConfigDict(extra="allow", validate_assignment=True)


# ─────────────────────────────────────────────────────────────────────────────
# Sub-models
# ─────────────────────────────────────────────────────────────────────────────


class StataInfo(_Base):
    version: str | None = None
    edition: StataEdition = StataEdition.UNKNOWN
    backend: Backend


class LogFileInfo(_Base):
    """Persistent on-disk artifacts written for file-backed runs."""

    directory: str
    log_path: str
    smcl_path: str
    manifest_path: str
    code_path: str | None = None
    working_dir: str | None = None
    graphs_dir: str | None = None
    outputs_dir: str | None = None
    graph_paths: list[str] = Field(default_factory=list)
    output_paths: list[str] = Field(default_factory=list)
    policy: Literal["per_run_directory"] = "per_run_directory"
    append: bool = False


class LogInfo(_Base):
    head: str = ""
    tail: str = ""
    lines_total: int = 0
    bytes_total: int = 0
    truncated: bool = False
    complete: bool = True
    error_window: str | None = None
    ref: str | None = None
    files: LogFileInfo | None = None

    @model_validator(mode="after")
    def _check_invariants(self) -> LogInfo:
        if self.truncated and self.ref is None:
            raise ValueError("log.truncated=True requires log.ref to be set")
        if not self.truncated and self.tail != "":
            raise ValueError(
                "log.truncated=False requires log.tail to be empty "
                "(see SCHEMA.md §3.3)"
            )
        if self.lines_total < 0 or self.bytes_total < 0:
            raise ValueError("log.lines_total and log.bytes_total must be ≥ 0")
        return self


class Matrix(_Base):
    rows: list[str]
    cols: list[str]
    values: list[list[float | None]] | None = None
    ref: str | None = None

    @model_validator(mode="after")
    def _check_shape(self) -> Matrix:
        if self.values is None and self.ref is None:
            raise ValueError("matrix must have either values or ref set (or both)")
        if self.values is not None:
            if len(self.values) != len(self.rows):
                raise ValueError(
                    f"matrix.values has {len(self.values)} rows, "
                    f"expected {len(self.rows)}"
                )
            ncols = len(self.cols)
            for i, row in enumerate(self.values):
                if len(row) != ncols:
                    raise ValueError(
                        f"matrix.values row {i} has {len(row)} cols, "
                        f"expected {ncols}"
                    )
        return self


class StataReturns(_Base):
    """Shape shared by r() and e() — distinct instances at RunResult.results.{r,e}."""

    scalars: dict[str, float | None] = Field(default_factory=dict)
    macros: dict[str, str] = Field(default_factory=dict)
    matrices: dict[str, Matrix] = Field(default_factory=dict)


class ResultsInfo(_Base):
    r: StataReturns = Field(default_factory=StataReturns)
    e: StataReturns = Field(default_factory=StataReturns)
    last_estimation_cmd: str | None = None


class VariableInfo(_Base):
    name: str
    type: str  # Stata storage type: byte/int/long/float/double/str#/strL
    label: str = ""


class DatasetInfo(_Base):
    frame: str = "default"
    n_obs: int = 0
    n_vars: int = 0
    changed: bool = False
    filename: str | None = None
    variables: list[VariableInfo] | None = None


class GraphInfo(_Base):
    ref: str
    name: str = "Graph"
    format: GraphFormat = GraphFormat.PNG
    width: int | None = None
    height: int | None = None
    source_command: str | None = None
    source_line: int | None = None
    inline: str | None = None  # base64 of the bytes when explicitly requested
    file_path: str | None = None


class Suggestion(_Base):
    action: str
    command: str | None = None


class ErrorContext(_Base):
    before: list[str] = Field(default_factory=list)
    failing: str = ""
    after: list[str] = Field(default_factory=list)


_MESSAGE_MAX = 4096
_COMMAND_MAX = 1024
_WARNING_MAX = 1024
_TRUNC_MARK = "…"


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - len(_TRUNC_MARK)] + _TRUNC_MARK


class ErrorInfo(_Base):
    kind: ErrorKind
    rc: int
    rc_label: str = ""
    message: str = ""
    command: str | None = None
    line: int | None = None
    context: ErrorContext = Field(default_factory=ErrorContext)
    commands_executed: int | None = None
    path: str | None = None
    varname: str | None = None
    name: str | None = None
    suggestions: list[Suggestion] = Field(default_factory=list)

    @field_validator("message")
    @classmethod
    def _truncate_message(cls, v: str) -> str:
        return _truncate(v, _MESSAGE_MAX)

    @field_validator("command")
    @classmethod
    def _truncate_command(cls, v: str | None) -> str | None:
        return None if v is None else _truncate(v, _COMMAND_MAX)


class StataWarning(_Base):
    """JSON wire name is `warnings`; class avoids shadowing the builtin `Warning`."""

    kind: str = "unknown"
    message: str = ""

    @field_validator("message")
    @classmethod
    def _truncate(cls, v: str) -> str:
        return _truncate(v, _WARNING_MAX)


class OriginInfo(_Base):
    """Echo of the editor-side origin metadata supplied with the request.

    Pure round-trip — the runner does not interpret these fields beyond
    forwarding ``path``/``kind``/``label`` to the on-disk run-bundle manifest.
    Notebook-aware agents set ``cell_id`` to a stable nbformat 4.5+ UUID so
    they can correlate ``stata_run`` calls with notebook cells without the
    MCP protocol itself becoming notebook-aware.
    """

    path: str | None = None
    kind: str | None = None
    label: str | None = None
    cell_id: str | None = None


# ─────────────────────────────────────────────────────────────────────────────
# Top-level
# ─────────────────────────────────────────────────────────────────────────────


_SESSION_ID_RE = re.compile(r"[A-Za-z0-9_-]+")


class RunResult(_Base):
    """Top-level v1.0 schema. SCHEMA.md is normative; this is its derived form."""

    ok: bool
    rc: int
    session_id: str = "main"
    request_id: str
    started_at: str  # ISO 8601 UTC with millisecond precision
    elapsed_ms: int
    stata_elapsed_ms: int | None = None

    stata: StataInfo
    log: LogInfo = Field(default_factory=LogInfo)
    results: ResultsInfo = Field(default_factory=ResultsInfo)
    dataset: DatasetInfo = Field(default_factory=DatasetInfo)
    graphs: list[GraphInfo] = Field(default_factory=list)
    warnings: list[StataWarning] = Field(default_factory=list)
    error: ErrorInfo | None = None
    origin: OriginInfo | None = None

    schema_version: Literal["1.0"] = "1.0"
    capabilities: list[str] = Field(default_factory=list)

    @field_validator("session_id")
    @classmethod
    def _check_session_id(cls, v: str) -> str:
        if not _SESSION_ID_RE.fullmatch(v):
            raise ValueError(
                f"session_id must match [A-Za-z0-9_-]+; got {v!r}. "
                "':' is reserved for future remote prefixing."
            )
        return v

    @field_validator("elapsed_ms")
    @classmethod
    def _nonneg_elapsed(cls, v: int) -> int:
        if v < 0:
            raise ValueError(f"elapsed_ms must be ≥ 0; got {v}")
        return v

    @field_validator("stata_elapsed_ms")
    @classmethod
    def _nonneg_stata_elapsed(cls, v: int | None) -> int | None:
        if v is not None and v < 0:
            raise ValueError(f"stata_elapsed_ms must be ≥ 0; got {v}")
        return v

    @model_validator(mode="after")
    def _consistency(self) -> RunResult:
        if self.ok:
            if self.error is not None:
                raise ValueError("ok=True requires error to be None (SCHEMA.md §3.1)")
            if self.rc != 0:
                raise ValueError(f"ok=True requires rc=0; got {self.rc}")
        else:
            if self.error is None:
                raise ValueError(
                    "ok=False requires error to be non-None (SCHEMA.md §3.1)"
                )
            if self.error.rc != self.rc:
                raise ValueError(
                    f"top-level rc ({self.rc}) must equal error.rc ({self.error.rc})"
                )
        return self
