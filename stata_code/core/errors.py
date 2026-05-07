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
# Canonical remediation suggestion seeds
# ─────────────────────────────────────────────────────────────────────────────


def suggestions_for(
    kind: ErrorKind,
    *,
    varname: str | None = None,
    name: str | None = None,
    path: str | None = None,
    available_varnames: list[str] | None = None,
) -> list[Suggestion]:
    """Generate canonical remediation suggestions for an error kind.

    Best-effort. Returns an empty list when no canonical hint applies.
    """
    out: list[Suggestion] = []

    if kind == ErrorKind.VARNAME_NOT_FOUND:
        if varname is not None:
            close = (
                _closest_match(varname, available_varnames)
                if available_varnames
                else None
            )
            if close is not None:
                out.append(
                    Suggestion(
                        action=f"Did you mean `{close}`? "
                        f"`{varname}` is not in the current dataset.",
                        command="describe",
                    )
                )
            else:
                out.append(
                    Suggestion(
                        action=f"`{varname}` is not in the current dataset.",
                        command="describe",
                    )
                )

    elif kind == ErrorKind.NAME_CONFLICT:
        target = f"`{name}`" if name else "the name"
        out.append(
            Suggestion(
                action=f"{target} already exists. "
                "If overwriting is intended, use the `replace` option.",
            )
        )

    elif kind == ErrorKind.COMMAND_NOT_FOUND:
        out.append(
            Suggestion(
                action="Command not recognized. "
                "If it is a community-contributed package, "
                "try `ssc install <name>` or `net install <name>`.",
            )
        )

    elif kind == ErrorKind.NOT_SORTED:
        out.append(
            Suggestion(
                action="Data must be sorted before this command. "
                "Run `sort <varlist>` first.",
            )
        )

    elif kind == ErrorKind.DATA_IN_MEMORY:
        out.append(
            Suggestion(
                action="Data in memory would be lost. "
                "Use `clear` to discard, or save first.",
                command="clear",
            )
        )

    elif kind == ErrorKind.NO_ESTIMATION_RESULTS:
        out.append(
            Suggestion(
                action="No prior estimation results. "
                "Run an estimation command (e.g., `regress`) before "
                "`predict` / `margins`.",
            )
        )

    elif kind == ErrorKind.FILE_NOT_FOUND:
        target = f"`{path}`" if path else "the requested file"
        out.append(
            Suggestion(
                action=f"{target} not found. "
                "Verify the path and the current working directory.",
                command="pwd",
            )
        )

    elif kind == ErrorKind.FILE_EXISTS:
        target = f"`{path}`" if path else "the target file"
        out.append(
            Suggestion(
                action=f"{target} already exists. "
                "Pass the `replace` option to overwrite.",
            )
        )

    elif kind == ErrorKind.STATA_LIMIT:
        out.append(
            Suggestion(
                action="Stata edition / matsize limit reached. "
                "Try `set maxvar` / `set matsize`, or upgrade Stata edition.",
            )
        )

    elif kind == ErrorKind.MATRIX_SINGULAR:
        out.append(
            Suggestion(
                action="Matrix is singular or not positive definite. "
                "Check for collinearity or omitted variables.",
            )
        )

    elif kind == ErrorKind.NO_OBSERVATIONS:
        out.append(
            Suggestion(
                action="No observations match the specified `if`/`in` "
                "criteria. Loosen the condition or check the data.",
            )
        )

    elif kind == ErrorKind.ESTIMATION_SAMPLE_EMPTY:
        out.append(
            Suggestion(
                action="Estimation sample is empty after applying "
                "`if`/`in`/missing-data exclusions. Inspect the data with "
                "`misstable summarize`.",
            )
        )

    return out


def _closest_match(target: str, candidates: list[str]) -> str | None:
    """Return the closest candidate by sequence-matching, if reasonably close."""
    matches = difflib.get_close_matches(target, candidates, n=1, cutoff=0.6)
    return matches[0] if matches else None
