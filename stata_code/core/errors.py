"""Stata _rc → ErrorKind mapping and canonical remediation suggestion seeds.

The mapping table here is deliberately living code, not part of the normative
SCHEMA.md. New rc codes default to ErrorKind.UNKNOWN; we tighten the table over
time as we encounter real-world failures.
"""

from __future__ import annotations

import difflib

from stata_code.core.schema import ErrorKind, Recovery, Suggestion

# ─────────────────────────────────────────────────────────────────────────────
# Stata _rc → ErrorKind
# ─────────────────────────────────────────────────────────────────────────────

# Every mapping below is checked against StataCorp's authoritative return-code
# table in ``[P] error`` (Stata 19 manual, 2025). Where a code's documented
# meaning has no good home in the closed v1.0 ``ErrorKind`` enum, we leave it
# unmapped (→ ``UNKNOWN``) rather than assert a misleading kind — a wrong kind
# is worse than ``unknown``, because agents branch on ``error.kind``. Codes that
# are not in the public manual table (e.g. 408, 615, 1401, 1402) are kept only
# where prior art already mapped them and a change could not be verified.
RC_TO_KIND: dict[int, ErrorKind] = {
    # Interrupt
    1: ErrorKind.INTERRUPT,  # you pressed Break
    # Network / connection. The manual groups r(630)–r(696) as "messages you
    # might receive when executing any command with a file over the network",
    # but only the genuinely network-level codes belong here; the I/O codes in
    # that range (688–696) are local filesystem failures (see Files, below).
    2: ErrorKind.NETWORK,  # connection timed out
    631: ErrorKind.NETWORK,  # host not found
    672: ErrorKind.NETWORK,  # server refused to send file
    677: ErrorKind.NETWORK,  # remote connection failed
    # Data state
    4: ErrorKind.DATA_IN_MEMORY,  # no; dataset in memory has changed since saved
    # Sorting. The canonical "not sorted" return code is r(5); the previous
    # table mis-mapped 119 ("statement out of context") and 459 ("something
    # that should be true of your data is not"), which are unrelated.
    5: ErrorKind.NOT_SORTED,  # not sorted
    # Syntax family (parser-level rejection)
    100: ErrorKind.SYNTAX,  # varlist required
    101: ErrorKind.SYNTAX,  # varlist not allowed
    102: ErrorKind.SYNTAX,  # too few variables specified
    103: ErrorKind.SYNTAX,  # too many variables specified
    # numlist parse failures r(121)–r(127) are all syntax errors. 122/123 were
    # previously mis-mapped to INVALID_NAME; the manual shows they are numlist
    # cardinality errors, not name errors. (INVALID_NAME has no dedicated rc —
    # Stata folds "invalid name" into r(198) "invalid syntax".)
    121: ErrorKind.SYNTAX,  # invalid numlist
    122: ErrorKind.SYNTAX,  # invalid numlist has too few elements
    123: ErrorKind.SYNTAX,  # invalid numlist has too many elements
    124: ErrorKind.SYNTAX,  # invalid numlist has elements out of order
    125: ErrorKind.SYNTAX,  # invalid numlist has elements outside allowed range
    126: ErrorKind.SYNTAX,  # invalid numlist has noninteger elements
    127: ErrorKind.SYNTAX,  # invalid numlist has missing values
    130: ErrorKind.SYNTAX,  # expression too long
    132: ErrorKind.SYNTAX,  # too many '(' or '['
    197: ErrorKind.SYNTAX,  # invalid syntax (from `syntax`)
    198: ErrorKind.SYNTAX,  # invalid syntax
    # Command resolution
    199: ErrorKind.COMMAND_NOT_FOUND,  # unrecognized command
    # Varname / name
    111: ErrorKind.VARNAME_NOT_FOUND,  # variable not found
    110: ErrorKind.NAME_CONFLICT,  # already defined
    # Types
    109: ErrorKind.TYPE_MISMATCH,  # type mismatch
    408: ErrorKind.TYPE_MISMATCH,  # (not in public manual table; kept from prior art)
    # Estimation / convergence
    301: ErrorKind.NO_ESTIMATION_RESULTS,  # last estimates not found
    322: ErrorKind.ESTIMATION_FAILURE,  # something true of your estimation results is not
    430: ErrorKind.CONVERGENCE,  # convergence not achieved
    480: ErrorKind.INFEASIBLE,  # nl: starting values invalid / RHS has missing values
    491: ErrorKind.INFEASIBLE,  # could not find feasible values
    1400: ErrorKind.ESTIMATION_FAILURE,  # numerical overflow (commonly during estimation)
    1401: ErrorKind.ESTIMATION_FAILURE,  # (not in public manual table; kept from prior art)
    1402: ErrorKind.ESTIMATION_FAILURE,  # (not in public manual table; kept from prior art)
    # Observations. Stata distinguishes rc 2000 ("no observations") from
    # rc 2001 ("insufficient observations") — currently both map to the
    # same kind to keep the schema enum compact. If a downstream consumer
    # needs to react differently to "filter excluded everything" vs
    # "estimator needs more rows", split into a new ``INSUFFICIENT_OBSERVATIONS``
    # kind and bump the schema version.
    2000: ErrorKind.NO_OBSERVATIONS,  # no observations
    2001: ErrorKind.NO_OBSERVATIONS,  # insufficient observations
    # Matrix. r(507) is documented as "name conflict" (a `matrix post` row/col
    # name mismatch); it stays in the matrix bucket because routing it to the
    # variable-oriented NAME_CONFLICT kind would trigger varname parsing that
    # cannot apply to a matrix message.
    503: ErrorKind.MATRIX_CONFORMABILITY,  # conformability error
    507: ErrorKind.MATRIX_CONFORMABILITY,  # name conflict (matrix post)
    504: ErrorKind.MATRIX_MISSING,  # matrix has missing values
    506: ErrorKind.MATRIX_SINGULAR,  # matrix not positive definite
    508: ErrorKind.MATRIX_SINGULAR,  # matrix has zero values
    # Files
    601: ErrorKind.FILE_NOT_FOUND,  # file not found
    602: ErrorKind.FILE_EXISTS,  # file already exists
    603: ErrorKind.FILE_IO,  # file could not be opened
    610: ErrorKind.FILE_CORRUPT,  # file not Stata format
    688: ErrorKind.FILE_CORRUPT,  # file is corrupt
    691: ErrorKind.FILE_IO,  # I/O error (local filesystem; was mis-mapped to NETWORK)
    692: ErrorKind.FILE_IO,  # file I/O error on read (was mis-mapped to NETWORK)
    693: ErrorKind.FILE_IO,  # file I/O error on write (was mis-mapped to NETWORK)
    # Permission
    608: ErrorKind.PERMISSION,  # file is read-only; cannot be modified or erased
    # Memory / Stata limits
    901: ErrorKind.STATA_LIMIT,  # no room to add more observations
    902: ErrorKind.STATA_LIMIT,  # no room to add more variables because of width
    903: ErrorKind.STATA_LIMIT,  # no room to promote variable because of width
    907: ErrorKind.STATA_LIMIT,  # maxvar too small
    909: ErrorKind.OUT_OF_MEMORY,  # op. sys. refuses to provide memory
    950: ErrorKind.OUT_OF_MEMORY,  # insufficient memory
}

# Synthetic codes — the producer (not Stata) sets these.
SYNTHETIC_RC_TO_KIND: dict[int, ErrorKind] = {
    -1: ErrorKind.ADAPTER_CRASH,
    -2: ErrorKind.TIMEOUT,
    -3: ErrorKind.CANCELLED,
    -4: ErrorKind.POLICY_BLOCKED,
}


def classify_rc(rc: int) -> ErrorKind:
    """Map a Stata `_rc` (or synthetic code) to its `ErrorKind`."""
    if rc in SYNTHETIC_RC_TO_KIND:
        return SYNTHETIC_RC_TO_KIND[rc]
    return RC_TO_KIND.get(rc, ErrorKind.UNKNOWN)


# ─────────────────────────────────────────────────────────────────────────────
# Stata _rc → canonical short label
#
# Stata's official one-line message for each return code, transcribed from the
# ``[P] error`` manual (Stata 19, 2025). These are the *generic* forms — the
# concrete runtime message (in ``error.message``) substitutes the offending
# name/path. Populating ``rc_label`` gives agents a stable, locale- and
# transcript-independent descriptor to branch and group on even when message
# parsing fails or the log is truncated. Only verified codes are listed; an
# unknown code yields an empty label rather than a guess.
# ─────────────────────────────────────────────────────────────────────────────

RC_LABEL: dict[int, str] = {
    1: "you pressed Break",
    2: "connection timed out",
    4: "no; dataset in memory has changed since last saved",
    5: "not sorted",
    100: "varlist required",
    101: "varlist not allowed",
    102: "too few variables specified",
    103: "too many variables specified",
    109: "type mismatch",
    110: "already defined",
    111: "variable not found",
    121: "invalid numlist",
    122: "invalid numlist has too few elements",
    123: "invalid numlist has too many elements",
    124: "invalid numlist has elements out of order",
    125: "invalid numlist has elements outside of allowed range",
    126: "invalid numlist has noninteger elements",
    127: "invalid numlist has missing values",
    130: "expression too long",
    132: "too many '(' or '['",
    197: "invalid syntax",
    198: "invalid syntax",
    199: "unrecognized command",
    301: "last estimates not found",
    322: "something that should be true of your estimation results is not",
    430: "convergence not achieved",
    480: "starting values invalid or some RHS variables have missing values",
    491: "could not find feasible values",
    503: "conformability error",
    504: "matrix has missing values",
    506: "matrix not positive definite",
    507: "name conflict",
    508: "matrix has zero values",
    601: "file not found",
    602: "file already exists",
    603: "file could not be opened",
    608: "file is read-only; cannot be modified or erased",
    610: "file not Stata format",
    631: "host not found",
    672: "server refused to send file",
    677: "remote connection failed",
    688: "file is corrupt",
    691: "I/O error",
    692: "file I/O error on read",
    693: "file I/O error on write",
    901: "no room to add more observations",
    902: "no room to add more variables because of width",
    903: "no room to promote variable because of width",
    907: "maxvar too small",
    909: "op. sys. refuses to provide memory",
    950: "insufficient memory",
    1400: "numerical overflow",
    2000: "no observations",
    2001: "insufficient observations",
}

# Synthetic codes carry their own labels (the producer, not Stata, sets these).
SYNTHETIC_RC_LABEL: dict[int, str] = {
    -1: "adapter_crash",
    -2: "timeout",
    -3: "cancelled",
    -4: "policy_blocked",
}


def label_for_rc(rc: int) -> str:
    """Return Stata's canonical short label for a return code.

    Returns the empty string for codes we have not verified, so consumers can
    distinguish "no label known" from a (possibly wrong) guess.
    """
    if rc in SYNTHETIC_RC_LABEL:
        return SYNTHETIC_RC_LABEL[rc]
    return RC_LABEL.get(rc, "")


# ─────────────────────────────────────────────────────────────────────────────
# Agent recovery contract
#
# Each ErrorKind carries a machine-readable verdict so a downstream agent can
# decide its next move without parsing prose: retry the identical code, change
# the code, or escalate to a human. The tuple is
# (category, retriable, needs_code_change, needs_user_input). See
# ``schema.Recovery`` for field semantics.
# ─────────────────────────────────────────────────────────────────────────────

_RECOVERY: dict[ErrorKind, tuple[str, bool, bool, bool]] = {
    # User-code errors: the submitted code is wrong; fixing it requires editing
    # the code, and re-running unchanged will fail identically.
    ErrorKind.SYNTAX: ("user_code", False, True, False),
    ErrorKind.COMMAND_NOT_FOUND: ("user_code", False, True, False),
    ErrorKind.VARNAME_NOT_FOUND: ("user_code", False, True, False),
    ErrorKind.INVALID_NAME: ("user_code", False, True, False),
    ErrorKind.TYPE_MISMATCH: ("user_code", False, True, False),
    ErrorKind.NAME_CONFLICT: ("user_code", False, True, False),
    ErrorKind.NOT_SORTED: ("user_code", False, True, False),
    ErrorKind.NO_ESTIMATION_RESULTS: ("user_code", False, True, False),
    ErrorKind.DATA_IN_MEMORY: ("user_code", False, True, False),
    ErrorKind.MATRIX_CONFORMABILITY: ("user_code", False, True, False),
    ErrorKind.FILE_EXISTS: ("user_code", False, True, False),
    ErrorKind.ENCODING: ("user_code", False, True, False),
    # Data-state errors: the code may be fine but the sample/data does not
    # support it. Usually an `if`/`in`/missing-data fix in the code.
    ErrorKind.NO_OBSERVATIONS: ("data", False, True, False),
    ErrorKind.ESTIMATION_SAMPLE_EMPTY: ("data", False, True, False),
    ErrorKind.MATRIX_MISSING: ("data", False, True, False),
    # Model/estimation errors: respecify the model or its options.
    ErrorKind.CONVERGENCE: ("model", False, True, False),
    ErrorKind.INFEASIBLE: ("model", False, True, False),
    ErrorKind.ESTIMATION_FAILURE: ("model", False, True, False),
    ErrorKind.MATRIX_SINGULAR: ("model", False, True, False),
    # Resource limits: usually a human action (upgrade edition, raise maxvar) or
    # a data-reduction code change.
    ErrorKind.STATA_LIMIT: ("resource", False, False, True),
    ErrorKind.OUT_OF_MEMORY: ("resource", True, True, True),
    # Environment: filesystem / network. Often transient and retriable, or an
    # out-of-band fix (permissions, re-acquire a file).
    ErrorKind.NETWORK: ("environment", True, False, False),
    ErrorKind.FILE_IO: ("environment", True, False, True),
    ErrorKind.FILE_NOT_FOUND: ("environment", False, True, False),
    ErrorKind.FILE_CORRUPT: ("environment", False, False, True),
    ErrorKind.PERMISSION: ("environment", False, False, True),
    # Internal / producer-side: nothing wrong with the Stata code itself.
    ErrorKind.INTERRUPT: ("internal", True, False, False),
    ErrorKind.CANCELLED: ("internal", False, False, False),
    ErrorKind.TIMEOUT: ("internal", True, False, False),
    ErrorKind.ADAPTER_CRASH: ("internal", True, False, False),
    # Command policy blocked the code before Stata ran: the submitted code must
    # change (drop the OS-escape command), or a human must relax the policy.
    ErrorKind.POLICY_BLOCKED: ("user_code", False, True, True),
    ErrorKind.UNKNOWN: ("unknown", False, False, False),
}


def recovery_for(kind: ErrorKind) -> Recovery:
    """Return the recovery contract for an error kind.

    Always returns a ``Recovery`` (an unmapped kind yields the conservative
    ``unknown`` default), so callers never have to None-check.
    """
    category, retriable, needs_code, needs_user = _RECOVERY.get(
        kind, ("unknown", False, False, False)
    )
    return Recovery(
        category=category,  # type: ignore[arg-type]
        retriable=retriable,
        needs_code_change=needs_code,
        needs_user_input=needs_user,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Curated catalog of common Stata commands for fuzzy "did you mean" matching
# on rc 199 (command_not_found). Kept as a module constant so it's cheap to
# import and easy to extend. This is intentionally not exhaustive — it covers
# the high-traffic commands an agent is most likely to mistype.
# ─────────────────────────────────────────────────────────────────────────────

COMMON_STATA_COMMANDS: tuple[str, ...] = (
    # Estimation
    "regress",
    "logit",
    "probit",
    "areg",
    "ivregress",
    "reghdfe",
    "xtreg",
    "xtivreg",
    # Summary / display
    "summarize",
    "tabulate",
    "tabstat",
    "table",
    "list",
    "describe",
    "codebook",
    # Data manipulation
    "generate",
    "replace",
    "drop",
    "keep",
    "sort",
    "gsort",
    "by",
    "bysort",
    "merge",
    "append",
    "save",
    "use",
    "sysuse",
    "import",
    "export",
    "encode",
    "decode",
    "recode",
    "label",
    "rename",
    "reshape",
    "collapse",
    "egen",
    # Postestimation
    "predict",
    "estimates",
    "margins",
    "test",
    "testparm",
    "lincom",
    "nlcom",
    # Programming primitives. Note: `cap`/`qui`/`noi`/`mat`/`di` are
    # accepted by Stata as short forms of the spelled-out versions
    # listed here. We register only the canonical long forms because
    # difflib's fuzzy match returns at most ``n=3`` candidates per
    # mistyped token — including both short and long would crowd out
    # legitimate "did you mean" alternatives with near-duplicates.
    "matrix",
    "scalar",
    "local",
    "global",
    "display",
    "set",
    "clear",
    "exit",
    "do",
    "run",
    "capture",
    "quietly",
    "noisily",
    "foreach",
    "forvalues",
    "while",
    "if",
    "else",
    "program",
    "return",
    "ereturn",
    "postutil",
    "post",
    "postclose",
    "putexcel",
    "putdocx",
    "file",
    # Logging / I/O / shell
    "log",
    "cmdlog",
    "cd",
    "pwd",
    "mkdir",
    "dir",
    "ls",
    # Versions / help / packages
    "version",
    "which",
    "ssc",
    "net",
    "search",
    "help",
    "findit",
    "view",
    "browse",
    "edit",
    # Time-series / panel setup
    "tsset",
    "xtset",
    "stset",
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
                action=("Data must be sorted before this command. Run `sort <by-vars>` first."),
                command="sort",
            )
        )

    elif kind == ErrorKind.DATA_IN_MEMORY:
        out.append(
            Suggestion(
                action=("Data in memory would be lost. Use `clear` to discard, or save first."),
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
                action=(f"{target} already exists. Pass the `replace` option to overwrite."),
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

    elif kind == ErrorKind.INFEASIBLE:
        out.append(
            Suggestion(
                action=(
                    "No feasible starting values. Provide explicit "
                    "`from(...)` / `init(...)` values, run `ml search` to "
                    "find feasible starts, or check that the model is "
                    "identified and right-hand-side variables have no "
                    "missing values."
                ),
            )
        )

    elif kind == ErrorKind.ESTIMATION_FAILURE:
        out.append(
            Suggestion(
                action=(
                    "Estimation produced an unexpected result. Verify the "
                    "model specification and sample, confirm the prior "
                    "estimation command succeeded, and inspect the stored "
                    "results with `ereturn list`."
                ),
                command="ereturn list",
            )
        )

    elif kind == ErrorKind.TYPE_MISMATCH:
        out.append(
            Suggestion(
                action=(
                    "Type mismatch — a string and a numeric value were "
                    "combined. Check storage types with `describe`, then "
                    "convert with `destring` / `tostring` or the "
                    "`real()` / `string()` functions."
                ),
                command="describe",
            )
        )

    elif kind == ErrorKind.MATRIX_MISSING:
        out.append(
            Suggestion(
                action=(
                    "Matrix contains missing values. Inspect it with "
                    "`matrix list`, and drop or impute missing inputs "
                    "before the matrix operation."
                ),
                command="matrix list",
            )
        )

    elif kind == ErrorKind.NETWORK:
        out.append(
            Suggestion(
                action=(
                    "Network/connection problem reaching the remote host. "
                    "Check connectivity and retry; if you are behind a "
                    "proxy, configure it (see `help netio`). For "
                    "`ssc install` the SSC mirror may be briefly "
                    "unavailable — retry shortly."
                ),
            )
        )

    elif kind == ErrorKind.FILE_IO:
        out.extend(_file_io_suggestions(path))

    elif kind == ErrorKind.FILE_CORRUPT:
        target = f"`{path}`" if path else "the file"
        out.append(
            Suggestion(
                action=(
                    f"{target} is not a readable Stata file (wrong format "
                    "or corrupt). Confirm it is a genuine `.dta` and was "
                    "not truncated during transfer; re-download if needed."
                ),
            )
        )

    elif kind == ErrorKind.PERMISSION:
        target = f"`{path}`" if path else "the target file"
        out.append(
            Suggestion(
                action=(
                    f"{target} is read-only or not writable. Adjust file "
                    "permissions, close it in any other program, or write "
                    "to a different path."
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
        matches = difflib.get_close_matches(varname, available_varnames, n=3, cutoff=0.6)
        if matches:
            return [
                Suggestion(
                    action=(f"Did you mean `{cand}`? `{varname}` is not in the current dataset."),
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
        matches = difflib.get_close_matches(command, COMMON_STATA_COMMANDS, n=3, cutoff=0.65)
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


def _file_io_suggestions(path: str | None) -> list[Suggestion]:
    """Build file_io suggestions: a writability/disk hint."""
    target = f"`{path}`" if path else "the file"
    return [
        Suggestion(
            action=(
                f"Could not read or write {target}. Check that the "
                "directory exists and is writable, that the disk is not "
                "full, and that no other program holds the file open."
            ),
            command="pwd",
        )
    ]


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
                    f"If you meant a Stata dataset, try `{path}.dta`. "
                    f"If you meant a do-file, try `{path}.do`."
                ),
            )
        )
    return out
