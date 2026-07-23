"""Best-effort static linting for Stata do-file source.

The point is token economy: let an agent catch a whole class of mistakes —
unbalanced braces, a ``program`` block with no ``end``, a dangling ``///``
continuation — *before* spending a Stata run to discover them. It is a
lightweight syntactic check, not a parser: it deliberately errs toward silence
(few, high-confidence findings) so it never nags about correct code.

Every finding carries a ``rule`` id, a 1-based ``line``, and a ``severity``
(``error`` = will almost certainly fail at runtime; ``warning`` = suspicious).
The linter never executes anything and needs neither Stata nor pystata.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

Severity = Literal["error", "warning"]

# Stata string forms: plain "..." and compound `"..."'. Stripping them before
# structural checks stops SMCL directives inside display strings (di "{hline}")
# and quoted braces from being counted as code structure.
_COMPOUND_STR_RE = re.compile(r"`\"(?:[^\"]|\"(?!'))*\"'")
_PLAIN_STR_RE = re.compile(r'"[^"]*"')
# Global macro reference ${name} — braces here are macro syntax, not blocks.
_MACRO_BRACE_RE = re.compile(r"\$\{[^}]*\}")
_LINE_COMMENT_RE = re.compile(r"(^|\s)//.*$")
_STAR_COMMENT_RE = re.compile(r"^\s*\*")
_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# Prefixes stripped before reading a command token (mirrors policy._PREFIXES,
# kept local so the two modules can diverge).
_PREFIXES: frozenset[str] = frozenset(
    {"capture", "cap", "quietly", "quiet", "quie", "qui", "noisily", "noisi", "nois", "noi", "by", "bysort"}
)

# `program` sub-commands that do NOT open a block.
_PROGRAM_NON_OPENERS: frozenset[str] = frozenset({"drop", "dir", "list", "define"})


@dataclass(frozen=True)
class LintFinding:
    """One static-analysis finding."""

    rule: str
    severity: Severity
    line: int
    message: str

    def to_dict(self) -> dict[str, object]:
        return {
            "rule": self.rule,
            "severity": self.severity,
            "line": self.line,
            "message": self.message,
        }


def _strip_strings_and_macros(line: str) -> str:
    line = _COMPOUND_STR_RE.sub(" ", line)
    line = _PLAIN_STR_RE.sub(" ", line)
    line = _MACRO_BRACE_RE.sub(" ", line)
    return line


def _strip_comment(line: str) -> str:
    return _LINE_COMMENT_RE.sub("", line)


def _command_token(line: str) -> str | None:
    """First real command token on a line, after stripping known prefixes."""
    rest = line.strip()
    while rest:
        match = _WORD_RE.match(rest)
        if match is None:
            return None
        word = match.group(0).lower()
        after = rest[match.end() :].lstrip()
        if word in _PREFIXES:
            rest = after[1:].lstrip() if after.startswith(":") else after
            continue
        return word
    return None


def _check_braces(lines: list[str]) -> list[LintFinding]:
    findings: list[LintFinding] = []
    depth = 0
    first_open_line = 0
    for lineno, raw in enumerate(lines, start=1):
        if _STAR_COMMENT_RE.match(raw):
            continue
        cleaned = _strip_strings_and_macros(_strip_comment(raw))
        for ch in cleaned:
            if ch == "{":
                if depth == 0:
                    first_open_line = lineno
                depth += 1
            elif ch == "}":
                if depth == 0:
                    findings.append(
                        LintFinding(
                            rule="unbalanced-braces",
                            severity="error",
                            line=lineno,
                            message="Closing brace `}` with no matching opening `{`.",
                        )
                    )
                else:
                    depth -= 1
    if depth > 0:
        findings.append(
            LintFinding(
                rule="unbalanced-braces",
                severity="error",
                line=first_open_line or len(lines),
                message=(
                    f"{depth} opening brace(s) `{{` never closed — a block "
                    "(foreach / forvalues / while / if / program) is missing its `}`."
                ),
            )
        )
    return findings


def _check_program_blocks(lines: list[str]) -> list[LintFinding]:
    """Flag ``program`` / bare ``mata`` / bare ``python`` blocks with no ``end``.

    Conservative on mata/python: only a bare opener (nothing meaningful after the
    keyword or its colon) is treated as a block, since ``mata: <expr>`` and
    ``python: <stmt>`` are one-liners that need no ``end``.
    """
    findings: list[LintFinding] = []
    open_stack: list[tuple[int, str]] = []  # (line, keyword)
    for lineno, raw in enumerate(lines, start=1):
        if _STAR_COMMENT_RE.match(raw):
            continue
        stripped = _strip_comment(raw).strip()
        if not stripped:
            continue
        token = _command_token(stripped)
        if token is None:
            continue
        lowered = stripped.lower()
        if token in ("program", "pr", "prog", "progr", "progra") and _opens_program(stripped):
            open_stack.append((lineno, "program"))
        elif token == "mata" and _opens_bare_block(lowered, "mata"):
            open_stack.append((lineno, "mata"))
        elif token == "python" and _opens_bare_block(lowered, "python"):
            open_stack.append((lineno, "python"))
        elif token == "end":
            if open_stack:
                open_stack.pop()
            else:
                findings.append(
                    LintFinding(
                        rule="unexpected-end",
                        severity="warning",
                        line=lineno,
                        message="`end` with no open `program` / `mata` / `python` block.",
                    )
                )
    for lineno, keyword in open_stack:
        findings.append(
            LintFinding(
                rule="missing-end",
                severity="error",
                line=lineno,
                message=f"`{keyword}` block opened here is never closed with `end`.",
            )
        )
    return findings


def _opens_program(stripped: str) -> bool:
    tokens = stripped.split()
    if len(tokens) < 2:
        # bare `program` is a syntax error, not a block opener we can trust
        return False
    second = tokens[1].lower().rstrip(",")
    if second in _PROGRAM_NON_OPENERS and second != "define":
        return False
    return True


def _opens_bare_block(lowered: str, keyword: str) -> bool:
    rest = lowered[len(keyword) :].strip()
    if rest in ("", ":"):
        return True
    # `mata:` / `python:` followed by code on the same line is a one-liner.
    return False


def _check_dangling_continuation(lines: list[str]) -> list[LintFinding]:
    last_idx = -1
    for idx in range(len(lines) - 1, -1, -1):
        if lines[idx].strip():
            last_idx = idx
            break
    if last_idx < 0:
        return []
    # Check the RAW line: `///` is itself a continuation-comment marker, so
    # comment-stripping would remove the very token we are looking for.
    if lines[last_idx].rstrip().endswith("///"):
        return [
            LintFinding(
                rule="dangling-continuation",
                severity="warning",
                line=last_idx + 1,
                message="Last line ends with `///`; the continued command has no following line.",
            )
        ]
    return []


def lint_code(code: str) -> list[LintFinding]:
    """Return static-analysis findings for Stata source, ordered by line.

    Empty / comment-only input yields a single ``empty-input`` warning so a
    caller can distinguish "clean" from "nothing to lint".
    """
    lines = code.replace("\r\n", "\n").split("\n")
    has_code = any(
        stripped and not _STAR_COMMENT_RE.match(raw) and not _strip_comment(raw).strip() == ""
        for raw, stripped in ((ln, ln.strip()) for ln in lines)
    )
    if not has_code:
        return [
            LintFinding(
                rule="empty-input",
                severity="warning",
                line=1,
                message="No executable Stata commands found.",
            )
        ]
    findings: list[LintFinding] = []
    findings += _check_braces(lines)
    findings += _check_program_blocks(lines)
    findings += _check_dangling_continuation(lines)
    findings.sort(key=lambda f: (f.line, f.rule))
    return findings


__all__ = ["LintFinding", "Severity", "lint_code"]
