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
import math
import re
import tempfile
import threading
import time
import uuid
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ParamSpec, TypeVar

from stata_code.core import _refs
from stata_code.core._runtime import PystataNotAvailable, get_runtime
from stata_code.core.errors import (
    classify_rc,
    label_for_rc,
    recovery_for,
    suggestions_for,
)
from stata_code.core.estimation import build_estimation_result, trim_estimation
from stata_code.core.log_artifacts import (
    MAX_SNAPSHOT_ENTRIES,
    FileSnapshot,
    changed_output_files,
    copy_output_artifacts,
    describe_output_files,
    persist_run_log_files,
    snapshot_is_truncated,
    snapshot_working_dir_files,
    update_run_artifact_manifest,
)
from stata_code.core.policy import check as policy_check
from stata_code.core.schema import (
    Backend,
    DatasetInfo,
    ErrorContext,
    ErrorInfo,
    ErrorKind,
    GraphFormat,
    GraphInfo,
    IncludeEstimation,
    IncludeResults,
    LogFileInfo,
    LogInfo,
    Matrix,
    OriginInfo,
    OutputFile,
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
_FILE_PATH_RE = re.compile(r"file\s+(\S+?)\s+(?:not\s+found|already\s+exists|could\s+not)")
_NAME_CONFLICT_RE = re.compile(r"(\w+)\s+already\s+(?:defined|exists)")
# Stata's actual rc 199 message is "command <X> is unrecognized" (verified
# against the live runtime and the [P] error manual). The second alternative is
# a defensive fallback for any "<X> unrecognized command" phrasing. The command
# token is whichever group matched (see _extract_typed_fields).
_UNRECOGNIZED_CMD_RE = re.compile(
    r"command\s+(\S+)\s+is\s+unrecognized|(\S+)\s+unrecognized\s+command"
)

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

# Longest macro value put on the wire verbatim. Stata macros are mostly short
# (`e(cmd)`, `e(depvar)`, `e(vcetype)`), but a few are enormous and carry
# nothing an agent can act on — `e(rngstate)` alone is ~2 KB of hex on every
# bootstrap / permute / simulate run. Anything longer is elided with an
# explicit marker rather than dropped, so the name still shows up.
MACRO_INLINE_CHAR_CAP = 256

# Stata's system missing `.` is exactly maxdouble (2**1023); the 26 extended
# missings `.a`–`.z` occupy the next representable doubles above it. `sfi`
# hands all of them back as ordinary Python floats, so any value at or above
# this threshold is a missing marker rather than a number. Without the guard,
# an omitted base level's standard error arrives on the wire as
# 8.98846567431158e+307 — a number an agent will happily format into a table.
_STATA_MISSING_MIN = 2.0**1023

# Matrices the estimation contract is built from. When the caller suppresses
# `results` but still wants `estimation`, these are the only matrices worth
# reading out of Stata.
_ESTIMATION_MATRICES: dict[str, frozenset[str]] = {
    "e": frozenset({"b", "V"}),
    "r": frozenset({"table"}),
}


def _norm_stata_number(value: Any) -> float | None:
    """Coerce an sfi numeric to a float, mapping Stata missings to ``None``.

    Applies to every number that crosses the wire — r()/e() scalars and every
    matrix cell — so SCHEMA.md §3.4's "system missing (`.`) → JSON `null`"
    holds uniformly instead of only for scalars that sfi happened to return as
    ``None``.
    """
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f) or f >= _STATA_MISSING_MIN:
        return None
    return f


def _refs_matrix_values(ref: str) -> list[list[float | None]] | None:
    """Resolve a ``matrix://`` ref back to its values for the estimation builder."""
    payload = _refs.get(ref)
    if not isinstance(payload, dict):
        return None
    values = payload.get("values")
    return values if isinstance(values, list) else None


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


def search_log(
    ref: str,
    pattern: str,
    *,
    is_regex: bool = False,
    ignore_case: bool = True,
    context: int = 0,
    max_matches: int = 50,
) -> dict[str, Any]:
    """Auxiliary tool: grep within a stored ``log://`` payload.

    Pairs with the token-economy default of returning long logs by
    reference: instead of pulling the whole log back with
    :func:`get_log`, the agent can find just the lines it cares about.

    Parameters
    ----------
    ref : str
        A ``log://<request_id>`` ref produced by a truncated ``stata_run``.
    pattern : str
        Substring (default) or regular expression (``is_regex=True``) to
        match against each line.
    is_regex : bool
        Treat ``pattern`` as a Python regular expression. A malformed
        regex raises :class:`ValueError` (surfaced as ``invalid_request``).
    ignore_case : bool
        Case-insensitive matching (default ``True``).
    context : int
        Lines of surrounding context to include on each side of a match
        (capped at 10). ``before`` / ``after`` are omitted when 0.
    max_matches : int
        Stop after this many matches; ``truncated`` reports whether more
        existed (capped at 1000).

    Returns
    -------
    dict
        ``{ref, pattern, is_regex, lines_total, match_count, truncated,
        matches: [{line_no, text, before?, after?}]}``. ``line_no`` is
        1-based. Raises :class:`RefNotFound` for an unknown ref.
    """
    payload = _refs.get(ref)
    if (
        not isinstance(payload, dict)
        or not isinstance(payload.get("text"), str)
        or "lines_total" not in payload
    ):
        raise RefNotFound(ref, kind="unknown_log_ref")
    if not pattern:
        raise ValueError("pattern must be a non-empty string")

    context = max(0, min(int(context), 10))
    max_matches = max(1, min(int(max_matches), 1000))

    flags = re.IGNORECASE if ignore_case else 0
    if is_regex:
        try:
            matcher = re.compile(pattern, flags)
        except re.error as exc:
            raise ValueError(f"invalid regex: {exc}") from exc

        def _hit(line: str) -> bool:
            return matcher.search(line) is not None
    else:
        needle = pattern.lower() if ignore_case else pattern

        def _hit(line: str) -> bool:
            hay = line.lower() if ignore_case else line
            return needle in hay

    text: str = payload["text"]
    lines = text.split("\n")
    matches: list[dict[str, Any]] = []
    truncated = False
    for idx, line in enumerate(lines):
        if not _hit(line):
            continue
        if len(matches) >= max_matches:
            truncated = True
            break
        entry: dict[str, Any] = {"line_no": idx + 1, "text": line}
        if context:
            before = lines[max(0, idx - context) : idx]
            after = lines[idx + 1 : idx + 1 + context]
            if before:
                entry["before"] = before
            if after:
                entry["after"] = after
        matches.append(entry)

    return {
        "ref": ref,
        "pattern": pattern,
        "is_regex": is_regex,
        "lines_total": payload["lines_total"],
        "match_count": len(matches),
        "truncated": truncated,
        "matches": matches,
    }


def cancel(session_id: str = "main") -> bool:
    """Request cancellation of the next ``execute()`` call for ``session_id``.

    Cooperative: does **not** interrupt code that is currently mid-execution
    inside pystata. The flag is consumed (and the run short-circuited)
    when ``execute(session_id=...)`` is next invoked for the same session.
    The short-circuit returns a ``RunResult`` with ``ok=False``, ``rc=-3``
    (synthetic), and ``error.kind=cancelled``.

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
            head="",
            tail="",
            lines_total=0,
            bytes_total=0,
            truncated=False,
            complete=True,
            ref=None,
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
                f"Execution cancelled before Stata received the code (session_id={session_id!r})."
            ),
            command=None,
            line=None,
            context=ErrorContext(before=[], failing="", after=[]),
            commands_executed=0,
            path=None,
            varname=None,
            name=None,
            suggestions=[],
            recovery=recovery_for(ErrorKind.CANCELLED),
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


def _collect_returns(
    rt: Any,
    prefix: str,
    *,
    want_named: bool = True,
    matrix_names: Iterable[str] | None = None,
) -> StataReturns:
    """Read r() or e() out of Stata using sfi for typed access.

    Matrices come back fully inline — this function never creates a ref.
    Deciding what actually goes on the wire (inline / ref / stub) is
    :func:`_project_returns`' job, so that the estimation contract can always
    be built from complete values regardless of the caller's payload budget.

    ``want_named`` false suppresses scalars and macros — used when the caller
    asked for ``include_results="none"`` but still wants ``estimation``.
    ``matrix_names`` restricts which matrices are read at all; ``None`` reads
    every matrix Stata reports.

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
    macros: dict[str, str] = {}
    if want_named:
        for name in names["scalars"]:
            try:
                scalars[name] = _norm_stata_number(sfi.Scalar.getValue(f"{prefix}({name})"))
            except Exception:  # noqa: BLE001
                scalars[name] = None

        for name in names["macros"]:
            try:
                v = sfi.Macro.getGlobal(f"{prefix}({name})")
                macros[name] = v if v is not None else ""
            except Exception:  # noqa: BLE001
                macros[name] = ""

    wanted = None if matrix_names is None else frozenset(matrix_names)
    matrices: dict[str, Matrix] = {}
    for name in names["matrices"]:
        if wanted is not None and name not in wanted:
            continue
        key = f"{prefix}({name})"
        try:
            values = sfi.Matrix.get(key)
            rows = list(sfi.Matrix.getRowNames(key) or [])
            cols = list(sfi.Matrix.getColNames(key) or [])
            norm_values: list[list[float | None]] = [
                [_norm_stata_number(v) for v in row] for row in values
            ]
            n_rows = len(norm_values)
            n_cols = len(norm_values[0]) if n_rows else 0
            # sfi can return None/short name lists for a non-empty matrix;
            # synthesize positional names rather than letting the Matrix shape
            # validator reject (and silently drop) successfully read values.
            if len(rows) != n_rows:
                rows = [f"r{i + 1}" for i in range(n_rows)]
            if len(cols) != n_cols:
                cols = [f"c{j + 1}" for j in range(n_cols)]
            matrices[name] = Matrix(
                rows=rows,
                cols=cols,
                values=norm_values,
                ref=None,
                n_rows=n_rows,
                n_cols=n_cols,
            )
        except Exception:  # noqa: BLE001
            continue

    return StataReturns(scalars=scalars, macros=macros, matrices=matrices)


def _project_returns(
    rv: StataReturns,
    *,
    prefix: str,
    request_id: str,
    mode: IncludeResults,
) -> StataReturns:
    """Reduce collected returns to the representation the caller asked for.

    * ``full`` — matrices inline, except those past ``MATRIX_INLINE_CELL_CAP``
      cells, which become ``matrix://`` refs (the pre-existing behaviour).
    * ``scalars`` — scalars and macros inline; *every* matrix becomes a ref
      stub carrying only its shape. This is the default because a single
      estimation typically encodes the same numbers four times over
      (``e(b)``, ``e(V)``, ``e(beta)``, ``r(table)``) while
      ``results.estimation`` already carries the typed, deduplicated view.
    * ``none`` — nothing; the caller wants only the log / dataset / estimation.

    In ``scalars`` mode the row and column labels are dropped along with the
    values: for a 141-term model those two label lists alone run to several
    kilobytes. ``full`` mode keeps them next to the ref, as SCHEMA.md §3.4
    describes. Either way ``get_matrix(ref)`` returns labels and values.
    """
    if mode == IncludeResults.NONE:
        return StataReturns(scalars={}, macros={}, matrices={})

    matrices: dict[str, Matrix] = {}
    for name, m in rv.matrices.items():
        values = m.values
        n_rows = m.n_rows if m.n_rows is not None else len(values or [])
        n_cols = m.n_cols if m.n_cols is not None else (len(values[0]) if values else 0)
        elide_labels = mode == IncludeResults.SCALARS
        stub = elide_labels or (n_rows * n_cols) > MATRIX_INLINE_CELL_CAP
        if not stub or values is None:
            matrices[name] = m
            continue
        ref = f"matrix://{request_id}/{prefix}/{name}"
        _refs.put(ref, {"rows": m.rows, "cols": m.cols, "values": values})
        matrices[name] = Matrix(
            rows=[] if elide_labels else m.rows,
            cols=[] if elide_labels else m.cols,
            values=None,
            ref=ref,
            n_rows=n_rows,
            n_cols=n_cols,
        )
    return StataReturns(
        scalars=rv.scalars,
        macros={name: _cap_macro(v) for name, v in rv.macros.items()},
        matrices=matrices,
    )


def _cap_macro(value: str) -> str:
    """Elide a macro value too long to be worth its tokens on the wire.

    Applied only at projection time — the estimation contract is built from the
    uncapped values, so nothing downstream loses precision.
    """
    if len(value) <= MACRO_INLINE_CHAR_CAP:
        return value
    dropped = len(value) - MACRO_INLINE_CHAR_CAP
    return f"{value[:MACRO_INLINE_CHAR_CAP]}… ({dropped} more chars elided)"


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
            fields["command"] = m.group(1) or m.group(2)
    return fields


def _parse_failure_transcript(
    error_text: str,
    user_code: str,
    *,
    working_dir: Path | None = None,
) -> dict[str, Any]:
    """Pinpoint the failing command in multi-line user code.

    pystata's SystemError for multi-line input contains the full Stata
    transcript with `. <cmd>` echoes for each line. We parse it to recover:

    - `failing`: the failing command's text (or "" if not isolatable)
    - `line`: 1-indexed line in the original user code (or None)
    - `source_file`: the `do`/`run` script `line` indexes into, when the
      failure happened inside one rather than in the submitted code
    - `commands_executed`: count of *non-comment* commands that completed
      successfully before the failure (or None)
    - `before` / `after`: surrounding lines (up to 3 / 1)
    """
    out: dict[str, Any] = {
        "failing": "",
        "line": None,
        "source_file": None,
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

    # Multi-line case — parse `. <cmd>` lines. `head_echoes` keeps the first
    # PHYSICAL line of each command; `cmd_echoes` keeps the logical command
    # with Stata's `> ` continuation fragments folded back in, so a command
    # broken across lines with `///` is reported whole.
    head_echoes, cmd_echoes = _transcript_command_echoes(error_text)

    if not cmd_echoes:
        return out

    failing = cmd_echoes[-1]
    failing_head = head_echoes[-1]
    out["failing"] = failing
    out["command"] = failing
    out["commands_executed"] = len(cmd_echoes) - 1

    # Match against original user code lines (with blanks) for line number.
    located = _locate_in_lines(user_lines, failing, failing_head)
    if located is not None:
        out.update(located)
        return out

    # Not in the submitted code — the failure happened inside a script the
    # submitted code invoked (`do "analysis.do"`). This is the common shape for
    # agent workflows, and it used to yield `line: null` with empty context.
    for candidate in _do_file_candidates(user_code, working_dir):
        try:
            source_lines = candidate.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        located = _locate_in_lines(source_lines, failing, failing_head)
        if located is not None:
            out.update(located)
            out["source_file"] = str(candidate)
            return out

    return out


# Stata renders a command that wraps (or was continued with `///`) by prefixing
# the continuation fragments with "> ".
_CONTINUATION_PREFIX = "> "


def _transcript_command_echoes(error_text: str) -> tuple[list[str], list[str]]:
    """Split a Stata transcript into (first-physical-line, logical) commands."""
    heads: list[str] = []
    logical: list[str] = []
    in_echo = False
    for ln in error_text.split("\n"):
        if ln.startswith(". "):
            body = ln[2:].strip()
            in_echo = False
            if not body:
                continue  # empty `. ` is just a prompt
            if body.startswith("*") or body.startswith("//"):
                continue  # comment-only line — Stata echoes but doesn't "run"
            heads.append(body)
            logical.append(body)
            in_echo = True
            continue
        if in_echo and ln.startswith(_CONTINUATION_PREFIX):
            logical[-1] = _join_continuation(logical[-1], ln[len(_CONTINUATION_PREFIX) :].strip())
            continue
        in_echo = False
    return heads, logical


def _join_continuation(head: str, tail: str) -> str:
    """Splice a `> ` continuation fragment onto the command it continues.

    A `///` comment marker ends the physical line, so drop it before joining;
    otherwise Stata simply wrapped a long line and the pieces abut directly.
    """
    base = head[: -len("///")].rstrip() if head.rstrip().endswith("///") else head
    if head.rstrip().endswith("///"):
        return f"{base} {tail}".strip()
    return f"{base}{tail}"


def _locate_in_lines(
    lines: list[str],
    failing: str,
    failing_head: str,
) -> dict[str, Any] | None:
    """Find `failing` among `lines`; return line number and surrounding context.

    Tries the logical command first, then the first physical line (the form a
    `///`-continued command actually has on disk), then a whitespace-normalized
    comparison. Returns ``None`` when the command is not in these lines at all.
    """
    needles = [n for n in (failing, failing_head) if n]
    normalized = {_squeeze_ws(n) for n in needles}
    for i, raw in enumerate(lines, 1):
        stripped = raw.strip()
        if stripped not in needles and _squeeze_ws(stripped) not in normalized:
            continue
        before = [lines[j] for j in range(max(0, i - 4), i - 1) if lines[j].strip()][-3:]
        after: list[str] = []
        if i < len(lines):
            after = [lines[j] for j in range(i, min(len(lines), i + 1)) if lines[j].strip()][:1]
        return {"line": i, "before": before, "after": after}
    return None


def _squeeze_ws(text: str) -> str:
    return " ".join(text.split())


# `[qui|cap|noi] do|run "path"` — the prefixes Stata allows in front of a
# script invocation. Unquoted paths stop at whitespace or a comma (the option
# separator), matching Stata's own parsing.
_DO_INVOCATION_RE = re.compile(
    r"""^\s*
        (?:(?:qui(?:etly)?|cap(?:ture)?|noi(?:sily)?)\s+)*
        (?:do|run)\s+
        (?:"(?P<quoted>[^"]+)"|(?P<bare>[^\s,]+))
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _do_file_candidates(user_code: str, working_dir: Path | None) -> list[Path]:
    """Resolve every `do`/`run` script the submitted code invokes.

    Relative paths resolve against the run's working directory — the same
    directory Stata itself resolved them against. Stata appends a default
    `.do` extension, so a bare `do analysis` is checked as `analysis.do` too.
    """
    out: list[Path] = []
    seen: set[str] = set()
    for raw in user_code.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith(("*", "//")):
            continue
        m = _DO_INVOCATION_RE.match(stripped)
        if m is None:
            continue
        target = m.group("quoted") or m.group("bare")
        base = Path(target).expanduser()
        variants = [base] if base.suffix else [base, base.with_suffix(".do")]
        for variant in variants:
            path = variant if variant.is_absolute() else ((working_dir or Path.cwd()) / variant)
            key = str(path)
            if key in seen:
                continue
            seen.add(key)
            if path.is_file():
                out.append(path)
    return out


def _build_error(
    rc: int,
    error_message: str,
    user_code: str,
    available_varnames: list[str] | None,
    *,
    working_dir: Path | None = None,
) -> ErrorInfo:
    kind = classify_rc(rc)
    short_msg = _last_error_line(error_message) if error_message else ""
    # Classification reads the raw transcript, but the typed-field regexes
    # should see the diagnosis sentence we just isolated as well, so a message
    # buried above Stata's `end of do-file` boilerplate still yields a varname.
    typed = _extract_typed_fields(kind, error_message)
    suggs = suggestions_for(
        kind,
        rc=rc,
        varname=typed["varname"],
        name=typed["name"],
        command=typed["command"],
        path=typed["path"],
        available_varnames=available_varnames,
    )
    pinpoint = _parse_failure_transcript(error_message, user_code, working_dir=working_dir)
    return ErrorInfo(
        kind=kind,
        rc=rc,
        rc_label=label_for_rc(rc),
        message=short_msg,
        command=pinpoint["command"],
        line=pinpoint["line"],
        source_file=pinpoint["source_file"],
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
        recovery=recovery_for(kind),
    )


# Structural lines Stata prints around a failure that carry no diagnosis.
# `end of do-file` in particular is what a `do "script.do"` failure ends with,
# and taking it as the error message told agents nothing about what broke.
_TRANSCRIPT_BOILERPLATE_RE = re.compile(
    r"^(?:end of (?:do-file|file)(?:\s*\(.*\))?|--\s*break\s*--)$",
    re.IGNORECASE,
)


def _last_error_line(error_text: str) -> str:
    """Extract the most informative line from a Stata error transcript.

    For single-line errors the text is short; we just take the first line.
    For multi-line transcripts the actual error sentence ("variable X not
    found") sits AFTER the last `. <cmd>` echo and BEFORE the `r(NN);` rc
    line. Return that sentence so agents see the diagnosis, not the echoed
    command and not Stata's `end of do-file` epilogue.
    """
    lines = [ln for ln in error_text.splitlines() if ln]
    if not lines:
        return ""
    if not any(ln.startswith(". ") for ln in lines):
        return lines[0].strip()
    # Walk from bottom: skip rc lines, echoes, continuation fragments and
    # structural boilerplate; take the first real sentence below them.
    for ln in reversed(lines):
        s = ln.strip()
        if not s:
            continue
        if s.startswith("r(") and s.endswith(");"):
            continue
        if ln.startswith((". ", _CONTINUATION_PREFIX)):
            continue
        if _TRANSCRIPT_BOILERPLATE_RE.match(s):
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


# ─────────────────────────────────────────────────────────────────────────────
# Log-handle hygiene
#
# A do-file that aborts between `log using` and `log close` leaves its handle
# open in the session. Every subsequent run then dies with r(604) "log file
# already open" — an error about the *previous* run that agents have no way to
# attribute. We snapshot open handles around each run and close the ones this
# run opened when it fails, so a failure cannot poison the session.
# ─────────────────────────────────────────────────────────────────────────────

_LOG_NAME_RE = re.compile(r"^\s*name:\s*(\S+)\s*$")

# Name used to park the user's r() while the runner issues its own r-class
# housekeeping commands. Prefixed so it cannot collide with a user hold.
_RETURN_HOLD_NAME = "_stata_code_rhold"


@contextmanager
def _preserved_returns(rt: Any) -> Iterator[None]:
    """Run internal housekeeping without destroying the caller's ``r()``.

    ``graph dir``, ``log query`` and ``graph export`` are all r-class: running
    them around user code silently wipes ``r()``, so the very common two-call
    pattern — ``summarize price`` then ``display r(mean)`` — returned missing.
    Stata's ``_return hold`` / ``_return restore`` parks the whole ``r()``
    bundle for the duration. ``e()`` is unaffected by these commands and is
    left alone.

    Entirely best-effort: if the hold fails (an ancient Stata, a leftover hold
    we could not clear) the housekeeping still runs, just without protection.
    """
    held = False
    try:
        # A hold leaked by a previous crash would make ours fail with r(110).
        rt.run_capture(f"capture _return drop {_RETURN_HOLD_NAME}")
        _stdout, rc, _err = rt.run_capture(f"_return hold {_RETURN_HOLD_NAME}")
        held = rc == 0
    except Exception:  # noqa: BLE001
        held = False
    try:
        yield
    finally:
        if held:
            try:
                rt.run_capture(f"capture _return restore {_RETURN_HOLD_NAME}")
            except Exception:  # noqa: BLE001
                pass


# Stata's placeholder for the log opened without `name(...)`; `log close`
# with no argument is what closes it.
_UNNAMED_LOG = "<unnamed>"


def _open_log_names(rt: Any) -> list[str]:
    """Names of every log handle currently open (`log query _all`).

    Returns ``[]`` when nothing is open (Stata prints `(closed)`) or when the
    query itself fails — this is best-effort hygiene, never a hard dependency.
    """
    try:
        stdout, rc, _err = rt.run_capture("log query _all")
    except Exception:  # noqa: BLE001
        return []
    if rc != 0:
        return []
    out: list[str] = []
    for line in stdout.splitlines():
        m = _LOG_NAME_RE.match(line)
        if m:
            out.append(m.group(1))
    return out


def _close_log_handles(rt: Any, names: Iterable[str]) -> list[str]:
    """Close the given log handles; return the ones actually closed."""
    closed: list[str] = []
    for name in names:
        cmd = "log close" if name == _UNNAMED_LOG else f"log close {name}"
        try:
            _stdout, rc, _err = rt.run_capture(f"capture {cmd}")
        except Exception:  # noqa: BLE001
            continue
        if rc == 0:
            closed.append(name)
    return closed


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
    include_results: str = "scalars",  # "none" | "scalars" | "full"
    include_estimation: str = "full",  # "none" | "summary" | "full"
    max_coefficients: int | None = None,
    timeout_ms: int | None = 600_000,  # metadata only here; enforced by _pool
    persist_log_files: bool = False,
    persist_generated_files: bool = True,
    track_output_files: bool = True,
    auto_close_logs: bool = True,
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

    Payload budget. ``include_results`` (default ``"scalars"``) controls how
    much of ``results.r`` / ``results.e`` is inlined; ``include_estimation``
    and ``max_coefficients`` control ``results.estimation``. The defaults are
    chosen so a single estimation is described once — as the typed coefficient
    table — rather than four times over in ``e(b)``, ``e(V)``, ``e(beta)`` and
    ``r(table)``. Raw matrix values stay retrievable via ``get_matrix(ref)``.
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
    try:
        results_mode = IncludeResults(include_results)
    except ValueError as exc:
        raise ValueError(
            f"include_results must be 'none' | 'scalars' | 'full'; got {include_results!r}"
        ) from exc
    try:
        estimation_mode = IncludeEstimation(include_estimation)
    except ValueError as exc:
        raise ValueError(
            "include_estimation must be 'none' | 'summary' | 'full'; "
            f"got {include_estimation!r}"
        ) from exc
    if max_coefficients is not None and max_coefficients < 0:
        raise ValueError(f"max_coefficients must be ≥ 0 or null; got {max_coefficients}")

    # Command-safety gate. Runs before Stata is even initialized so a blocked
    # command is rejected identically whether or not Stata is installed, and so
    # the in-process paths (Jupyter kernel, direct callers) get the same guard
    # the subprocess pool applies parent-side.
    policy_block = policy_check(code, session_id=session_id)
    if policy_block is not None:
        return policy_block

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
    if run_working_dir:
        _change_stata_working_dir(rt, run_working_dir)
    # Everything downstream — output detection, relative `do` path resolution,
    # run-bundle placement — needs the directory Stata will actually resolve
    # relative paths against, which is `c(pwd)` when the caller named neither
    # an origin nor an explicit working dir.
    effective_dir = run_working_dir or _current_stata_dir(rt)

    want_snapshot = track_output_files or (persist_log_files and persist_generated_files)
    output_snapshot: FileSnapshot | None = None
    if want_snapshot and effective_dir:
        output_snapshot = snapshot_working_dir_files(effective_dir, origin_path=origin_path)

    # Pre-run probes are r-class commands, so they run inside a hold that parks
    # the caller's r(). Without it, submitting `display r(mean)` one call after
    # `summarize price` reads back a missing value.
    with _preserved_returns(rt):
        # Snapshot open log handles so a run that aborts mid-script cannot leave
        # a dangling handle that fails every subsequent run with r(604).
        pre_log_names = _open_log_names(rt) if auto_close_logs else []

        # Snapshot existing graph names before user code so we can take a delta
        # afterward.
        pre_graphs = _list_graph_names(rt) if include_graphs != "none" else []

        # e() is session-global and outlives the run that set it, so
        # `results.estimation` may describe an estimation from an earlier call.
        # Fingerprint it now to tell "this run estimated something" from "this
        # run inherited an estimation" — the difference decides whether an
        # e(b)/e(V) rebuild is worth reporting.
        pre_estimation = (
            _estimation_fingerprint(rt)
            if estimation_mode != IncludeEstimation.NONE
            else None
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

    # pystata raises the whole Stata transcript as the exception message and
    # leaves the redirected stdout empty, so a failing run used to come back
    # with no log at all — no `log.head`, no `log://` ref, nothing for
    # `search_log` to search, exactly when the agent needs the transcript most.
    # Adopt the transcript as the log text when stdout has nothing.
    log_text = stdout_text if stdout_text.strip() or not err_msg else err_msg

    log = _split_log(
        log_text,
        log_lines_head,
        log_lines_tail,
        include_full_log,
        request_id,
    )

    # On Stata error, we still surface results/dataset state — they reflect
    # whatever state existed before the failing command (per SCHEMA §3.7
    # commands_executed semantics).
    results, estimation = _collect_results(
        rt,
        request_id=request_id,
        results_mode=results_mode,
        estimation_mode=estimation_mode,
        max_coefficients=max_coefficients,
    )
    results.estimation = estimation
    dataset = _collect_dataset(rt, include_dataset_variables)

    available_varnames = [v.name for v in dataset.variables] if dataset.variables else None

    if err_msg is not None:
        error = _build_error(
            rc,
            err_msg,
            code,
            available_varnames,
            working_dir=effective_dir,
        )
        # Build an error_window: prefer log tail; fall back to the error message
        # itself when the log is empty (pystata can raise before any stdout
        # gets flushed for short failures).
        log_lines = [ln for ln in log_text.replace("\r\n", "\n").split("\n") if ln]
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
    # `graph display` / `graph export` (all r-class) don't clobber the r() we
    # report, and inside a hold so they don't clobber the r() the NEXT call
    # may want to read.
    if include_graphs != "none":
        with _preserved_returns(rt):
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

    capabilities = [
        "log_truncation",
        "matrix_ref",
        "multi_session",
        "result_budget",
        "log_hygiene",
    ]
    if include_graphs != "none":
        capabilities.append("graph_ref")
    if include_graphs == "inline":
        capabilities.append("inline_graphs")

    top_rc = rc if error is not None else 0
    ok = error is None and rc == 0
    warnings = _extract_warnings(log_text)

    # r(table) is cleared by the next command, so a block that runs anything
    # after its estimation gets a table rebuilt from e(b)/e(V). The numbers
    # follow Stata's own t-vs-z rule and so still match the log, but the
    # provenance is worth stating: a caller comparing against `r(table)` rows
    # it did not capture should know which path produced these.
    #
    # Gated on the estimation being *this run's*. e() outlives the call that
    # set it, so every later `summarize` in the session re-reports the same
    # inherited table through the same fallback — warning on those would be
    # pure noise about a rebuild the caller did not ask for and cannot act on.
    estimated_here = (
        pre_estimation is not None and _estimation_fingerprint(rt) != pre_estimation
    )
    if estimated_here and estimation is not None and estimation.source == "e_b_v":
        warnings.append(
            StataWarning(
                kind="estimation_from_e_b_v",
                message=(
                    "results.estimation was rebuilt from e(b)/e(V) because r(table) "
                    "no longer described the current estimation — a later command "
                    "cleared it. Inference uses "
                    f"{estimation.statistic_kind!r} as Stata would "
                    f"(df_r={estimation.df_resid!r}). Put the estimation last in the "
                    "block, or re-run it alone, to get the r(table) values verbatim."
                ),
            )
        )

    # A failed run may have left `log using` handles open. Close only the ones
    # this run opened: a log the caller opened in an earlier successful run is
    # theirs to manage and must survive.
    if auto_close_logs and not ok:
        with _preserved_returns(rt):
            leaked = [n for n in _open_log_names(rt) if n not in pre_log_names]
            closed = _close_log_handles(rt, leaked)
        if closed:
            names = ", ".join(closed)
            warnings.append(
                StataWarning(
                    kind="log_closed",
                    message=(
                        f"Closed {len(closed)} log handle(s) left open by this failed "
                        f"run ({names}). Without this, the next run in this session "
                        "would fail with r(604) 'log file already open'."
                    ),
                )
            )

    outputs: list[OutputFile] = []
    if track_output_files and output_snapshot is not None and effective_dir:
        # Advertised whenever detection ran, not only when it found something —
        # a capability describes what the producer supports, so gating it on a
        # non-empty result would read as "feature missing" on a quiet run.
        capabilities.append("output_tracking")
        if snapshot_is_truncated(output_snapshot):
            warnings.append(
                StataWarning(
                    kind="output_tracking_skipped",
                    message=(
                        f"Working directory {effective_dir} holds more than "
                        f"{MAX_SNAPSHOT_ENTRIES} files; generated-file detection was "
                        "skipped. Pass track_output_files=false to silence this, or "
                        "point working_dir at a narrower directory."
                    ),
                )
            )
        else:
            try:
                outputs = [
                    OutputFile(**entry)
                    for entry in describe_output_files(
                        output_snapshot,
                        effective_dir,
                        origin_path=origin_path,
                    )
                ]
            except OSError:
                outputs = []

    if persist_log_files and origin_path:
        try:
            generated_files = (
                changed_output_files(
                    output_snapshot,
                    effective_dir,
                    origin_path=origin_path,
                )
                if (
                    persist_generated_files
                    and output_snapshot is not None
                    and not snapshot_is_truncated(output_snapshot)
                    and effective_dir
                )
                else []
            )
            files = persist_run_log_files(
                log_text=log_text,
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
                working_dir=str(effective_dir) if effective_dir else None,
            )
            graphs, graph_paths = _persist_graph_artifacts(files, graphs)
            files = files.model_copy(update={"graph_paths": graph_paths})
            if graph_paths:
                files = files.model_copy(
                    update={"graphs_dir": str(Path(files.directory) / "graphs")}
                )
            if generated_files and effective_dir:
                files = copy_output_artifacts(
                    files,
                    generated_files,
                    working_dir=effective_dir,
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
        outputs=outputs,
        warnings=warnings,
        error=error,
        origin=origin_echo,
        capabilities=capabilities,
    )


def _collect_results(
    rt: Any,
    *,
    request_id: str,
    results_mode: IncludeResults,
    estimation_mode: IncludeEstimation,
    max_coefficients: int | None,
) -> tuple[ResultsInfo, Any]:
    """Read r()/e(), derive the estimation contract, then project to the wire.

    The estimation contract is always built from the *complete* values, so a
    caller who suppressed ``results`` still gets correct standard errors; only
    the wire representation is reduced afterwards.
    """
    want_estimation = estimation_mode != IncludeEstimation.NONE
    if results_mode == IncludeResults.NONE and not want_estimation:
        # Nothing downstream reads r()/e() — skip the Stata round-trips.
        return ResultsInfo(last_estimation_cmd=_last_estimation_cmd(rt)), None

    # With results suppressed but estimation wanted, read only the matrices the
    # contract is derived from rather than every matrix in scope.
    named = results_mode != IncludeResults.NONE
    # e() scalars and macros are inputs to the estimation contract, not just
    # payload: n_obs, df_model, df_resid, model_stats, the t-vs-z choice and
    # depvar all come from them. Suppressing them for `include_results="none"`
    # silently hollowed out `estimation` — a caller who asked only to stop
    # *echoing* r()/e() lost the model-level numbers that `include_estimation`
    # is the knob for. Read them whenever estimation is wanted; the wire
    # projection below still drops them when the caller said "none".
    e_named = named or want_estimation
    raw = ResultsInfo(
        r=_collect_returns(
            rt,
            "r",
            want_named=named,
            matrix_names=None if named else _ESTIMATION_MATRICES["r"],
        ),
        e=_collect_returns(
            rt,
            "e",
            want_named=e_named,
            matrix_names=None if named else _ESTIMATION_MATRICES["e"],
        ),
        last_estimation_cmd=_last_estimation_cmd(rt),
    )
    estimation = (
        trim_estimation(
            build_estimation_result(raw, resolve_matrix=_refs_matrix_values),
            mode=estimation_mode,
            max_coefficients=max_coefficients,
        )
        if want_estimation
        else None
    )
    projected = ResultsInfo(
        r=_project_returns(raw.r, prefix="r", request_id=request_id, mode=results_mode),
        e=_project_returns(raw.e, prefix="e", request_id=request_id, mode=results_mode),
        last_estimation_cmd=raw.last_estimation_cmd,
    )
    return projected, estimation


def _current_stata_dir(rt: Any) -> Path | None:
    """Stata's current working directory (`c(pwd)`), or None if unreadable."""
    try:
        raw = rt.sfi.SFIToolkit.macroExpand("`c(pwd)'")
    except Exception:  # noqa: BLE001
        return None
    if not raw:
        return None
    try:
        path = Path(raw).expanduser()
        return path if path.is_dir() else None
    except OSError:
        return None


def _last_estimation_cmd(rt: Any) -> str | None:
    """Mirror e(cmd) for callers; returns None if no estimation has run."""
    try:
        v = rt.sfi.Macro.getGlobal("e(cmd)")
        return v or None
    except Exception:  # noqa: BLE001
        return None


def _estimation_fingerprint(rt: Any) -> tuple[str | None, str | None, float | None]:
    """Cheap identity for the estimation currently in e-scope.

    Three reads, no matrices: enough to tell one estimation from the next
    without paying to fetch e(b). Used only to compare before/after a run.
    """
    sfi = rt.sfi

    def _macro(name: str) -> str | None:
        try:
            return sfi.Macro.getGlobal(name) or None
        except Exception:  # noqa: BLE001
            return None

    try:
        n = sfi.Scalar.getValue("e(N)")
    except Exception:  # noqa: BLE001
        n = None
    return (_macro("e(cmd)"), _macro("e(cmdline)"), _norm_stata_number(n))


# ─────────────────────────────────────────────────────────────────────────────
# Multi-session via Stata frames (Module 4)
# ─────────────────────────────────────────────────────────────────────────────


_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_STATA_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MAPPED_FRAME_PREFIX = "_sc_"
# Stata's maximum name length ([U] 11.3 Naming conventions).
_STATA_NAME_MAX = 32
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
    if (
        _STATA_NAME_RE.fullmatch(session_id)
        and not session_id.startswith(_MAPPED_FRAME_PREFIX)
        # "default" is the frame "main" maps to; letting a session named
        # "default" claim it would silently alias the two sessions onto one
        # dataset. Route it through the mapped-frame path instead.
        and session_id != "default"
        # Stata caps name length at 32; passing a longer id straight through
        # makes `frame create` fail with rc 198. The mapped path below yields
        # a safe 28-char name, so fall through to it instead.
        and len(session_id) <= _STATA_NAME_MAX
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
        re.compile(r"convergence (?:not achieved|not reached|failed)", re.IGNORECASE),
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
            # Record the span BEFORE the de-dup check. Every occurrence of a
            # specific pattern must claim its span, or the 2nd and later
            # copies of an identical line (e.g. the same regression re-run
            # inside a foreach loop) go unclaimed and get picked up again by
            # the generic-note pass below — the exact double-count this
            # overlap test exists to prevent.
            matched_spans.append(m.span())
            if key in seen:
                continue
            seen.add(key)
            out.append(StataWarning(kind=kind, message=msg))

    # Generic notes: any `note: ...` line not already matched by a specific
    # pattern. Avoid double-counting. _NOTE_RE's span starts at the line's
    # leading whitespace while the specific patterns anchor at `note:` itself,
    # so containment of m.start() is not enough — test for span overlap.
    for m in _NOTE_RE.finditer(log):
        if any(s < m.end() and m.start() < e for s, e in matched_spans):
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
# Stata's default in-memory graph name, (re)used by any graph command that
# omits an explicit `name(...)` option. Capture/redraw detection keys off this.
_DEFAULT_GRAPH_NAME = "Graph"
# Commands that actually *draw* a graph (and thus create/overwrite an
# in-memory graph). Deliberately excludes the `graph` utility subcommands
# (export, display, dir, drop, describe, save, use, rename, set, copy, query,
# replay) — those operate on existing graphs and must not be mistaken for a
# redraw, or a bare `graph export` cell would spuriously re-surface a stale
# graph.
_GRAPH_COMMAND_RE = re.compile(
    r"^\s*(?:"
    r"graph\s+(?:bar|hbar|box|hbox|dot|pie|twoway|matrix|combine)\b|"
    r"twoway|scatter|line|connected|histogram|hist|kdensity|lpoly|lowess|"
    r"lfit|qfit|coefplot|binscatter|marginsplot"
    r")\b",
    re.IGNORECASE,
)


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
    after to find the post-existing list. Capture a graph when its name is
    genuinely new *or* when this cell's source shows it (re)drew that name.

    The redraw case matters because Stata keeps only one in-memory graph per
    name, so a command that overwrites an existing name (most commonly the
    default ``Graph``, produced by any unnamed graph command) leaves the
    ``graph dir`` name set unchanged. A pure set-difference against
    `pre_existing` therefore misses it — which is why, in a persistent session
    (Jupyter cell 2+, repeated MCP runs), only the first graph ever surfaced.

    For each captured graph: `graph display <name>` (makes it active),
    `graph export` to a tmpfile, read bytes, store under a ref. Tmpfile is
    deleted after.
    """
    after_names = _list_graph_names(rt)
    source_hints = source_hints or {}
    unnamed_source_hints = unnamed_source_hints or []

    # Names this cell explicitly drew, inferred from its source: every
    # `name(...)` option, plus the default graph when any unnamed graph
    # command ran. These are re-captured even if they already existed, so an
    # in-place redraw is not dropped.
    redrawn = set(source_hints)
    if unnamed_source_hints:
        redrawn.add(_DEFAULT_GRAPH_NAME)

    new_names = [n for n in after_names if n not in pre_existing or n in redrawn]
    if not new_names:
        return []
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
                rt.run_suppressed(f'graph export "{target}", as({fmt_str}) replace')
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

    ``format`` is a consistency check, not a converter: graph bytes are
    stored in the format the original run exported. Requesting a different
    format raises ``ValueError`` instead of silently returning mismatched
    bytes — re-run with ``graph_format=...`` to get another format.
    """
    payload = _refs.get(ref)
    if payload is None:
        raise RefNotFound(ref, kind="unknown_graph_ref")
    if format is not None and format != payload["format"]:
        raise ValueError(
            f"graph {ref} is stored as {payload['format']!r}; conversion to "
            f"{format!r} is not supported — re-run with graph_format={format!r}"
        )
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
