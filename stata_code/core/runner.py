"""High-level execute() — runs Stata code and returns a v1.0 RunResult.

This is the only place that touches Stata. The MCP server and Jupyter
kernel both import from here and only translate transports.

Implements the v1.0 envelope from SCHEMA.md: ok / rc / error / log /
results / dataset / graphs / warnings / capabilities. r() and e() are
collected via sfi (native types). Multi-session is implemented through
Stata frames (session_id="main" ↔ default frame). Per-line error
attribution comes from parsing pystata's transcript.

This direct in-process runner does not enforce hard timeouts once execution
has entered `pystata`; use the package-level `stata_code.run()` or the MCP
server for the subprocess-backed timeout/cancellation path. Still-deferred
items such as console fallback for Stata 11–16 and streaming logs are tracked
in SCHEMA.md §8.
"""

from __future__ import annotations

import functools
import hashlib
import re
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ParamSpec, TypeVar

from stata_code.core import _refs
from stata_code.core._runtime import PystataNotAvailable, get_runtime
from stata_code.core.errors import classify_rc, suggestions_for
from stata_code.core.log_artifacts import (
    FileSnapshot,
    changed_output_files,
    copy_output_artifacts,
    persist_run_log_files,
    snapshot_working_dir_files,
    update_run_artifact_manifest,
)
from stata_code.core.schema import (
    Backend,
    DatasetInfo,
    ErrorContext,
    ErrorInfo,
    ErrorKind,
    GraphFormat,
    GraphInfo,
    LogFileInfo,
    LogInfo,
    Matrix,
    OriginInfo,
    ResultsInfo,
    RunResult,
    StataEdition,
    StataInfo,
    StataReturns,
    StataWarning,
    VariableInfo,
)

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


_EDITION_MAP: dict[str, StataEdition] = {
    "mp": StataEdition.MP,
    "se": StataEdition.SE,
    "ic": StataEdition.IC,
    "be": StataEdition.BE,
}

_ERETURN_NAME_RE = re.compile(r"^\s*(?:e|r)\(([A-Za-z_][A-Za-z0-9_]*)\)\s*[=:]")
_VARNAME_RE = re.compile(r"variable (\w+) (?:not found|already defined)")
_FILE_PATH_RE = re.compile(
    r"file\s+(\S+?)\s+(?:not\s+found|already\s+exists|could\s+not)"
)
_NAME_CONFLICT_RE = re.compile(r"(\w+)\s+already\s+(?:defined|exists)")
_UNRECOGNIZED_CMD_RE = re.compile(r"(\S+)\s+(?:is\s+)?unrecognized\s+command")

# Cooperative cancellation for the IN-PROCESS runner only. A per-session
# "cancel-pending" flag, settable from any thread via `cancel(session_id)`.
# The flag is consumed by the next `execute()` call for that session, which
# short-circuits and returns a RunResult with `error.kind="cancelled"`
# instead of forwarding the code to Stata. Cooperative semantics — does
# NOT interrupt code that is already mid-`stata.run()`. Hard interruption
# is provided by the subprocess pool (`SessionPool.request_cancel`).
#
# IMPORTANT: this set is DISJOINT from `SessionPool._cancel_pending`. The
# top-level `stata_code.cancel()` only touches the pool's set, so calling
# it does NOT short-circuit a direct `core.runner.execute()` call. Users
# of the in-process API must import `cancel` from this module.
_cancel_lock = threading.Lock()
_cancel_pending: set[str] = set()
_stata_state_lock = threading.RLock()
_P = ParamSpec("_P")
_R = TypeVar("_R")


def _serialize_stata_state(fn: Callable[_P, _R]) -> Callable[_P, _R]:
    """Serialize entrypoints that mutate or inspect process-global Stata state."""

    @functools.wraps(fn)
    def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        with _stata_state_lock:
            return fn(*args, **kwargs)

    return wrapper

# Cap on `dataset.variables` to avoid pathological return sizes (per SCHEMA §3.5).
_DATASET_VAR_CAP = 200

# Cap on inlined matrix cells (rows × cols). Above this, `values` is omitted
# from the envelope and a `matrix://...` ref is stored instead, retrievable
# via `get_matrix(ref)`. Per SCHEMA.md §3.4: "Producers SHOULD do this when
# a matrix would inline more than ~10,000 cells."
MATRIX_INLINE_CELL_CAP = 10_000


def _utc_iso_ms() -> str:
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def _new_request_id() -> str:
    # uuid4 hex is unique enough; ULID would be sortable but adds a dep.
    return uuid.uuid4().hex


def _split_log(
    log: str,
    head_lines: int,
    tail_lines: int,
    include_full: bool,
    request_id: str,
) -> LogInfo:
    """Build a LogInfo per SCHEMA §3.3.

    Stores the full log under `log://<request_id>` when truncating, so that
    `get_log(ref)` can retrieve it later within the producer's lifetime.
    """
    norm = log.replace("\r\n", "\n").replace("\r", "\n")
    lines = norm.split("\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]
    lines_total = len(lines)
    # `bytes_total` reflects the byte count of what `get_log(ref)` would
    # return — i.e., the normalized text without trailing newline.
    full_text = "\n".join(lines)
    bytes_total = len(full_text.encode("utf-8"))

    if include_full or lines_total <= head_lines + tail_lines:
        return LogInfo(
            head=full_text,
            tail="",
            lines_total=lines_total,
            bytes_total=bytes_total,
            truncated=False,
            complete=True,
            ref=None,
        )

    head = "\n".join(lines[:head_lines])
    tail = "\n".join(lines[-tail_lines:])
    ref = f"log://{request_id}"
    _refs.put(
        ref,
        {"text": full_text, "lines_total": lines_total, "bytes_total": bytes_total},
    )
    return LogInfo(
        head=head,
        tail=tail,
        lines_total=lines_total,
        bytes_total=bytes_total,
        truncated=True,
        complete=True,
        ref=ref,
    )


class RefNotFound(KeyError):
    """Raised by :func:`get_log` / :func:`get_graph` / :func:`get_matrix`
    when a ref has expired, was never produced, or was dropped by a
    session reset.

    Subclasses :class:`KeyError` for backward compatibility — older
    ``except KeyError:`` callers keep working — but exposes :pyattr:`ref`
    and :pyattr:`kind` for typed handling.
    """

    def __init__(self, ref: str, *, kind: str = "unknown_ref") -> None:
        self.ref = ref
        self._kind = kind
        super().__init__(f"{kind}: {ref!r}")

    @property
    def kind(self) -> str:
        return self._kind


def get_log(ref: str) -> dict[str, Any]:
    """Auxiliary tool: fetch the full log behind a `log.ref`.

    Per SCHEMA.md §5. Raises :class:`RefNotFound` (a ``KeyError`` subclass)
    when the ref is unknown.
    """
    payload = _refs.get(ref)
    if payload is None:
        raise RefNotFound(ref, kind="unknown_log_ref")
    return {
        "text": payload["text"],
        "lines_total": payload["lines_total"],
        "bytes_total": payload["bytes_total"],
    }


def cancel(session_id: str = "main") -> bool:
    """Request cancellation of the next ``execute()`` call for ``session_id``.

    Cooperative: does **not** interrupt code that is currently mid-execution
    inside pystata. The flag is consumed (and the run short-circuited)
    when ``execute(session_id=...)`` is next invoked for the same session.
    The short-circuit returns a ``RunResult`` with ``ok=False``, ``rc=-1``,
    and ``error.kind=cancelled``.

    Returns ``True`` if a new cancel was registered, ``False`` if one was
    already pending (idempotent).
    """
    with _cancel_lock:
        if session_id in _cancel_pending:
            return False
        _cancel_pending.add(session_id)
        return True


def is_cancel_pending(session_id: str = "main") -> bool:
    """Whether a cancel will fire on the next ``execute()`` for this session."""
    with _cancel_lock:
        return session_id in _cancel_pending


def clear_cancel(session_id: str = "main") -> bool:
    """Drop any pending cancel for ``session_id`` without firing it.

    Returns ``True`` if a pending cancel was cleared.
    """
    with _cancel_lock:
        if session_id in _cancel_pending:
            _cancel_pending.remove(session_id)
            return True
        return False


def _consume_cancel(session_id: str) -> bool:
    """Pop and return whether a cancel is pending for ``session_id``."""
    with _cancel_lock:
        if session_id in _cancel_pending:
            _cancel_pending.remove(session_id)
            return True
        return False


def _build_cancelled_result(
    *,
    rt: Any,
    session_id: str,
    request_id: str,
    started_at: str,
    started: float,
    include_dataset_variables: bool,
) -> RunResult:
    """Synthesize a RunResult for a cancel-before-Stata short-circuit.

    The dataset block still reflects current state (post-cancel snapshot);
    log / results / graphs / warnings are empty because no code ran.
    rc=-3 is the synthetic code reserved for cooperative cancellation
    (distinct from -1 adapter_crash and -2 timeout, per SCHEMA.md §3.7).
    """
    elapsed_total_ms = max(1, int((time.monotonic() - started) * 1000))
    return RunResult(
        ok=False,
        rc=-3,
        session_id=session_id,
        request_id=request_id,
        started_at=started_at,
        elapsed_ms=elapsed_total_ms,
        stata_elapsed_ms=0,
        stata=_stata_info(rt),
        log=LogInfo(
            head="", tail="", lines_total=0, bytes_total=0,
            truncated=False, complete=True, ref=None,
        ),
        results=ResultsInfo(),
        dataset=_collect_dataset(rt, include_dataset_variables),
        graphs=[],
        warnings=[],
        error=ErrorInfo(
            kind=ErrorKind.CANCELLED,
            rc=-3,
            rc_label="cancelled",
            message=(
                "Execution cancelled before Stata received the code "
                f"(session_id={session_id!r})."
            ),
            command=None,
            line=None,
            context=ErrorContext(before=[], failing="", after=[]),
            commands_executed=0,
            path=None,
            varname=None,
            name=None,
            suggestions=[],
        ),
        capabilities=["cancel", "multi_session"],
    )


def _parse_return_list(text: str) -> dict[str, list[str]]:
    """Parse `return list` / `ereturn list` output into category -> names.

    Categories are 'scalars', 'macros', 'matrices' (and 'functions' which we
    ignore in v0.1).
    """
    out: dict[str, list[str]] = {"scalars": [], "macros": [], "matrices": []}
    current: str | None = None
    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        # Section headers are at the left margin: "scalars:", "macros:", etc.
        if not line.startswith(" ") and stripped.endswith(":"):
            label = stripped[:-1].strip().lower()
            if label in out:
                current = label
            else:
                current = None
            continue
        if current is None:
            continue
        m = _ERETURN_NAME_RE.match(line)
        if m:
            out[current].append(m.group(1))
    return out


def _list_returns(rt: Any, prefix: str) -> dict[str, list[str]]:
    """Get the names of r() / e() members by parsing `return list` text.

    `prefix` is "r" or "e". This runs `return list` / `ereturn list` and
    captures its output into a dedicated buffer (separate from the user log).
    """
    cmd = "ereturn list" if prefix == "e" else "return list"
    stdout, rc, _err = rt.run_capture(cmd)
    if rc != 0:
        return {"scalars": [], "macros": [], "matrices": []}
    return _parse_return_list(stdout)


def _collect_returns(rt: Any, prefix: str, request_id: str) -> StataReturns:
    """Build a StataReturns for r() or e() using sfi for typed access.

    Matrices larger than ``MATRIX_INLINE_CELL_CAP`` cells are emitted with
    ``values=None`` and a ``matrix://<request_id>/<prefix>/<name>`` ref;
    callers fetch the values via :func:`get_matrix`.

    Failure semantics: a per-name collection failure (sfi raises) is
    silently coerced — scalars become ``None``, macros become ``""``,
    matrices are dropped from the dict entirely. This is deliberate: the
    caller has no actionable recourse for a Stata C-side error on a
    single scalar, and bubbling it would mask the surrounding successful
    fields. Agents that need to detect dropped matrices should compare
    ``return list`` / ``ereturn list`` against the populated dict.
    """
    names = _list_returns(rt, prefix)
    sfi = rt.sfi

    scalars: dict[str, float | None] = {}
    for name in names["scalars"]:
        try:
            v = sfi.Scalar.getValue(f"{prefix}({name})")
            scalars[name] = float(v) if v is not None else None
        except Exception:  # noqa: BLE001
            scalars[name] = None

    macros: dict[str, str] = {}
    for name in names["macros"]:
        try:
            v = sfi.Macro.getGlobal(f"{prefix}({name})")
            macros[name] = v if v is not None else ""
        except Exception:  # noqa: BLE001
            macros[name] = ""

    matrices: dict[str, Matrix] = {}
    for name in names["matrices"]:
        key = f"{prefix}({name})"
        try:
            values = sfi.Matrix.get(key)
            rows = list(sfi.Matrix.getRowNames(key) or [])
            cols = list(sfi.Matrix.getColNames(key) or [])
            norm_values: list[list[float | None]] = [
                [None if v is None else float(v) for v in row]
                for row in values
            ]
            n_rows = len(norm_values)
            n_cols = len(norm_values[0]) if n_rows else 0
            if n_rows * n_cols > MATRIX_INLINE_CELL_CAP:
                ref = f"matrix://{request_id}/{prefix}/{name}"
                _refs.put(
                    ref,
                    {"rows": rows, "cols": cols, "values": norm_values},
                )
                matrices[name] = Matrix(
                    rows=rows, cols=cols, values=None, ref=ref
                )
            else:
                matrices[name] = Matrix(
                    rows=rows, cols=cols, values=norm_values, ref=None
                )
        except Exception:  # noqa: BLE001
            continue

    return StataReturns(scalars=scalars, macros=macros, matrices=matrices)


def _collect_dataset(rt: Any, include_variables: bool) -> DatasetInfo:
    sfi = rt.sfi
    Data = sfi.Data
    SFIToolkit = sfi.SFIToolkit
    Scalar = sfi.Scalar

    n_vars = int(Data.getVarCount())
    n_obs = int(Data.getObsTotal())

    # c(changed) / c(filename) / c(frame): some are scalar-accessible, some are
    # macro-accessible. Use a try/fallback.
    def _c_macro(name: str) -> str | None:
        try:
            v = SFIToolkit.macroExpand(f"`c({name})'")
            return v if v else None
        except Exception:  # noqa: BLE001
            return None

    changed_val = 0.0
    try:
        changed_val = float(Scalar.getValue("c(changed)") or 0.0)
    except Exception:  # noqa: BLE001
        pass
    changed = bool(changed_val)

    filename = _c_macro("filename")
    frame_name = _c_macro("frame") or "default"

    variables: list[VariableInfo] | None
    if include_variables and n_vars > 0:
        cap = min(n_vars, _DATASET_VAR_CAP)
        variables = [
            VariableInfo(
                name=Data.getVarName(i),
                type=Data.getVarType(i),
                label=Data.getVarLabel(i) or "",
            )
            for i in range(cap)
        ]
    else:
        variables = None

    return DatasetInfo(
        frame=frame_name,
        n_obs=n_obs,
        n_vars=n_vars,
        changed=changed,
        filename=filename,
        variables=variables,
    )


def _stata_info(rt: Any) -> StataInfo:
    sfi = rt.sfi
    SFIToolkit = sfi.SFIToolkit
    try:
        version = SFIToolkit.macroExpand("`c(stata_version)'") or None
    except Exception:  # noqa: BLE001
        version = None
    edition_str = (rt.edition or "").lower()
    edition = _EDITION_MAP.get(edition_str, StataEdition.UNKNOWN)
    return StataInfo(version=version, edition=edition, backend=Backend.PYSTATA)


def _extract_typed_fields(kind: ErrorKind, message: str) -> dict[str, str | None]:
    fields: dict[str, str | None] = {
        "varname": None,
        "path": None,
        "name": None,
        "command": None,
    }
    if kind == ErrorKind.VARNAME_NOT_FOUND or kind == ErrorKind.NAME_CONFLICT:
        m = _VARNAME_RE.search(message)
        if m:
            if kind == ErrorKind.VARNAME_NOT_FOUND:
                fields["varname"] = m.group(1)
            else:
                fields["name"] = m.group(1)
    if kind in (
        ErrorKind.FILE_NOT_FOUND,
        ErrorKind.FILE_EXISTS,
        ErrorKind.FILE_IO,
        ErrorKind.FILE_CORRUPT,
    ):
        m = _FILE_PATH_RE.search(message)
        if m:
            fields["path"] = m.group(1)
    if kind == ErrorKind.NAME_CONFLICT and fields["name"] is None:
        m = _NAME_CONFLICT_RE.search(message)
        if m:
            fields["name"] = m.group(1)
    if kind == ErrorKind.COMMAND_NOT_FOUND:
        m = _UNRECOGNIZED_CMD_RE.search(message)
        if m:
            fields["command"] = m.group(1)
    return fields


def _parse_failure_transcript(
    error_text: str, user_code: str
) -> dict[str, Any]:
    """Pinpoint the failing command in multi-line user code.

    pystata's SystemError for multi-line input contains the full Stata
    transcript with `. <cmd>` echoes for each line. We parse it to recover:

    - `failing`: the failing command's text (or "" if not isolatable)
    - `line`: 1-indexed line in the original user code (or None)
    - `commands_executed`: count of *non-comment* commands that completed
      successfully before the failure (or None)
    - `before` / `after`: surrounding lines in the user code (up to 3 / 1)
    """
    out: dict[str, Any] = {
        "failing": "",
        "line": None,
        "commands_executed": None,
        "before": [],
        "after": [],
        "command": None,
    }
    user_lines = user_code.splitlines()
    non_empty_user_lines = [ln for ln in user_lines if ln.strip()]

    # Single-line case — no transcript, just the error message.
    if "\n. " not in error_text and not error_text.startswith(". "):
        if len(non_empty_user_lines) == 1:
            failing = non_empty_user_lines[0].strip()
            out["failing"] = failing
            out["command"] = failing
            # Find its line number in the original (with blanks)
            for i, ln in enumerate(user_lines, 1):
                if ln.strip() == failing:
                    out["line"] = i
                    break
            out["commands_executed"] = 0
        return out

    # Multi-line case — parse `. <cmd>` lines.
    transcript_lines = error_text.split("\n")
    cmd_echoes: list[str] = []
    for ln in transcript_lines:
        if not ln.startswith(". "):
            continue
        body = ln[2:].strip()
        if not body:
            continue  # empty `. ` is just a prompt
        if body.startswith("*") or body.startswith("//"):
            continue  # comment-only line — Stata echoes but doesn't "run"
        cmd_echoes.append(body)

    if not cmd_echoes:
        return out

    failing = cmd_echoes[-1]
    out["failing"] = failing
    out["command"] = failing
    out["commands_executed"] = len(cmd_echoes) - 1

    # Match against original user code lines (with blanks) for line number.
    for i, ln in enumerate(user_lines, 1):
        if ln.strip() == failing:
            out["line"] = i
            out["before"] = [
                user_lines[j] for j in range(max(0, i - 4), i - 1) if user_lines[j].strip()
            ][-3:]
            if i < len(user_lines):
                next_lines = [
                    user_lines[j] for j in range(i, min(len(user_lines), i + 1))
                    if user_lines[j].strip()
                ]
                out["after"] = next_lines[:1]
            break

    return out


def _build_error(
    rc: int,
    error_message: str,
    user_code: str,
    available_varnames: list[str] | None,
) -> ErrorInfo:
    kind = classify_rc(rc)
    short_msg = (
        _last_error_line(error_message) if error_message else ""
    )
    typed = _extract_typed_fields(kind, error_message)
    suggs = suggestions_for(
        kind,
        varname=typed["varname"],
        name=typed["name"],
        command=typed["command"],
        path=typed["path"],
        available_varnames=available_varnames,
    )
    pinpoint = _parse_failure_transcript(error_message, user_code)
    return ErrorInfo(
        kind=kind,
        rc=rc,
        message=short_msg,
        command=pinpoint["command"],
        line=pinpoint["line"],
        context=ErrorContext(
            before=pinpoint["before"],
            failing=pinpoint["failing"],
            after=pinpoint["after"],
        ),
        commands_executed=pinpoint["commands_executed"],
        path=typed["path"],
        varname=typed["varname"],
        name=typed["name"],
        suggestions=suggs,
    )


def _last_error_line(error_text: str) -> str:
    """Extract the most informative line from a Stata error transcript.

    For single-line errors the text is short; we just take the first line.
    For multi-line transcripts the actual error sentence ("variable X not
    found") sits AFTER the last `. <cmd>` echo and BEFORE the `r(NN);` rc
    line. Return that sentence so agents see the diagnosis, not the echoed
    command.
    """
    lines = [ln for ln in error_text.splitlines() if ln]
    if not lines:
        return ""
    if not any(ln.startswith(". ") for ln in lines):
        return lines[0].strip()
    # Walk from bottom: skip rc lines, take next non-rc, non-`.` line.
    for ln in reversed(lines):
        s = ln.strip()
        if not s:
            continue
        if s.startswith("r(") and s.endswith(");"):
            continue
        if ln.startswith(". "):
            continue
        return s
    return lines[0].strip()


# ─────────────────────────────────────────────────────────────────────────────
# Public entrypoint
# ─────────────────────────────────────────────────────────────────────────────


def _resolve_working_dir(
    *,
    origin_path: str | None,
    working_dir: str | None,
    use_origin_workdir: bool,
) -> Path | None:
    if working_dir:
        target = Path(working_dir).expanduser()
    elif origin_path and use_origin_workdir:
        target = Path(origin_path).expanduser().parent
    else:
        return None
    if not target.is_absolute():
        target = target.resolve()
    if not target.is_dir():
        raise ValueError(f"working directory does not exist: {target}")
    return target


def _change_stata_working_dir(rt: Any, directory: Path) -> None:
    """Change Stata's working directory.

    Note: the in-process runner does NOT restore the prior cwd on exit.
    A subsequent ``execute()`` on the same in-process runtime that does
    not pass ``origin_path`` / ``working_dir`` will inherit whatever the
    previous call (or user code) left as ``c(pwd)``. Pool-mode callers
    are immune because each session has its own subprocess. Notebook /
    kernel callers should pass ``origin_path`` on every cell to pin
    relative-path resolution to the source file.
    """
    stata_path = str(directory).replace("\\", "/").replace('"', '""')
    rt.run_suppressed(f'cd "{stata_path}"')


def _persist_graph_artifacts(
    files: LogFileInfo,
    graphs: list[GraphInfo],
) -> tuple[list[GraphInfo], list[str]]:
    if not graphs:
        return graphs, []
    graphs_dir = Path(files.directory) / "graphs"
    graphs_dir.mkdir(parents=True, exist_ok=True)

    updated: list[GraphInfo] = []
    paths: list[str] = []
    for idx, graph in enumerate(graphs, 1):
        payload = _refs.get(graph.ref)
        data = payload.get("bytes") if isinstance(payload, dict) else None
        if not isinstance(data, (bytes, bytearray)):
            updated.append(graph)
            continue
        stem = _safe_file_stem(graph.name) or f"graph-{idx:02d}"
        target = graphs_dir / f"{idx:02d}-{stem}.{graph.format.value}"
        target.write_bytes(bytes(data))
        paths.append(str(target))
        updated.append(graph.model_copy(update={"file_path": str(target)}))
    return updated, paths


def _safe_file_stem(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-")[:64]


@_serialize_stata_state
def execute(
    code: str,
    *,
    session_id: str = "main",
    log_lines_head: int = 20,
    log_lines_tail: int = 20,
    include_full_log: bool = False,
    include_graphs: str = "ref",  # "ref" | "inline" | "none"
    graph_format: str = "png",
    include_dataset_variables: bool = True,
    timeout_ms: int | None = 600_000,  # metadata only here; enforced by _pool
    persist_log_files: bool = False,
    persist_generated_files: bool = True,
    origin_path: str | None = None,
    origin_kind: str | None = None,
    origin_label: str | None = None,
    origin_cell_id: str | None = None,
    use_origin_workdir: bool = True,
    working_dir: str | None = None,
) -> RunResult:
    """Execute Stata code and return a v1.0 RunResult.

    Raises PystataNotAvailable if Stata cannot be initialized.

    Multi-session: `session_id="main"` routes to Stata's master frame
    (`default`); other schema-compatible ids route to same-named Stata
    frames when possible, or to deterministic private frame names when the
    public id is not legal in Stata. Frames isolate **data** (variables and
    observations), but `r()`, `e()`, scalars, and macros remain global
    across frames — agents needing full isolation should use separate
    processes.

    Origin metadata (`origin_path`, `origin_kind`, `origin_label`,
    `origin_cell_id`) is opaque to the execution path. Whatever the caller
    supplies is echoed back in ``result.origin``. The on-disk run-bundle
    manifest also records these fields, but **only when** ``persist_log_files``
    is true *and* ``origin_path`` is provided — supplying ``origin_cell_id``
    alone (no path) yields an echo on the result but no manifest entry.
    """
    if include_graphs not in ("ref", "inline", "none"):
        raise ValueError(
            f"include_graphs must be 'ref' | 'inline' | 'none'; got {include_graphs!r}"
        )
    try:
        gfmt = GraphFormat(graph_format)
    except ValueError as exc:
        raise ValueError(
            f"graph_format must be 'png' | 'svg' | 'pdf'; got {graph_format!r}"
        ) from exc

    rt = get_runtime()  # may raise PystataNotAvailable
    _ensure_session(rt, session_id)

    request_id = _new_request_id()
    started_at = _utc_iso_ms()
    started = time.monotonic()

    if _consume_cancel(session_id):
        return _build_cancelled_result(
            rt=rt,
            session_id=session_id,
            request_id=request_id,
            started_at=started_at,
            started=started,
            include_dataset_variables=include_dataset_variables,
        )

    run_working_dir = _resolve_working_dir(
        origin_path=origin_path,
        working_dir=working_dir,
        use_origin_workdir=use_origin_workdir,
    )
    output_snapshot: FileSnapshot | None = None
    if persist_log_files and persist_generated_files and run_working_dir:
        output_snapshot = snapshot_working_dir_files(
            run_working_dir, origin_path=origin_path
        )
    if run_working_dir:
        _change_stata_working_dir(rt, run_working_dir)

    # Snapshot existing graph names before user code so we can take a delta
    # afterward. This itself calls `graph dir`, which clobbers r(); user code
    # will overwrite r() if they care about return values.
    pre_graphs = (
        _list_graph_names(rt) if include_graphs != "none" else []
    )
    graph_source_hints, unnamed_graph_source_hints = (
        _graph_source_hints(code) if include_graphs != "none" else ({}, [])
    )

    stdout_text, rc, err_msg = rt.run_capture(code)

    elapsed_total_ms = max(1, int((time.monotonic() - started) * 1000))
    # The in-process runner has no IPC overhead to subtract. We still report
    # Stata elapsed time separately so frontends exercise the schema field
    # consistently across in-process and subprocess-backed paths.
    stata_elapsed_ms = elapsed_total_ms

    log = _split_log(
        stdout_text,
        log_lines_head,
        log_lines_tail,
        include_full_log,
        request_id,
    )

    # On Stata error, we still surface results/dataset state — they reflect
    # whatever state existed before the failing command (per SCHEMA §3.7
    # commands_executed semantics).
    results = ResultsInfo(
        r=_collect_returns(rt, "r", request_id),
        e=_collect_returns(rt, "e", request_id),
        last_estimation_cmd=_last_estimation_cmd(rt),
    )
    dataset = _collect_dataset(rt, include_dataset_variables)

    available_varnames = (
        [v.name for v in dataset.variables] if dataset.variables else None
    )

    if err_msg is not None:
        error = _build_error(rc, err_msg, code, available_varnames)
        # Build an error_window: prefer log tail; fall back to the error message
        # itself when the log is empty (pystata can raise before any stdout
        # gets flushed for short failures).
        log_lines = [
            ln for ln in stdout_text.replace("\r\n", "\n").split("\n") if ln
        ]
        if log_lines:
            tail_n = min(len(log_lines), 10)
            error_window = "\n".join(log_lines[-tail_n:])
        else:
            error_window = err_msg.strip()
        log = LogInfo(
            head=log.head,
            tail=log.tail,
            lines_total=log.lines_total,
            bytes_total=log.bytes_total,
            truncated=log.truncated,
            complete=log.complete,
            error_window=error_window,
            ref=log.ref,
        )
    else:
        error = None

    stata_info = _stata_info(rt)

    # Graph capture happens AFTER r/e collection so that `graph dir` /
    # `graph display` / `graph export` (all r-class) don't clobber user r().
    if include_graphs != "none":
        graphs = _collect_graphs(
            rt,
            request_id=request_id,
            pre_existing=pre_graphs,
            fmt=gfmt,
            inline=(include_graphs == "inline"),
            source_hints=graph_source_hints,
            unnamed_source_hints=unnamed_graph_source_hints,
        )
    else:
        graphs = []

    capabilities = ["log_truncation", "matrix_ref", "multi_session"]
    if include_graphs != "none":
        capabilities.append("graph_ref")
    if include_graphs == "inline":
        capabilities.append("inline_graphs")

    top_rc = rc if error is not None else 0
    ok = error is None and rc == 0
    warnings = _extract_warnings(stdout_text)

    if persist_log_files and origin_path:
        try:
            generated_files = (
                changed_output_files(
                    output_snapshot,
                    run_working_dir,
                    origin_path=origin_path,
                )
                if persist_generated_files and output_snapshot is not None and run_working_dir
                else []
            )
            files = persist_run_log_files(
                log_text=stdout_text,
                code=code,
                origin_path=origin_path,
                origin_kind=origin_kind,
                origin_label=origin_label,
                origin_cell_id=origin_cell_id,
                request_id=request_id,
                session_id=session_id,
                started_at=started_at,
                elapsed_ms=elapsed_total_ms,
                rc=top_rc,
                ok=ok,
                stata=stata_info,
                working_dir=str(run_working_dir) if run_working_dir else None,
            )
            graphs, graph_paths = _persist_graph_artifacts(files, graphs)
            files = files.model_copy(update={"graph_paths": graph_paths})
            if graph_paths:
                files = files.model_copy(
                    update={"graphs_dir": str(Path(files.directory) / "graphs")}
                )
            if generated_files and run_working_dir:
                files = copy_output_artifacts(
                    files,
                    generated_files,
                    working_dir=run_working_dir,
                )
            update_run_artifact_manifest(files)
            log = log.model_copy(update={"files": files})
            capabilities.append("log_files")
            if graph_paths or files.output_paths:
                capabilities.append("run_artifacts")
        except Exception as exc:  # noqa: BLE001
            warnings.append(
                StataWarning(
                    kind="log_files",
                    message=f"Could not write log-files artifacts: {exc}",
                )
            )

    origin_echo = (
        OriginInfo(
            path=origin_path,
            kind=origin_kind,
            label=origin_label,
            cell_id=origin_cell_id,
        )
        if any(v is not None for v in (origin_path, origin_kind, origin_label, origin_cell_id))
        else None
    )

    return RunResult(
        ok=ok,
        rc=top_rc,
        session_id=session_id,
        request_id=request_id,
        started_at=started_at,
        elapsed_ms=elapsed_total_ms,
        stata_elapsed_ms=stata_elapsed_ms,
        stata=stata_info,
        log=log,
        results=results,
        dataset=dataset,
        graphs=graphs,
        warnings=warnings,
        error=error,
        origin=origin_echo,
        capabilities=capabilities,
    )


def _last_estimation_cmd(rt: Any) -> str | None:
    """Mirror e(cmd) for callers; returns None if no estimation has run."""
    try:
        v = rt.sfi.Macro.getGlobal("e(cmd)")
        return v or None
    except Exception:  # noqa: BLE001
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Multi-session via Stata frames (Module 4)
# ─────────────────────────────────────────────────────────────────────────────


_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_STATA_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MAPPED_FRAME_PREFIX = "_sc_"
_session_frame_map: dict[str, str] = {}
_frame_session_map: dict[str, str] = {}


def _frame_for_session(session_id: str) -> str:
    """Map a session_id to a Stata frame name.

    ``"main"`` maps to Stata's master frame ``"default"``. Public session
    ids follow the schema pattern ``[A-Za-z0-9_-]+``; ids that are not legal
    Stata frame names are routed through a deterministic private frame name.
    """
    if session_id == "main":
        return "default"
    if not _SESSION_ID_RE.fullmatch(session_id):
        raise ValueError(
            f"session_id must match [A-Za-z0-9_-]+; got {session_id!r}. "
            "':' is reserved for future remote prefixing."
        )
    if _STATA_NAME_RE.fullmatch(session_id) and not session_id.startswith(
        _MAPPED_FRAME_PREFIX
    ):
        return session_id
    mapped = _session_frame_map.get(session_id)
    if mapped is None:
        digest = hashlib.sha1(session_id.encode("utf-8")).hexdigest()[:24]
        mapped = f"{_MAPPED_FRAME_PREFIX}{digest}"
        _session_frame_map[session_id] = mapped
        _frame_session_map[mapped] = session_id
    return mapped


def _session_for_frame(frame_name: str) -> str:
    if frame_name == "default":
        return "main"
    return _frame_session_map.get(frame_name, frame_name)


def _list_frame_names(rt: Any) -> list[str]:
    Frame = rt.sfi.Frame
    n = Frame.getFrameCount()
    return [Frame.getFrameAt(i) for i in range(n)]


def _ensure_session(rt: Any, session_id: str) -> None:
    """Switch to the frame for `session_id`, creating it if it does not exist."""
    target = _frame_for_session(session_id)
    existing = _list_frame_names(rt)
    if target not in existing:
        rt.run_suppressed(f"frame create {target}")
    # Switch (no-op if already on it; cheap)
    rt.run_suppressed(f"frame change {target}")


@_serialize_stata_state
def list_sessions() -> list[dict[str, Any]]:
    """Auxiliary tool: enumerate live sessions (mapped from Stata frames)."""
    try:
        rt = get_runtime()
    except PystataNotAvailable:
        return []
    sessions: list[dict[str, Any]] = []
    for fname in _list_frame_names(rt):
        sid = _session_for_frame(fname)
        # n_obs from each frame; switching is needed since Frame helpers
        # operate on the current working frame for getObsTotal indirectly.
        # Easier: query c(N) after switching.
        rt.run_suppressed(f"frame change {fname}")
        n_obs = int(rt.sfi.Data.getObsTotal())
        sessions.append({"session_id": sid, "frame": fname, "n_obs": n_obs})
    return sessions


@_serialize_stata_state
def reset_session(session_id: str = "main") -> dict[str, Any]:
    """Auxiliary tool: drop a session's data (and its frame, except `main`).

    `main` cannot be dropped — it maps to Stata's master `default` frame.
    For `main`, this performs `clear all` to wipe data in place.
    """
    rt = get_runtime()
    target = _frame_for_session(session_id)
    if session_id == "main":
        # Switch in, clear, return
        rt.run_suppressed("frame change default")
        rt.run_suppressed("clear all")
        return {"session_id": "main", "dropped_frame": False}
    # Drop a non-main frame. Must switch off it first.
    rt.run_suppressed("frame change default")
    rt.run_suppressed(f"capture frame drop {target}")
    # Refs use `log://<request_id>` / `graph://<request_id>/<idx>` — no
    # session prefix to clear. The LRU evicts naturally as the producer
    # generates new requests. If session-scoped purge is ever needed,
    # re-key refs as `log://<session_id>/<request_id>` first.
    return {"session_id": session_id, "dropped_frame": True}


# ─────────────────────────────────────────────────────────────────────────────
# Warning extraction (Module 3)
# ─────────────────────────────────────────────────────────────────────────────


# Patterns are ordered: more specific kinds first. Each pattern produces one
# warning per match (de-duped at the schema level).
_WARNING_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # Stata's "omitted because of collinearity" note — shows up under
    # `regress`, `logit`, etc. when factor levels or duplicate vars are
    # dropped from the design matrix.
    (
        "omitted_collinear",
        re.compile(
            r"note:\s+(.+?)\s+omitted because of collinearity\.?",
            re.IGNORECASE,
        ),
    ),
    # Convergence not achieved (MLE-family commands)
    (
        "convergence",
        re.compile(
            r"convergence (?:not achieved|not reached|failed)", re.IGNORECASE
        ),
    ),
    # Matrix not pos. def. / singular — typically reported in MLE diagnostics
    (
        "singular",
        re.compile(
            r"(?:matrix\s+)?(?:not symmetric|not positive definite|"
            r"is\s+singular)",
            re.IGNORECASE,
        ),
    ),
    # Boundary / could-not-find-feasible — softer than rc 491
    (
        "boundary",
        re.compile(r"could not find feasible (?:starting )?values", re.IGNORECASE),
    ),
)

# Generic Stata "note:" lines that don't match a more specific pattern.
_NOTE_RE = re.compile(r"^\s*note:\s*(.+?)\s*$", re.MULTILINE)


def _extract_warnings(log: str) -> list:  # list[StataWarning]
    """Scan the captured log for known Stata warning patterns.

    Returns a list of StataWarning entries. De-duplicated at the schema layer
    by `(kind, message)`.
    """
    from stata_code.core.schema import StataWarning

    out: list = []
    seen: set[tuple[str, str]] = set()
    matched_spans: list[tuple[int, int]] = []

    for kind, pat in _WARNING_PATTERNS:
        for m in pat.finditer(log):
            msg = m.group(0).strip()
            key = (kind, msg)
            if key in seen:
                continue
            seen.add(key)
            matched_spans.append(m.span())
            out.append(StataWarning(kind=kind, message=msg))

    # Generic notes: any `note: ...` line not already matched by a specific
    # pattern. Avoid double-counting.
    for m in _NOTE_RE.finditer(log):
        if any(s <= m.start() < e for s, e in matched_spans):
            continue
        msg = m.group(0).strip()
        key = ("note", msg)
        if key in seen:
            continue
        seen.add(key)
        out.append(StataWarning(kind="note", message=msg))

    return out


# ─────────────────────────────────────────────────────────────────────────────
# Graph capture (Module 1)
# ─────────────────────────────────────────────────────────────────────────────


_GRAPH_NAME_RE = re.compile(r"\bname\(\s*([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE)
_GRAPH_COMMAND_RE = re.compile(
    r"^\s*(?:"
    r"graph\s+\w+|"
    r"twoway|scatter|line|connected|histogram|kdensity|lowess|lfit|qfit|"
    r"coefplot|binscatter"
    r")\b",
    re.IGNORECASE,
)
# Commands that *create or redraw* a graph (as opposed to graph-management
# subcommands like `graph dir`, `graph export`, `graph drop`). Used to decide
# whether a cell touched the default-named "Graph", which Stata reuses for every
# unnamed plot — so the delta-by-name snapshot alone cannot tell that a later
# cell redrew it. See `_collect_graphs`.
_GRAPH_CREATING_RE = re.compile(
    r"^\s*(?:"
    r"graph\s+(?:bar|hbar|box|hbox|pie|dot|matrix|twoway|combine)\b|"
    r"twoway|scatter|line|connected|histogram|hist|kdensity|lowess|lfit|qfit|"
    r"coefplot|binscatter"
    r")\b",
    re.IGNORECASE,
)

# Stata's default name for any graph drawn without an explicit `name(...)`.
_DEFAULT_GRAPH_NAME = "Graph"


def _graph_source_hints(code: str) -> tuple[dict[str, tuple[str, int]], list[tuple[str, int]]]:
    """Best-effort map from graph names to the submitted source line.

    Stata does not expose a graph's source command after creation, so this is
    intentionally heuristic. Named graphs are reliable because the `name(...)`
    option is echoed in the user code. Unnamed graph commands are retained in
    order and only attached when there is exactly one new graph, or when the
    number of unnamed hints matches the number of otherwise-unattributed new
    graphs.
    """
    named: dict[str, tuple[str, int]] = {}
    unnamed: list[tuple[str, int]] = []
    for line_no, raw in enumerate(code.splitlines(), start=1):
        stripped = raw.strip()
        if not stripped or stripped.startswith(("*", "//")):
            continue
        if not _GRAPH_COMMAND_RE.search(stripped):
            continue
        name_match = _GRAPH_NAME_RE.search(stripped)
        if name_match:
            named[name_match.group(1)] = (stripped, line_no)
        else:
            unnamed.append((stripped, line_no))
    return named, unnamed


def _png_dimensions(data: bytes) -> tuple[int | None, int | None]:
    """Best-effort width/height from a PNG IHDR chunk."""
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        return None, None
    return (
        int.from_bytes(data[16:20], "big"),
        int.from_bytes(data[20:24], "big"),
    )


def _list_graph_names(rt: Any) -> list[str]:
    """Run `graph dir` (silently) and return current in-memory graph names."""
    try:
        _stdout, rc, _err = rt.run_capture("graph dir")
        if rc != 0:
            return []
        raw = rt.sfi.SFIToolkit.macroExpand("`r(list)'") or ""
        return raw.split()
    except Exception:  # noqa: BLE001
        return []


def _collect_graphs(
    rt: Any,
    request_id: str,
    pre_existing: list[str],
    fmt: GraphFormat,
    inline: bool,
    source_hints: dict[str, tuple[str, int]] | None = None,
    unnamed_source_hints: list[tuple[str, int]] | None = None,
) -> list[GraphInfo]:
    """Capture graphs that user code newly created or redrew.

    Strategy: snapshot graph names before user code (`pre_existing`), call
    after to find the post-existing list, take the set difference. For each
    new graph: `graph display <name>` (makes it active), `graph export` to a
    tmpfile, read bytes, store under a ref. Tmpfile is deleted after.

    The set difference alone misses graphs the cell *redrew* under a name that
    already existed — most importantly Stata's default ``Graph``, which every
    unnamed plot command reuses. Without this, a notebook only ever shows the
    first cell's plot: the second cell redraws ``Graph`` in place, the name is
    unchanged, and the delta is empty. So we also capture any graph this cell's
    code explicitly targeted (a ``name(...)`` it drew, or ``Graph`` when the
    cell ran an unnamed plotting command).
    """
    source_hints = source_hints or {}
    unnamed_source_hints = unnamed_source_hints or []

    after_names = _list_graph_names(rt)
    # Names this cell's code explicitly (re)drew, regardless of whether they
    # pre-existed in memory.
    targeted: set[str] = set(source_hints)
    if any(_GRAPH_CREATING_RE.search(src) for src, _line in unnamed_source_hints):
        targeted.add(_DEFAULT_GRAPH_NAME)
    capture_names = [
        n for n in after_names if n not in pre_existing or n in targeted
    ]
    if not capture_names:
        return []
    new_names = capture_names
    unattributed_names = [n for n in new_names if n not in source_hints]
    unnamed_by_graph: dict[str, tuple[str, int]] = {}
    if len(unattributed_names) == len(unnamed_source_hints):
        unnamed_by_graph = dict(zip(unattributed_names, unnamed_source_hints, strict=False))

    fmt_str = fmt.value
    out: list[GraphInfo] = []
    tmpdir = Path(tempfile.mkdtemp(prefix="stata_code_graph_"))
    try:
        for idx, gname in enumerate(new_names):
            target = tmpdir / f"{idx}.{fmt_str}"
            try:
                rt.run_suppressed(f"graph display {gname}")
                rt.run_suppressed(
                    f'graph export "{target}", as({fmt_str}) replace'
                )
            except SystemError:
                # Stata refused — skip this graph (e.g., window not found)
                continue
            if not target.exists():
                continue
            data = target.read_bytes()
            ref = f"graph://{request_id}/{idx}"
            width = height = None
            if fmt == GraphFormat.PNG:
                width, height = _png_dimensions(data)
            _refs.put(
                ref,
                {
                    "format": fmt_str,
                    "bytes": data,
                    "width": width,
                    "height": height,
                },
            )
            source = source_hints.get(gname) or unnamed_by_graph.get(gname)
            out.append(
                GraphInfo(
                    ref=ref,
                    name=gname,
                    format=fmt,
                    width=width,
                    height=height,
                    source_command=source[0] if source else None,
                    source_line=source[1] if source else None,
                    inline=_b64(data) if inline else None,
                )
            )
    finally:
        try:
            for f in tmpdir.iterdir():
                try:
                    f.unlink()
                except OSError:
                    pass
            tmpdir.rmdir()
        except OSError:
            pass

    return out


def _b64(data: bytes) -> str:
    import base64

    return base64.b64encode(data).decode("ascii")


def get_graph(ref: str, format: str | None = None) -> dict[str, Any]:
    """Auxiliary tool: fetch a graph's bytes and dimensions by ref.

    Per SCHEMA.md §5. Returns a dict with `format`, `bytes_b64`, `width`,
    `height`. Raises :class:`RefNotFound` (a ``KeyError`` subclass) when
    the ref is unknown (expired, never existed, or session reset).
    """
    payload = _refs.get(ref)
    if payload is None:
        raise RefNotFound(ref, kind="unknown_graph_ref")
    return {
        "format": payload["format"],
        "bytes_b64": _b64(payload["bytes"]),
        "width": payload["width"],
        "height": payload["height"],
    }


def get_matrix(ref: str) -> dict[str, Any]:
    """Auxiliary tool: fetch a matrix's values, rows, cols by ref.

    Per SCHEMA.md §5. Used when ``run()`` returns a Matrix with ``values=None``
    and a ``matrix://...`` ref because the matrix exceeded the inline cell
    cap (``MATRIX_INLINE_CELL_CAP`` = 10,000 cells by default). Returns a
    dict with ``rows``, ``cols``, ``values``. Raises :class:`RefNotFound`
    (a ``KeyError`` subclass) when the ref is unknown.
    """
    payload = _refs.get(ref)
    if payload is None:
        raise RefNotFound(ref, kind="unknown_matrix_ref")
    return {
        "rows": payload["rows"],
        "cols": payload["cols"],
        "values": payload["values"],
    }
