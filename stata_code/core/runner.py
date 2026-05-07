"""High-level execute() — runs Stata code and returns a v1.0 RunResult.

This is the only place that touches Stata. The MCP server and Jupyter
kernel both import from here and only translate transports.

Implements the v1.0 envelope from SCHEMA.md: ok / rc / error / log /
results / dataset / graphs / warnings / capabilities. r() and e() are
collected via sfi (native types). Multi-session is implemented through
Stata frames (session_id="main" ↔ default frame). Per-line error
attribution comes from parsing pystata's transcript.

For deferred items (hard timeout, cooperative cancellation, get_matrix
ref mode, console fallback for Stata 11–16, streaming logs), see
SCHEMA.md §8.
"""

from __future__ import annotations

import io
import re
import tempfile
import time
import uuid
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from stata_code.core import _refs
from stata_code.core._runtime import PystataNotAvailable, get_runtime
from stata_code.core.errors import classify_rc, suggestions_for
from stata_code.core.schema import (
    Backend,
    DatasetInfo,
    ErrorContext,
    ErrorInfo,
    ErrorKind,
    GraphFormat,
    GraphInfo,
    LogInfo,
    Matrix,
    ResultsInfo,
    RunResult,
    StataEdition,
    StataInfo,
    StataReturns,
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


def get_log(ref: str) -> dict[str, Any]:
    """Auxiliary tool: fetch the full log behind a `log.ref`.

    Per SCHEMA.md §5. Raises KeyError if the ref is unknown.
    """
    payload = _refs.get(ref)
    if payload is None:
        raise KeyError(f"unknown log ref: {ref!r}")
    return {
        "text": payload["text"],
        "lines_total": payload["lines_total"],
        "bytes_total": payload["bytes_total"],
    }


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
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            rt.stata.run(cmd, quietly=False, echo=False)
    except Exception:  # noqa: BLE001
        return {"scalars": [], "macros": [], "matrices": []}
    return _parse_return_list(buf.getvalue())


def _collect_returns(rt: Any, prefix: str, request_id: str) -> StataReturns:
    """Build a StataReturns for r() or e() using sfi for typed access.

    Matrices larger than ``MATRIX_INLINE_CELL_CAP`` cells are emitted with
    ``values=None`` and a ``matrix://<request_id>/<prefix>/<name>`` ref;
    callers fetch the values via :func:`get_matrix`.
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
    fields: dict[str, str | None] = {"varname": None, "path": None, "name": None}
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
    timeout_ms: int | None = 600_000,  # accepted but not yet enforced (v0.1)
) -> RunResult:
    """Execute Stata code and return a v1.0 RunResult.

    Raises PystataNotAvailable if Stata cannot be initialized.

    Multi-session: `session_id="main"` routes to Stata's master frame
    (`default`); any other valid Stata-name routes to a same-named Stata
    frame, created on demand. Frames isolate **data** (variables and
    observations), but `r()`, `e()`, scalars, and macros remain global
    across frames — agents needing full isolation should use separate
    processes.
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

    # Snapshot existing graph names before user code so we can take a delta
    # afterward. This itself calls `graph dir`, which clobbers r(); user code
    # will overwrite r() if they care about return values.
    pre_graphs = (
        _list_graph_names(rt) if include_graphs != "none" else []
    )

    stdout_text, rc, err_msg = rt.run_capture(code)

    elapsed_total_ms = max(1, int((time.monotonic() - started) * 1000))
    # v0.1: stata_elapsed_ms is the same as elapsed_ms (no IPC overhead to
    # subtract; pystata is in-process). We still report it separately so the
    # field is exercised end-to-end.
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

    # Graph capture happens AFTER r/e collection so that `graph dir` /
    # `graph display` / `graph export` (all r-class) don't clobber user r().
    if include_graphs != "none":
        graphs = _collect_graphs(
            rt,
            request_id=request_id,
            pre_existing=pre_graphs,
            fmt=gfmt,
            inline=(include_graphs == "inline"),
        )
    else:
        graphs = []

    capabilities = ["log_truncation", "multi_session"]
    if include_graphs != "none":
        capabilities.append("graph_ref")
    if include_graphs == "inline":
        capabilities.append("inline_graphs")

    return RunResult(
        ok=(error is None and rc == 0),
        rc=rc if error is not None else 0,
        session_id=session_id,
        request_id=request_id,
        started_at=started_at,
        elapsed_ms=elapsed_total_ms,
        stata_elapsed_ms=stata_elapsed_ms,
        stata=_stata_info(rt),
        log=log,
        results=results,
        dataset=dataset,
        graphs=graphs,
        warnings=_extract_warnings(stdout_text),
        error=error,
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


_STATA_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _frame_for_session(session_id: str) -> str:
    """Map a session_id to a Stata frame name.

    `"main"` → Stata's master frame `"default"`. Any other id must be a
    Stata-valid name (`[A-Za-z_][A-Za-z0-9_]*`). The schema permits `-` in
    session_id, but Stata frame names disallow it; v0.1 rejects.
    """
    if session_id == "main":
        return "default"
    if not _STATA_NAME_RE.match(session_id):
        raise ValueError(
            f"session_id {session_id!r} is not a valid Stata frame name. "
            "Use only letters, digits, and underscore; first char must be "
            "a letter or underscore. (v0.1 limitation.)"
        )
    return session_id


def _list_frame_names(rt: Any) -> list[str]:
    Frame = rt.sfi.Frame
    n = Frame.getFrameCount()
    return [Frame.getFrameAt(i) for i in range(n)]


def _ensure_session(rt: Any, session_id: str) -> None:
    """Switch to the frame for `session_id`, creating it if it does not exist."""
    target = _frame_for_session(session_id)
    existing = _list_frame_names(rt)
    if target not in existing:
        with redirect_stdout(io.StringIO()):
            rt.stata.run(f"frame create {target}", quietly=True, echo=False)
    # Switch (no-op if already on it; cheap)
    with redirect_stdout(io.StringIO()):
        rt.stata.run(f"frame change {target}", quietly=True, echo=False)


def list_sessions() -> list[dict[str, Any]]:
    """Auxiliary tool: enumerate live sessions (mapped from Stata frames)."""
    try:
        rt = get_runtime()
    except PystataNotAvailable:
        return []
    sessions: list[dict[str, Any]] = []
    for fname in _list_frame_names(rt):
        sid = "main" if fname == "default" else fname
        # n_obs from each frame; switching is needed since Frame helpers
        # operate on the current working frame for getObsTotal indirectly.
        # Easier: query c(N) after switching.
        with redirect_stdout(io.StringIO()):
            rt.stata.run(f"frame change {fname}", quietly=True, echo=False)
        n_obs = int(rt.sfi.Data.getObsTotal())
        sessions.append({"session_id": sid, "frame": fname, "n_obs": n_obs})
    return sessions


def reset_session(session_id: str = "main") -> dict[str, Any]:
    """Auxiliary tool: drop a session's data (and its frame, except `main`).

    `main` cannot be dropped — it maps to Stata's master `default` frame.
    For `main`, this performs `clear all` to wipe data in place.
    """
    rt = get_runtime()
    target = _frame_for_session(session_id)
    if session_id == "main":
        # Switch in, clear, return
        with redirect_stdout(io.StringIO()):
            rt.stata.run("frame change default", quietly=True, echo=False)
            rt.stata.run("clear all", quietly=True, echo=False)
        return {"session_id": "main", "dropped_frame": False}
    # Drop a non-main frame. Must switch off it first.
    with redirect_stdout(io.StringIO()):
        rt.stata.run("frame change default", quietly=True, echo=False)
        rt.stata.run(f"capture frame drop {target}", quietly=True, echo=False)
    # Drop ref-store entries scoped to this session (best-effort).
    _refs.clear_prefix(f"log://{session_id}-")
    _refs.clear_prefix(f"graph://{session_id}-")
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
        with redirect_stdout(io.StringIO()):
            rt.stata.run("graph dir", quietly=False, echo=False)
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
) -> list[GraphInfo]:
    """Capture graphs that user code newly created.

    Strategy: snapshot graph names before user code (`pre_existing`), call
    after to find the post-existing list, take the set difference. For each
    new graph: `graph display <name>` (makes it active), `graph export` to a
    tmpfile, read bytes, store under a ref. Tmpfile is deleted after.
    """
    after_names = _list_graph_names(rt)
    new_names = [n for n in after_names if n not in pre_existing]
    if not new_names:
        return []

    fmt_str = fmt.value
    out: list[GraphInfo] = []
    tmpdir = Path(tempfile.mkdtemp(prefix="stata_code_graph_"))
    try:
        for idx, gname in enumerate(new_names):
            target = tmpdir / f"{idx}.{fmt_str}"
            try:
                with redirect_stdout(io.StringIO()):
                    rt.stata.run(f"graph display {gname}", quietly=True, echo=False)
                    rt.stata.run(
                        f'graph export "{target}", as({fmt_str}) replace',
                        quietly=True,
                        echo=False,
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
            out.append(
                GraphInfo(
                    ref=ref,
                    name=gname,
                    format=fmt,
                    width=width,
                    height=height,
                    source_command=None,  # v0.1: not yet pinpointing
                    source_line=None,
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
    `height`. Raises KeyError if the ref is unknown (expired, never existed,
    or session reset).
    """
    payload = _refs.get(ref)
    if payload is None:
        raise KeyError(f"unknown graph ref: {ref!r}")
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
    dict with ``rows``, ``cols``, ``values``. Raises ``KeyError`` if the
    ref is unknown (expired, never existed, or session reset).
    """
    payload = _refs.get(ref)
    if payload is None:
        raise KeyError(f"unknown matrix ref: {ref!r}")
    return {
        "rows": payload["rows"],
        "cols": payload["cols"],
        "values": payload["values"],
    }
