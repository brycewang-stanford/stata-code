"""Stata _rc → ErrorKind mapping and canonical remediation suggestion seeds.

The mapping table here is deliberately living code, not part of the normative
SCHEMA.md. New rc codes default to ErrorKind.UNKNOWN; we tighten the table over
time as we encounter real-world failures.
"""

from __future__ import annotations

import difflib

from stata_code.core.schema import ErrorKind, Suggestion

# ─────────────────────────────────────────────────────────────────────────────
# Stata _rc → ErrorKind
# ─────────────────────────────────────────────────────────────────────────────

RC_TO_KIND: dict[int, ErrorKind] = {
    # Syntax family (parser-level rejection)
    9: ErrorKind.SYNTAX,
    100: ErrorKind.SYNTAX,
    101: ErrorKind.SYNTAX,
    102: ErrorKind.SYNTAX,
    103: ErrorKind.SYNTAX,
    121: ErrorKind.SYNTAX,
    130: ErrorKind.SYNTAX,
    132: ErrorKind.SYNTAX,
    197: ErrorKind.SYNTAX,
    198: ErrorKind.SYNTAX,
    # Command resolution
    199: ErrorKind.COMMAND_NOT_FOUND,
    # Varname / name
    111: ErrorKind.VARNAME_NOT_FOUND,
    122: ErrorKind.INVALID_NAME,
    123: ErrorKind.INVALID_NAME,
    110: ErrorKind.NAME_CONFLICT,
    # Types
    109: ErrorKind.TYPE_MISMATCH,
    408: ErrorKind.TYPE_MISMATCH,
    # Sorting
    119: ErrorKind.NOT_SORTED,
    459: ErrorKind.NOT_SORTED,
    # Estimation / convergence
    430: ErrorKind.CONVERGENCE,
    491: ErrorKind.INFEASIBLE,
    301: ErrorKind.NO_ESTIMATION_RESULTS,
    1400: ErrorKind.ESTIMATION_SAMPLE_EMPTY,
    1401: ErrorKind.ESTIMATION_FAILURE,
    1402: ErrorKind.ESTIMATION_FAILURE,
    # Observations
    2000: ErrorKind.NO_OBSERVATIONS,
    2001: ErrorKind.NO_OBSERVATIONS,
    # Data state
    4: ErrorKind.DATA_IN_MEMORY,
    # Matrix
    503: ErrorKind.MATRIX_CONFORMABILITY,
    507: ErrorKind.MATRIX_CONFORMABILITY,
    504: ErrorKind.MATRIX_MISSING,
    506: ErrorKind.MATRIX_SINGULAR,
    508: ErrorKind.MATRIX_SINGULAR,
    # Files
    322: ErrorKind.FILE_NOT_FOUND,
    601: ErrorKind.FILE_NOT_FOUND,
    602: ErrorKind.FILE_EXISTS,
    603: ErrorKind.FILE_IO,
    604: ErrorKind.FILE_CORRUPT,
    610: ErrorKind.FILE_CORRUPT,
    # Network
    691: ErrorKind.NETWORK,
    692: ErrorKind.NETWORK,
    693: ErrorKind.NETWORK,
    # Permission / encoding
    608: ErrorKind.PERMISSION,
    615: ErrorKind.ENCODING,
    616: ErrorKind.ENCODING,
    # Memory / Stata limits
    901: ErrorKind.STATA_LIMIT,
    902: ErrorKind.STATA_LIMIT,
    903: ErrorKind.STATA_LIMIT,
    480: ErrorKind.OUT_OF_MEMORY,
    909: ErrorKind.OUT_OF_MEMORY,
    # Interrupt
    1: ErrorKind.INTERRUPT,
}

# Synthetic codes — the producer (not Stata) sets these.
SYNTHETIC_RC_TO_KIND: dict[int, ErrorKind] = {
    -1: ErrorKind.ADAPTER_CRASH,
    -2: ErrorKind.TIMEOUT,
    -3: ErrorKind.CANCELLED,
}


def classify_rc(rc: int) -> ErrorKind:
    """Map a Stata `_rc` (or synthetic code) to its `ErrorKind`."""
    if rc in SYNTHETIC_RC_TO_KIND:
        return SYNTHETIC_RC_TO_KIND[rc]
    return RC_TO_KIND.get(rc, ErrorKind.UNKNOWN)


# ─────────────────────────────────────────────────────────────────────────────
# Curated catalog of common Stata commands for fuzzy "did you mean" matching
# on rc 199 (command_not_found). Kept as a module constant so it's cheap to
# import and easy to extend. This is intentionally not exhaustive — it covers
# the high-traffic commands an agent is most likely to mistype.
# ─────────────────────────────────────────────────────────────────────────────

COMMON_STATA_COMMANDS: tuple[str, ...] = (
    # Estimation
    "regress", "logit", "probit", "areg", "ivregress", "reghdfe",
    "xtreg", "xtivreg",
    # Summary / display
    "summarize", "tabulate", "tabstat", "table", "list",
    "describe", "codebook",
    # Data manipulation
    "generate", "replace", "drop", "keep", "sort", "gsort",
    "by", "bysort", "merge", "append", "save", "use", "sysuse",
    "import", "export", "encode", "decode", "recode", "label",
    "rename", "reshape", "collapse", "egen",
    # Postestimation
    "predict", "estimates", "margins", "test", "testparm",
    "lincom", "nlcom",
    # Programming primitives
    "mat", "matrix", "scalar", "local", "global",
    "di", "display", "set", "clear", "exit", "do", "run",
    "capture", "quietly", "noisily",
    "foreach", "forvalues", "while", "if", "else",
    "program", "return", "ereturn",
    "postutil", "post", "postclose",
    "putexcel", "putdocx", "file",
    # Logging / I/O / shell
    "log", "cmdlog", "cd", "pwd", "mkdir", "dir", "ls",
    "cap", "qui", "noi",
    # Versions / help / packages
    "version", "which", "ssc", "net", "search", "help", "findit",
    "view", "browse", "edit",
    # Time-series / panel setup
    "tsset", "xtset", "stset",
)


# ─────────────────────────────────────────────────────────────────────────────
# Canonical remediation suggestion seeds
# ─────────────────────────────────────────────────────────────────────────────


def suggestions_for(
    kind: ErrorKind,
    *,
    varname: str | None = None,
    name: str | None = None,
    command: str | None = None,
    path: str | None = None,
    available_varnames: list[str] | None = None,
) -> list[Suggestion]:
    """Generate canonical remediation suggestions for an error kind.

    Best-effort. Returns an empty list when no canonical hint applies.

    Parameters
    ----------
    kind : ErrorKind
        The classified error kind.
    varname : str, optional
        The bad variable name parsed from the Stata error message
        (used for VARNAME_NOT_FOUND).
    name : str, optional
        The conflicting name parsed from the Stata error message
        (used for NAME_CONFLICT).
    command : str, optional
        The unrecognized command parsed from the Stata error message
        (used for COMMAND_NOT_FOUND fuzzy matching).
    path : str, optional
        The offending file path (used for FILE_NOT_FOUND).
    available_varnames : list[str], optional
        Variable names currently in memory; used as the candidate set for
        `varname_not_found` fuzzy matching. The runner passes this from
        `dataset.variables` (capped at 200 names per SCHEMA §3.5).
    """
    out: list[Suggestion] = []

    if kind == ErrorKind.VARNAME_NOT_FOUND:
        out.extend(_varname_suggestions(varname, available_varnames))

    elif kind == ErrorKind.COMMAND_NOT_FOUND:
        out.extend(_command_suggestions(command))

    elif kind == ErrorKind.NAME_CONFLICT:
        target = f"`{name}`" if name else "the name"
        if name:
            out.append(
                Suggestion(
                    action=(
                        f"{target} already exists. "
                        f"Use `replace {name} = ...` to overwrite, "
                        f"or `drop {name}` first."
                    ),
                    command=f"drop {name}",
                )
            )
        else:
            out.append(
                Suggestion(
                    action=(
                        f"{target} already exists. "
                        "If overwriting is intended, use the `replace` option."
                    ),
                )
            )

    elif kind == ErrorKind.NOT_SORTED:
        out.append(
            Suggestion(
                action=(
                    "Data must be sorted before this command. "
                    "Run `sort <by-vars>` first."
                ),
                command="sort",
            )
        )

    elif kind == ErrorKind.DATA_IN_MEMORY:
        out.append(
            Suggestion(
                action=(
                    "Data in memory would be lost. "
                    "Use `clear` to discard, or save first."
                ),
                command="clear",
            )
        )

    elif kind == ErrorKind.NO_ESTIMATION_RESULTS:
        out.append(
            Suggestion(
                action=(
                    "No prior estimation results. "
                    "Run an estimation command (e.g., `regress`) before "
                    "`predict` / `margins`."
                ),
            )
        )

    elif kind == ErrorKind.FILE_NOT_FOUND:
        out.extend(_file_not_found_suggestions(path))

    elif kind == ErrorKind.FILE_EXISTS:
        target = f"`{path}`" if path else "the target file"
        out.append(
            Suggestion(
                action=(
                    f"{target} already exists. "
                    "Pass the `replace` option to overwrite."
                ),
            )
        )

    elif kind == ErrorKind.STATA_LIMIT:
        out.append(
            Suggestion(
                action=(
                    "Stata edition / matsize limit reached. "
                    "Try `set maxvar` / `set matsize`, or upgrade Stata edition."
                ),
            )
        )

    elif kind == ErrorKind.OUT_OF_MEMORY:
        out.append(
            Suggestion(
                action=(
                    "Out of memory. Try `compress` to shrink storage types, "
                    "drop unneeded vars/obs (`keep var*` / `keep if ...`), "
                    "or `set memory` (Stata 12 and earlier). "
                    "Upgrading Stata edition (SE → MP) raises the ceiling."
                ),
                command="compress",
            )
        )

    elif kind == ErrorKind.MATRIX_SINGULAR:
        out.append(
            Suggestion(
                action=(
                    "Matrix is singular or not positive definite. "
                    "Check for collinear regressors with `corr` or `vif` "
                    "after `regress`. If a constant-free model is intended, "
                    "the `noconst` option may help."
                ),
            )
        )

    elif kind == ErrorKind.MATRIX_CONFORMABILITY:
        out.append(
            Suggestion(
                action=(
                    "Matrices are not conformable. "
                    "Verify operand shapes with `rowsof()` and `colsof()`."
                ),
            )
        )

    elif kind == ErrorKind.NO_OBSERVATIONS:
        out.append(
            Suggestion(
                action=(
                    "No observations match the specified `if`/`in` "
                    "criteria. Use `count if <conditions>` to debug, "
                    "or drop the `if`/`in` clause to widen the sample."
                ),
                command="count",
            )
        )

    elif kind == ErrorKind.ESTIMATION_SAMPLE_EMPTY:
        out.append(
            Suggestion(
                action=(
                    "Estimation sample is empty after applying "
                    "`if`/`in`/missing-data exclusions. "
                    "Use `count if <conditions>` to debug, and inspect "
                    "missingness with `misstable summarize`."
                ),
                command="count",
            )
        )

    elif kind == ErrorKind.CONVERGENCE:
        out.append(
            Suggestion(
                action=(
                    "Optimizer did not converge. Try increasing "
                    "`iterate(50)` or relaxing `nrtolerance(1e-5)`. "
                    "An alternate algorithm via `technique(bfgs)` "
                    "(or `nr` / `dfp`) sometimes helps."
                ),
            )
        )

    return out


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────


def _varname_suggestions(
    varname: str | None,
    available_varnames: list[str] | None,
) -> list[Suggestion]:
    """Build varname_not_found suggestions.

    With candidates: emit one suggestion per close match (n=3, cutoff=0.6).
    Without candidates / no matches: emit a `describe` hint.
    """
    if varname is None:
        return [
            Suggestion(
                action="Run `describe` to list variables in memory.",
                command="describe",
            )
        ]
    if available_varnames:
        matches = difflib.get_close_matches(
            varname, available_varnames, n=3, cutoff=0.6
        )
        if matches:
            return [
                Suggestion(
                    action=(
                        f"Did you mean `{cand}`? "
                        f"`{varname}` is not in the current dataset."
                    ),
                    command="describe",
                )
                for cand in matches
            ]
    # No close match — generic fallback.
    return [
        Suggestion(
            action=(
                f"`{varname}` is not in the current dataset. "
                "Run `describe` to list available variables."
            ),
            command="describe",
        )
    ]


def _command_suggestions(command: str | None) -> list[Suggestion]:
    """Build command_not_found suggestions: fuzzy match + ssc/net hint.

    The ssc/net hint always appears so agents know where community-contributed
    packages come from. The fuzzy match (top 3, cutoff 0.65) appears first
    when one or more commands are close enough.
    """
    out: list[Suggestion] = []
    if command:
        matches = difflib.get_close_matches(
            command, COMMON_STATA_COMMANDS, n=3, cutoff=0.65
        )
        for cand in matches:
            out.append(
                Suggestion(
                    action=f"Did you mean `{cand}`?",
                    command=cand,
                )
            )
    out.append(
        Suggestion(
            action=(
                "Command not recognized. "
                "If it is a community-contributed package, "
                "try `ssc install <name>` or `net install <name>`."
            ),
        )
    )
    return out


def _file_not_found_suggestions(path: str | None) -> list[Suggestion]:
    """Build file_not_found suggestions: pwd + optional extension hint."""
    target = f"`{path}`" if path else "the requested file"
    out: list[Suggestion] = [
        Suggestion(
            action=(
                f"{target} not found. "
                "Verify the path and the current working directory "
                "(`pwd`, `ls`)."
            ),
            command="pwd",
        )
    ]
    # If the path looks like it's missing an extension, add a hint.
    # `.` heuristic: dataset / script paths nearly always have one.
    if path and "." not in path:
        out.append(
            Suggestion(
                action=(
                    f"`{path}` has no file extension. "
                    "If you meant a Stata dataset, try `{path}.dta`. "
                    "If you meant a do-file, try `{path}.do`."
                ).replace("{path}", path),
            )
        )
    return out
