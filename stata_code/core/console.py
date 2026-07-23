"""Console (batch) backend — run Stata **without** pystata.

pystata needs Stata 17+ and a Python that can import StataCorp's package. A large
installed base (older Stata, or environments where pystata simply is not wired up)
is shut out of the typed `RunResult` pipeline. This backend closes that gap: it
drives the Stata **command-line executable** in batch mode (`stata -b do …` on
Unix, `StataMP-64 /e do …` on Windows), then parses the resulting log and a
marker-delimited results dump into the *same* v1.0 `RunResult` the pystata path
produces — typed `r()` / `e()` scalars and macros, estimation matrices, the
error taxonomy, warnings, and dataset metadata.

Trade-offs vs. the pystata backend (documented, not hidden):

* **Stateless per call.** Each run is a fresh `stata -b do` process, so data and
  `r()` / `e()` do not persist across calls (there is no in-memory session). Run a
  complete do-file, or the pystata backend for interactive multi-call sessions.
* **No hard in-process cancel** — the subprocess is killed on timeout.
* **Graphs are not captured** in this first version (results/errors/data are).

The pure pieces here — wrapper generation and every parser — are exercised
offline against realistic batch-log samples. Only :func:`execute` touches a real
Stata, and it is gated behind the ``stata_required`` test marker like the rest of
the live path.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from stata_code.core.errors import recovery_for
from stata_code.core.estimation import build_estimation_result
from stata_code.core.policy import check as policy_check
from stata_code.core.runner import (
    _build_error,
    _extract_warnings,
    _split_log,
)
from stata_code.core.schema import (
    Backend,
    DatasetInfo,
    ErrorContext,
    ErrorInfo,
    ErrorKind,
    LogInfo,
    Matrix,
    ResultsInfo,
    RunResult,
    StataEdition,
    StataInfo,
    StataReturns,
    VariableInfo,
)


class ConsoleNotAvailable(RuntimeError):
    """Raised when no Stata command-line executable can be found."""


# ─────────────────────────────────────────────────────────────────────────────
# Discovery of the Stata command-line executable.
# ─────────────────────────────────────────────────────────────────────────────

_STATA_CLI_ENV_VARS: tuple[str, ...] = ("STATA_CODE_STATA_CLI", "STATA_CLI")
_STATA_ROOT_ENV_VARS: tuple[str, ...] = ("STATA_HOME", "STATA_PATH")

# Console executable basenames by platform, most-capable edition first.
_UNIX_EXE_NAMES: tuple[str, ...] = ("stata-mp", "stata-se", "stata", "stata-be")
_MAC_EXE_NAMES: tuple[str, ...] = ("stata-mp", "stata-se", "stata", "stata-be")
_WIN_EXE_NAMES: tuple[str, ...] = (
    "StataMP-64.exe",
    "StataSE-64.exe",
    "StataBE-64.exe",
    "Stata-64.exe",
    "StataMP.exe",
    "StataSE.exe",
    "Stata.exe",
)

_LINUX_ROOTS: tuple[str, ...] = (
    "/usr/local/stata19",
    "/usr/local/stata18",
    "/usr/local/stata17",
    "/usr/local/stata16",
    "/usr/local/stata15",
    "/usr/local/stata14",
    "/usr/local/stata13",
    "/usr/local/stata",
)
_MAC_APP_ROOTS: tuple[str, ...] = (
    "/Applications/StataNow",
    "/Applications/Stata19",
    "/Applications/Stata18",
    "/Applications/Stata17",
    "/Applications/Stata16",
    "/Applications/Stata15",
    "/Applications/Stata14",
    "/Applications/Stata",
)
_WIN_ROOTS: tuple[str, ...] = (
    r"C:\Program Files\StataNow",
    r"C:\Program Files\Stata19",
    r"C:\Program Files\Stata18",
    r"C:\Program Files\Stata17",
    r"C:\Program Files\Stata16",
    r"C:\Program Files\Stata15",
)


def _exe_names() -> tuple[str, ...]:
    system = platform.system()
    if system == "Windows":
        return _WIN_EXE_NAMES
    if system == "Darwin":
        return _MAC_EXE_NAMES
    return _UNIX_EXE_NAMES


def _candidate_executables() -> list[str]:
    """Return candidate Stata CLI executable paths, best-guess order."""
    out: list[str] = []

    # 1. Explicit env vars pointing at the executable.
    for var in _STATA_CLI_ENV_VARS:
        raw = os.environ.get(var)
        if raw:
            out.append(raw)

    names = _exe_names()

    # 2. Install roots from env vars.
    for var in _STATA_ROOT_ENV_VARS:
        raw = os.environ.get(var)
        if raw:
            out.extend(_names_in_root(raw, names))

    # 3. Platform default roots.
    system = platform.system()
    if system == "Windows":
        roots: tuple[str, ...] = _WIN_ROOTS
    elif system == "Darwin":
        roots = _MAC_APP_ROOTS
    else:
        roots = _LINUX_ROOTS
    for root in roots:
        out.extend(_names_in_root(root, names))

    # 4. Bare names on PATH.
    out.extend(names)

    # Dedupe preserving order.
    seen: set[str] = set()
    deduped: list[str] = []
    for item in out:
        if item not in seen:
            seen.add(item)
            deduped.append(item)
    return deduped


def _names_in_root(root: str, names: tuple[str, ...]) -> list[str]:
    base = Path(root).expanduser()
    out: list[str] = []
    for name in names:
        out.append(str(base / name))
        # macOS app bundles keep the console binary under Contents/MacOS.
        if platform.system() == "Darwin":
            out.append(str(base / f"{name}.app" / "Contents" / "MacOS" / name))
    return out


def find_stata_cli() -> str | None:
    """Return the first resolvable Stata CLI executable, or ``None``."""
    for candidate in _candidate_executables():
        # Absolute / relative path that exists and is a file.
        p = Path(candidate)
        if p.is_file() and os.access(candidate, os.X_OK):
            return str(p)
        # Bare name resolvable on PATH.
        if p.name == candidate:
            found = shutil.which(candidate)
            if found:
                return found
    return None


def console_available() -> bool:
    return find_stata_cli() is not None


def _edition_from_exe(exe: str) -> StataEdition:
    name = Path(exe).name.lower()
    if "mp" in name:
        return StataEdition.MP
    if "se" in name:
        return StataEdition.SE
    if "be" in name:
        return StataEdition.BE
    return StataEdition.UNKNOWN


# ─────────────────────────────────────────────────────────────────────────────
# Wrapper do-file generation.
# ─────────────────────────────────────────────────────────────────────────────

_M = "__STATACODE__"  # marker prefix, deliberately unlikely to collide
MARK_VERSION = f"{_M}|VERSION|"
MARK_RC = f"{_M}|RC|"
MARK_SECTION = f"{_M}|SECTION|"
MARK_MATRIX = f"{_M}|MATRIX|"
MARK_DS = f"{_M}|DS|"
MARK_VAR = f"{_M}|VAR|"
MARK_BEGIN = f"{_M}|BEGIN"
MARK_END = f"{_M}|END"

# Estimation matrices worth dumping values for (drives the typed coefficient
# table). Others are reported by name/dimension only.
_MATRIX_DUMPS: tuple[str, ...] = ("e(b)", "e(V)", "r(table)")


def build_wrapper_do(code: str, *, working_dir: str | None = None) -> str:
    """Return the wrapper do-file that runs ``code`` and dumps typed results.

    User code is wrapped in ``capture noisily`` so a Stata error does not abort
    the batch run before the results block executes; the real return code is
    saved first and echoed behind a marker. Every metadata probe is itself
    ``capture``-guarded so a single unsupported ``c()`` value on older Stata can
    never abort the dump.
    """
    lines: list[str] = [
        "set more off",
        "set linesize 255",
    ]
    if working_dir:
        # Quote defensively; the path is producer-controlled, not user code.
        safe = working_dir.replace('"', "")
        lines.append(f'capture cd "{safe}"')
    lines += [
        "capture noisily {",
        code,
        "}",
        "local __sc_rc = _rc",
        f'display "{MARK_VERSION}" c(stata_version) "|" c(flavor)',
        f'display "{MARK_RC}`__sc_rc\'"',
        f'display "{MARK_BEGIN}"',
        f'display "{MARK_SECTION}R"',
        "return list",
        f'display "{MARK_SECTION}E"',
        "ereturn list",
        f'display "{MARK_SECTION}MATRICES"',
    ]
    for mat in _MATRIX_DUMPS:
        lines += [
            f"capture confirm matrix {mat}",
            "if _rc == 0 {",
            f'    display "{MARK_MATRIX}{mat}"',
            f"    matrix list {mat}, format(%20.15g)",
            "}",
        ]
    lines += [
        f'display "{MARK_SECTION}DATASET"',
        f'display "{MARK_DS}nobs|" c(N)',
        f'display "{MARK_DS}nvars|" c(k)',
        f'display "{MARK_DS}filename|" c(filename)',
        "capture noisily {",
        "    foreach __v of varlist * {",
        # Stata: display "<MARK>`__v'|`: type `__v''|`: variable label `__v''"
        # Built by concatenation to avoid f-string quote/backtick collisions.
        "        display \"" + MARK_VAR + "`__v'|`: type `__v''|`: variable label `__v''\"",
        "    }",
        "}",
        f'display "{MARK_END}"',
    ]
    return "\n".join(lines) + "\n"


# ─────────────────────────────────────────────────────────────────────────────
# Parsing the batch log.
# ─────────────────────────────────────────────────────────────────────────────

# Markers are matched only at line start (after leading whitespace) so the
# command-echo line — `. display "__STATACODE__|RC|..."` — is never mistaken for
# the actual output line `__STATACODE__|RC|0`.
_RC_LINE_RE = re.compile(r"^\s*" + re.escape(MARK_RC) + r"(-?\d+)", re.MULTILINE)
_VERSION_LINE_RE = re.compile(
    r"^\s*" + re.escape(MARK_VERSION) + r"([0-9.]+)\|(\w+)?", re.MULTILINE
)
# `return list` scalar row:      r(N) =  74
_SCALAR_RE = re.compile(r"^\s*[re]\(([A-Za-z_][A-Za-z0-9_]*)\)\s*=\s*(.+?)\s*$")
# `return list` macro row:       r(cmd) : "regress"
_MACRO_RE = re.compile(r'^\s*[re]\(([A-Za-z_][A-Za-z0-9_]*)\)\s*:\s*"?(.*?)"?\s*$')
_DS_RE = re.compile(r"^\s*" + re.escape(MARK_DS) + r"(\w+)\|(.*)$")
_VAR_RE = re.compile(r"^\s*" + re.escape(MARK_VAR) + r"(.+?)\|(.*?)\|(.*)$")
# `matrix list` dimension header:  e(b)[1,2]  or  symmetric e(V)[2,2]
_MATRIX_HEADER_RE = re.compile(r"^\s*(symmetric\s+)?[A-Za-z_][A-Za-z0-9_]*\([^)]*\)\[\d+,\d+\]")


def _strip_markers(log: str) -> str:
    """Remove the results-dump section and marker lines from the user-facing log.

    Everything from the first ``BEGIN`` marker onward is bookkeeping the user did
    not write; drop it so ``log.head`` reads like a normal Stata log.
    """
    out: list[str] = []
    for line in log.replace("\r\n", "\n").split("\n"):
        if _M in line:
            if line.lstrip().startswith(("display", ". display")):
                # the echoed `display "__STATACODE__..."` command line
                continue
            if MARK_BEGIN in line:
                break
            continue
        out.append(line)
    # Trim trailing blank lines.
    while out and not out[-1].strip():
        out.pop()
    return "\n".join(out)


def _is_command_echo(line: str) -> bool:
    """A batch-log command echo line (`. cmd` or `> continuation`)."""
    s = line.lstrip()
    return s.startswith(". ") or s == "." or s.startswith("> ")


def _find_results_block(log: str) -> str | None:
    """Return the text between the output BEGIN and END marker *lines*.

    Uses the output lines (marker at line start), not the `. display "…BEGIN"`
    command echo, so the block excludes the wrapper's own echo scaffolding.
    """
    lines = log.replace("\r\n", "\n").split("\n")
    begin_idx = end_idx = None
    for i, line in enumerate(lines):
        s = line.strip()
        if begin_idx is None and s.startswith(MARK_BEGIN):
            begin_idx = i
        elif begin_idx is not None and s.startswith(MARK_END):
            end_idx = i
            break
    if begin_idx is None:
        return None
    return "\n".join(lines[begin_idx : end_idx if end_idx is not None else len(lines)])


def _split_sections(block: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in block.split("\n"):
        s = line.strip()
        if s.startswith(MARK_SECTION):
            current = s[len(MARK_SECTION) :].strip().split()[0].upper()
            sections[current] = []
            continue
        # Keep the MARK_MATRIX / MARK_DS / MARK_VAR *output* lines — the section
        # parsers need them — but drop the wrapper's BEGIN/END markers and every
        # command echo.
        if (
            current is not None
            and not _is_command_echo(line)
            and not s.startswith(MARK_BEGIN)
            and not s.startswith(MARK_END)
        ):
            sections[current].append(line)
    return sections


def _parse_number(text: str) -> float | None:
    t = text.strip().strip('"')
    if t in ("", ".", "..z", ".z"):
        return None
    if t.startswith(".") and len(t) == 2 and t[1].isalpha():
        return None  # extended missing .a .. .z
    try:
        return float(t)
    except ValueError:
        return None


def _parse_returns(section_lines: list[str]) -> StataReturns:
    scalars: dict[str, float | None] = {}
    macros: dict[str, str] = {}
    matrices: dict[str, Matrix] = {}
    mode: str | None = None
    for raw in section_lines:
        stripped = raw.strip()
        if not stripped:
            continue
        low = stripped.lower()
        if low in ("scalars:", "macros:", "matrices:", "functions:"):
            mode = low[:-1]
            continue
        if mode == "scalars":
            m = _SCALAR_RE.match(raw)
            if m:
                scalars[m.group(1)] = _parse_number(m.group(2))
        elif mode == "macros":
            m = _MACRO_RE.match(raw)
            if m:
                macros[m.group(1)] = m.group(2)
        # Matrices are intentionally not materialized from the `return list`
        # dimension rows: a Matrix needs values or a ref, and only the
        # estimation matrices (e(b)/e(V)/r(table)) are value-dumped. Those are
        # merged in afterward by _apply_matrix_dumps; other matrices are omitted
        # in this first console version.
    return StataReturns(scalars=scalars, macros=macros, matrices=matrices)


def _parse_matrix_dumps(section_lines: list[str]) -> dict[str, Matrix]:
    """Parse `matrix list <name>, format(...)` dumps, keyed by full spec.

    Keys are the original specs (``e(b)``, ``e(V)``, ``r(table)``) so the caller
    can route each to the right scope. Handles Stata's two-block wide-matrix
    wrapping by merging blocks that share row names.
    """
    out: dict[str, Matrix] = {}
    current: str | None = None
    buffer: list[str] = []
    for raw in section_lines:
        if raw.strip().startswith(MARK_MATRIX):
            if current is not None:
                out[current] = _parse_one_matrix(buffer)
            current = raw.strip()[len(MARK_MATRIX) :].strip()
            buffer = []
            continue
        if current is not None:
            buffer.append(raw)
    if current is not None:
        out[current] = _parse_one_matrix(buffer)
    return out


def _parse_one_matrix(lines: list[str]) -> Matrix:
    """Parse a `matrix list <name>, format(...)` body into a rectangular Matrix.

    Handles: the dimension header (`e(b)[1,2]` / `symmetric e(V)[2,2]`), column-
    name header rows, wide-matrix column blocks (merged by row name), and Stata's
    lower-triangular printing of symmetric matrices (mirror-filled to full). The
    result is always rectangular (rows padded with ``None`` as a safety net) so it
    satisfies the ``Matrix`` shape validator.
    """
    symmetric = False
    col_names: list[str] = []
    row_names: list[str] = []
    values_by_row: dict[str, list[float | None]] = {}

    for raw in lines:
        stripped = raw.strip()
        if not stripped or _is_command_echo(raw) or _M in raw:
            continue
        if _MATRIX_HEADER_RE.match(raw):
            symmetric = stripped.lower().startswith("symmetric")
            continue
        tokens = stripped.split()
        if _looks_like_header(tokens):
            for c in tokens:
                if c not in col_names:
                    col_names.append(c)
            continue
        row = tokens[0]
        vals = [_parse_number(t) for t in tokens[1:]]
        if row not in values_by_row:
            values_by_row[row] = []
            row_names.append(row)
        values_by_row[row].extend(vals)

    if not row_names:
        # Nothing parseable: represent as an empty-but-valid 0x0 matrix.
        return Matrix(rows=[], cols=col_names, values=[])

    ncols = len(col_names) if col_names else max(len(v) for v in values_by_row.values())
    grid = [list(values_by_row[r]) for r in row_names]

    if symmetric and len(row_names) == ncols:
        _mirror_fill(grid)
    # Rectangularize: pad short rows, truncate long ones.
    for i, grow in enumerate(grid):
        if len(grow) < ncols:
            grow.extend([None] * (ncols - len(grow)))
        elif len(grow) > ncols:
            grid[i] = grow[:ncols]

    return Matrix(rows=row_names, cols=col_names or row_names, values=grid)


def _mirror_fill(grid: list[list[float | None]]) -> None:
    """Fill the upper triangle of a lower-triangular symmetric matrix in place."""
    n = len(grid)
    for i in range(n):
        while len(grid[i]) < n:
            grid[i].append(None)
    for i in range(n):
        for j in range(i + 1, n):
            if grid[i][j] is None and j < len(grid) and i < len(grid[j]):
                grid[i][j] = grid[j][i]


def _looks_like_header(tokens: list[str]) -> bool:
    if not tokens:
        return False
    for t in tokens:
        if _parse_number(t) is not None:
            return False
    return True


def _parse_dataset(block: str) -> DatasetInfo:
    n_obs = 0
    n_vars = 0
    filename: str | None = None
    variables: list[VariableInfo] = []
    for raw in block.split("\n"):
        m = _DS_RE.search(raw)
        if m:
            key, val = m.group(1), m.group(2).strip()
            if key == "nobs":
                n_obs = int(_parse_number(val) or 0)
            elif key == "nvars":
                n_vars = int(_parse_number(val) or 0)
            elif key == "filename":
                filename = val or None
            continue
        v = _VAR_RE.search(raw)
        if v:
            variables.append(
                VariableInfo(
                    name=v.group(1).strip(),
                    type=v.group(2).strip() or "",
                    label=v.group(3).strip(),
                )
            )
    return DatasetInfo(
        frame="default",
        n_obs=n_obs,
        n_vars=n_vars,
        changed=False,
        filename=filename,
        variables=variables[:200] if variables else None,
    )


def _parse_version(log: str) -> tuple[str | None, StataEdition]:
    m = _VERSION_LINE_RE.search(log)
    if not m:
        return None, StataEdition.UNKNOWN
    version = m.group(1)
    flavor = (m.group(2) or "").upper()
    edition = {
        "MP": StataEdition.MP,
        "SE": StataEdition.SE,
        "IC": StataEdition.IC,
        "BE": StataEdition.BE,
    }.get(flavor, StataEdition.UNKNOWN)
    return version, edition


def _parse_rc(log: str) -> int:
    m = _RC_LINE_RE.search(log)
    if m:
        return int(m.group(1))
    # No marker rc — the wrapper never reached the RC line (should not happen
    # with capture-wrapping, but stay defensive): fall back to a trailing r(N);.
    trailing = re.findall(r"r\((\d+)\);", log)
    return int(trailing[-1]) if trailing else 0


# ─────────────────────────────────────────────────────────────────────────────
# Public build + execute.
# ─────────────────────────────────────────────────────────────────────────────


def _utc_iso_ms() -> str:
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def build_run_result(
    *,
    code: str,
    raw_log: str,
    exe: str,
    session_id: str,
    request_id: str,
    started_at: str,
    elapsed_ms: int,
    log_lines_head: int,
    log_lines_tail: int,
    include_full_log: bool,
) -> RunResult:
    """Assemble a v1.0 RunResult from a batch log. Pure — no subprocess."""
    rc = _parse_rc(raw_log)
    version, edition = _parse_version(raw_log)
    if edition is StataEdition.UNKNOWN:
        edition = _edition_from_exe(exe)

    user_log = _strip_markers(raw_log)
    log = _split_log(user_log, log_lines_head, log_lines_tail, include_full_log, request_id)

    block = _find_results_block(raw_log)
    if block is not None:
        sections = _split_sections(block)
        r = _parse_returns(sections.get("R", []))
        e = _parse_returns(sections.get("E", []))
        mats = _parse_matrix_dumps(sections.get("MATRICES", []))
        _apply_matrix_dumps(r, e, mats)
        dataset = _parse_dataset("\n".join(sections.get("DATASET", [])))
    else:
        r = StataReturns(scalars={}, macros={}, matrices={})
        e = StataReturns(scalars={}, macros={}, matrices={})
        dataset = DatasetInfo(
            frame="default", n_obs=0, n_vars=0, changed=False, filename=None, variables=None
        )

    results = ResultsInfo(r=r, e=e, last_estimation_cmd=e.macros.get("cmd"))
    results.estimation = build_estimation_result(results)

    warnings = _extract_warnings(user_log)

    error: ErrorInfo | None = None
    if rc != 0:
        varnames = [v.name for v in dataset.variables] if dataset.variables else None
        error = _build_error(rc, user_log, code, varnames)

    return RunResult(
        ok=rc == 0,
        rc=rc,
        session_id=session_id,
        request_id=request_id,
        started_at=started_at,
        elapsed_ms=max(1, elapsed_ms),
        stata_elapsed_ms=max(1, elapsed_ms),
        stata=StataInfo(version=version, edition=edition, backend=Backend.CONSOLE),
        log=log,
        results=results,
        dataset=dataset,
        graphs=[],
        warnings=warnings,
        error=error,
        schema_version="1.0",
        capabilities=["console", "stata_13_plus"],
    )


def _apply_matrix_dumps(
    r: StataReturns, e: StataReturns, dumps: dict[str, Matrix]
) -> None:
    """Route each value-dumped matrix (keyed ``e(b)`` / ``r(table)``) to its scope."""
    for spec, mat in dumps.items():
        m = re.match(r"^([re])\((.+)\)$", spec.strip())
        if not m or mat.values is None:
            continue
        scope, name = m.group(1), m.group(2)
        target = e if scope == "e" else r
        target.matrices[name] = mat


def execute(
    code: str,
    *,
    session_id: str = "main",
    log_lines_head: int = 20,
    log_lines_tail: int = 20,
    include_full_log: bool = False,
    timeout_ms: int | None = 600_000,
    working_dir: str | None = None,
    origin_path: str | None = None,
    origin_kind: str | None = None,
    origin_label: str | None = None,
    origin_cell_id: str | None = None,
    use_origin_workdir: bool = True,
    **_ignored: object,
) -> RunResult:
    """Run ``code`` through the Stata command-line executable in batch mode.

    Stateless: each call is a fresh Stata process. Raises
    :class:`ConsoleNotAvailable` when no executable is found.
    """
    # Command-safety gate — identical policy to the pystata paths, applied
    # before we spend a Stata process on blocked OS-escape commands.
    policy_block = policy_check(code, session_id=session_id)
    if policy_block is not None:
        return policy_block

    exe = find_stata_cli()
    if exe is None:
        raise ConsoleNotAvailable(
            "No Stata command-line executable found. Set STATA_CODE_STATA_CLI "
            "to the Stata console binary (e.g. /usr/local/stata18/stata-mp)."
        )

    resolved_workdir = working_dir
    if resolved_workdir is None and use_origin_workdir and origin_path:
        parent = Path(origin_path).expanduser().parent
        resolved_workdir = str(parent) if parent else None

    request_id = uuid.uuid4().hex
    started_at = _utc_iso_ms()
    started = time.monotonic()

    wrapper = build_wrapper_do(code, working_dir=resolved_workdir)
    with tempfile.TemporaryDirectory(prefix="stata_code_console_") as tmp:
        do_path = Path(tmp) / "run.do"
        do_path.write_text(wrapper, encoding="utf-8")
        log_path = Path(tmp) / "run.log"
        argv = _batch_argv(exe, do_path)
        timeout_s = None if not timeout_ms or timeout_ms <= 0 else timeout_ms / 1000.0
        try:
            subprocess.run(
                argv,
                cwd=tmp,
                capture_output=True,
                timeout=timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            return _timeout_result(
                session_id=session_id,
                request_id=request_id,
                started_at=started_at,
                elapsed_ms=elapsed_ms,
                timeout_ms=timeout_ms or 0,
                exe=exe,
            )
        raw_log = log_path.read_text(encoding="utf-8", errors="replace") if log_path.is_file() else ""

    elapsed_ms = int((time.monotonic() - started) * 1000)
    return build_run_result(
        code=code,
        raw_log=raw_log,
        exe=exe,
        session_id=session_id,
        request_id=request_id,
        started_at=started_at,
        elapsed_ms=elapsed_ms,
        log_lines_head=log_lines_head,
        log_lines_tail=log_lines_tail,
        include_full_log=include_full_log,
    )


def _batch_argv(exe: str, do_path: Path) -> list[str]:
    """Batch invocation differs by platform: Unix `-b do`, Windows `/e do`."""
    if platform.system() == "Windows":
        return [exe, "/e", "do", str(do_path)]
    return [exe, "-b", "do", str(do_path)]


def _timeout_result(
    *,
    session_id: str,
    request_id: str,
    started_at: str,
    elapsed_ms: int,
    timeout_ms: int,
    exe: str,
) -> RunResult:
    error = ErrorInfo(
        kind=ErrorKind.TIMEOUT,
        rc=-2,
        rc_label="timeout",
        message=(
            f"Console run exceeded the configured timeout of {timeout_ms} ms; "
            "the Stata batch process was terminated."
        ),
        context=ErrorContext(before=[], failing="", after=[]),
        suggestions=[],
        recovery=recovery_for(ErrorKind.TIMEOUT),
    )
    return RunResult(
        ok=False,
        rc=-2,
        session_id=session_id,
        request_id=request_id,
        started_at=started_at,
        elapsed_ms=max(1, elapsed_ms),
        stata_elapsed_ms=max(1, elapsed_ms),
        stata=StataInfo(
            version=None, edition=_edition_from_exe(exe), backend=Backend.CONSOLE
        ),
        log=LogInfo(
            head="", tail="", lines_total=0, bytes_total=0, truncated=False,
            complete=False, ref=None,
        ),
        results=ResultsInfo(
            r=StataReturns(scalars={}, macros={}, matrices={}),
            e=StataReturns(scalars={}, macros={}, matrices={}),
            last_estimation_cmd=None,
        ),
        dataset=DatasetInfo(
            frame="default", n_obs=0, n_vars=0, changed=False, filename=None, variables=None
        ),
        graphs=[],
        warnings=[],
        error=error,
        schema_version="1.0",
        capabilities=["console", "stata_13_plus"],
    )


__all__ = [
    "Backend",
    "ConsoleNotAvailable",
    "build_run_result",
    "build_wrapper_do",
    "console_available",
    "execute",
    "find_stata_cli",
]
