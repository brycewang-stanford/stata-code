"""Helpers for navigating and editing Jupyter notebooks (.ipynb).

stata-code's MCP execution protocol stays cell-agnostic: `stata_run` accepts a
string and returns a `RunResult`. This module provides side-channel tools so
agents can navigate, search, and atomically edit a notebook without pulling
the whole file into context or risking JSON corruption.

Phase 1 (read-only):

- :func:`outline_notebook` — one-line preview per cell (id, type, head)
- :func:`get_cell` — single cell's full source plus a token-economic summary
  of its outputs

Phase 2 (search + atomic edits):

- :func:`locate_cells` — find cells by exact snippet, regex, or error text
- :func:`edit_cell` — atomically replace one cell's source; preserves the
  cell's ``id`` and ``metadata``; clears ``outputs`` and ``execution_count``
- :func:`insert_cell` — insert a new cell after/before an anchor; assigns a
  fresh nbformat 4.5+ UUID
- :func:`delete_cell` — remove a cell by id

Edits write the whole notebook atomically (temp file + rename). The runner
itself never mutates the notebook — only these explicit edit calls do.

Cell identity follows nbformat 4.5+: every cell SHOULD have a stable ``id``
field. For older notebooks (pre-4.5) we synthesise a deterministic id from
the cell's array index plus a short hash of its source so agents still get
a stable handle within a single read; ``id_synthesized=True`` flags this so
callers can warn the user.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import uuid
from pathlib import Path
from typing import Any, Literal

CellType = Literal["code", "markdown", "raw", "unknown"]

# How many characters of an output's text payload to keep in summaries before
# truncating. Aligned with the log head/tail token economy elsewhere in the
# project — outputs can be megabytes, agents only need a fingerprint.
_MAX_OUTPUT_TEXT_CHARS = 4000
_MAX_TRACEBACK_HEAD = 20
_MAX_TRACEBACK_TAIL = 20
_PREVIEW_LINES_DEFAULT = 2
_PREVIEW_CHARS_PER_LINE = 120


# ─────────────────────────────────────────────────────────────────────────────
# Loading
# ─────────────────────────────────────────────────────────────────────────────


class NotebookError(ValueError):
    """Raised for any problem reading or interpreting a notebook file.

    The message always starts with a stable kind token followed by ``": "``
    (e.g. ``"notebook_not_found: …"``). Use the :pyattr:`kind` property to
    map it to a typed enum without parsing the message yourself.
    """

    @property
    def kind(self) -> str:
        """Return the kind token, or ``"notebook_error"`` if the message
        does not follow the ``"<kind>: …"`` convention."""
        message = str(self)
        token, sep, _ = message.partition(":")
        token = token.strip()
        if not sep or not token:
            return "notebook_error"
        return token


def load_notebook(path: str | Path) -> dict[str, Any]:
    """Load a ``.ipynb`` file as a JSON dict.

    Raises NotebookError with a stable ``kind`` prefix on any failure so MCP
    callers can surface a typed message instead of a stack trace.
    """
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = p.resolve()
    if not p.exists():
        raise NotebookError(f"notebook_not_found: {p}")
    if not p.is_file():
        raise NotebookError(f"notebook_not_file: {p}")
    try:
        # ``utf-8-sig`` strips an optional leading BOM (some Windows editors and
        # Git configurations write one) while leaving BOM-less files untouched.
        # Plain ``utf-8`` would surface a valid-but-BOM'd notebook as
        # ``notebook_invalid_json`` — a false "your notebook is corrupt" that
        # would derail an agent mid repair loop. Note the round-trip is
        # intentionally BOM-less: ``_atomic_write_notebook`` writes plain UTF-8,
        # matching Jupyter's own saver.
        text = p.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise NotebookError(f"notebook_io_error: {exc}") from exc
    try:
        nb = json.loads(text)
    except json.JSONDecodeError as exc:
        raise NotebookError(f"notebook_invalid_json: {exc}") from exc
    if not isinstance(nb, dict) or "cells" not in nb or not isinstance(nb["cells"], list):
        raise NotebookError(
            "notebook_invalid_structure: top-level object must have a 'cells' list"
        )
    return nb


# ─────────────────────────────────────────────────────────────────────────────
# Cell identity / source normalisation
# ─────────────────────────────────────────────────────────────────────────────


def _source_to_str(src: Any) -> str:
    """nbformat allows ``source`` to be a string or list of strings."""
    if isinstance(src, list):
        return "".join(s for s in src if isinstance(s, str))
    if isinstance(src, str):
        return src
    return ""


def _normalize_newlines(text: str) -> str:
    """Collapse CRLF / CR to LF.

    Used to compare ``expected_source`` against the on-disk source: a notebook
    authored on Windows may carry ``\\r\\n`` line endings while the agent
    reconstructs ``expected_source`` with ``\\n`` (or vice versa). Comparing
    raw would raise a spurious ``*_source_drift`` even though the content is
    identical, which is a frequent false alarm in a Windows repair loop."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _synth_cell_id(index: int, source: str) -> str:
    """Deterministic id for pre-nbformat-4.5 cells (no native ``id`` field).

    Format: ``synth-<index>-<8 hex chars of source hash>``. Stable across
    repeated reads of the same on-disk content; changes if either the cell
    index or its source changes — that's acceptable because callers are told
    via ``id_synthesized=True`` that this id is not a real notebook handle.
    """
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:8]
    return f"synth-{index}-{digest}"


def _cell_id(cell: dict[str, Any], index: int, source: str) -> tuple[str, bool]:
    raw = cell.get("id")
    if isinstance(raw, str) and raw:
        return raw, False
    return _synth_cell_id(index, source), True


def _cell_type(cell: dict[str, Any]) -> CellType:
    t = cell.get("cell_type")
    if t == "code":
        return "code"
    if t == "markdown":
        return "markdown"
    if t == "raw":
        return "raw"
    return "unknown"


def _source_preview(source: str, max_lines: int) -> str:
    if not source:
        return ""
    lines = source.split("\n")
    head = lines[:max_lines]
    truncated = [
        (ln if len(ln) <= _PREVIEW_CHARS_PER_LINE else ln[: _PREVIEW_CHARS_PER_LINE - 1] + "…")
        for ln in head
    ]
    return "\n".join(truncated)


# ─────────────────────────────────────────────────────────────────────────────
# Outline (cheap, all cells)
# ─────────────────────────────────────────────────────────────────────────────


def outline_notebook(
    path: str | Path,
    *,
    preview_lines: int = _PREVIEW_LINES_DEFAULT,
) -> dict[str, Any]:
    """Return a compact per-cell index of a notebook.

    The returned dict is shaped for direct use as the MCP tool result:

        {
            "path": <absolute path>,
            "nbformat": <int|None>,
            "kernelspec": {"name", "display_name", "language"} | None,
            "cell_count": int,
            "cells": [
                {
                    "cell_id": str,
                    "id_synthesized": bool,
                    "index": int,
                    "cell_type": "code"|"markdown"|"raw"|"unknown",
                    "source_preview": str,        # first preview_lines lines
                    "line_count": int,
                    "char_count": int,
                    "execution_count": int | None,
                    "has_outputs": bool,
                    "has_error_output": bool,
                },
                ...
            ],
        }
    """
    nb = load_notebook(path)
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = p.resolve()

    cells_out: list[dict[str, Any]] = []
    for index, cell in enumerate(nb["cells"]):
        if not isinstance(cell, dict):
            continue
        source = _source_to_str(cell.get("source"))
        cell_id, synthesized = _cell_id(cell, index, source)
        ctype = _cell_type(cell)
        outputs = cell.get("outputs") if ctype == "code" else None
        outputs_list = outputs if isinstance(outputs, list) else []
        has_error = any(
            isinstance(o, dict) and o.get("output_type") == "error"
            for o in outputs_list
        )
        exec_count = cell.get("execution_count") if ctype == "code" else None
        if not isinstance(exec_count, int):
            exec_count = None
        cells_out.append(
            {
                "cell_id": cell_id,
                "id_synthesized": synthesized,
                "index": index,
                "cell_type": ctype,
                "source_preview": _source_preview(source, preview_lines),
                "line_count": source.count("\n") + (1 if source and not source.endswith("\n") else 0),
                "char_count": len(source),
                "execution_count": exec_count,
                "has_outputs": bool(outputs_list),
                "has_error_output": has_error,
            }
        )

    metadata = nb.get("metadata") if isinstance(nb.get("metadata"), dict) else {}
    kernelspec = metadata.get("kernelspec") if isinstance(metadata, dict) else None
    if isinstance(kernelspec, dict):
        ks = {
            "name": kernelspec.get("name"),
            "display_name": kernelspec.get("display_name"),
            "language": kernelspec.get("language"),
        }
    else:
        ks = None

    return {
        "path": str(p),
        "nbformat": nb.get("nbformat") if isinstance(nb.get("nbformat"), int) else None,
        "kernelspec": ks,
        "cell_count": len(cells_out),
        "cells": cells_out,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Single-cell detail
# ─────────────────────────────────────────────────────────────────────────────


def _output_text_payload(output: dict[str, Any]) -> str | None:
    """Extract a text-ish representation of one output for summarisation.

    Order of preference:
    - stream output's ``text``
    - ``data['text/plain']``
    - error's ``ename: evalue``
    """
    otype = output.get("output_type")
    if otype == "stream":
        text = output.get("text")
        if isinstance(text, list):
            return "".join(s for s in text if isinstance(s, str))
        if isinstance(text, str):
            return text
        return None
    data = output.get("data") if isinstance(output.get("data"), dict) else None
    if isinstance(data, dict):
        plain = data.get("text/plain")
        if isinstance(plain, list):
            return "".join(s for s in plain if isinstance(s, str))
        if isinstance(plain, str):
            return plain
    if otype == "error":
        ename = output.get("ename") or "Error"
        evalue = output.get("evalue") or ""
        return f"{ename}: {evalue}"
    return None


def _output_has_image(output: dict[str, Any]) -> bool:
    data = output.get("data")
    if not isinstance(data, dict):
        return False
    return any(k.startswith("image/") for k in data)


def _summarise_outputs(outputs: list[Any]) -> dict[str, Any]:
    """Token-economic summary of one cell's outputs.

    ``count`` is the raw output-array length (so non-dict garbage entries are
    still counted); ``types`` only includes well-formed dict outputs, so
    ``count >= len(types)`` always.

    Streaming-truncation contract: we keep accumulating ``text_chars_total``
    across every output (so the caller knows how much was elided) but stop
    appending to ``text_preview_parts`` once the ``_MAX_OUTPUT_TEXT_CHARS``
    budget is hit. A pathological cell with 50 stream events × 2 KB is now
    O(budget + per-output overhead) in memory, not O(total).
    """
    types: list[str] = []
    has_image = False
    error_summary: dict[str, Any] | None = None
    text_preview_parts: list[str] = []
    remaining_budget = _MAX_OUTPUT_TEXT_CHARS
    chars_total = 0

    for o in outputs:
        if not isinstance(o, dict):
            continue
        otype = o.get("output_type", "unknown")
        if isinstance(otype, str):
            types.append(otype)
        if _output_has_image(o):
            has_image = True
        if otype == "error" and error_summary is None:
            tb = o.get("traceback")
            tb_lines = tb if isinstance(tb, list) else []
            head = [ln for ln in tb_lines[:_MAX_TRACEBACK_HEAD] if isinstance(ln, str)]
            tail_start = max(_MAX_TRACEBACK_HEAD, len(tb_lines) - _MAX_TRACEBACK_TAIL)
            tail = [ln for ln in tb_lines[tail_start:] if isinstance(ln, str)]
            error_summary = {
                "ename": o.get("ename"),
                "evalue": o.get("evalue"),
                "traceback_head": head,
                "traceback_tail": tail,
                "traceback_lines_total": len(tb_lines),
                "traceback_truncated": len(tb_lines) > _MAX_TRACEBACK_HEAD + _MAX_TRACEBACK_TAIL,
            }
        payload = _output_text_payload(o)
        if not payload:
            continue
        chars_total += len(payload)
        if remaining_budget > 0:
            take = payload[:remaining_budget]
            text_preview_parts.append(take)
            remaining_budget -= len(take)

    truncated = chars_total > _MAX_OUTPUT_TEXT_CHARS
    text_preview = "".join(text_preview_parts)
    if truncated:
        text_preview += "…[truncated]"

    return {
        "count": len(outputs),
        "types": types,
        "has_image": has_image,
        "has_error": error_summary is not None,
        "error": error_summary,
        "text_preview": text_preview,
        "text_chars_total": chars_total,
        "text_truncated": truncated,
    }


def _resolve_cell(
    cells: list[Any],
    *,
    cell_id: str | None,
    cell_index: int | None,
) -> tuple[int, dict[str, Any]]:
    if cell_id is None and cell_index is None:
        raise NotebookError("cell_locator_required: provide cell_id or cell_index")

    if cell_index is not None:
        if not isinstance(cell_index, int):
            raise NotebookError(f"cell_index_invalid: must be int, got {type(cell_index).__name__}")
        if cell_index < 0 or cell_index >= len(cells):
            raise NotebookError(
                f"cell_index_out_of_range: {cell_index} not in [0, {len(cells)})"
            )
        cell = cells[cell_index]
        if not isinstance(cell, dict):
            raise NotebookError(f"cell_malformed: index {cell_index}")
        if cell_id is not None:
            source = _source_to_str(cell.get("source"))
            actual_id, _ = _cell_id(cell, cell_index, source)
            if actual_id != cell_id:
                raise NotebookError(
                    f"cell_id_index_mismatch: index {cell_index} has id "
                    f"{actual_id!r}, not {cell_id!r}"
                )
        return cell_index, cell

    # cell_id only: scan, supporting both real and synth ids. nbformat 4.5+
    # requires ids to be unique, but hand-merged or tool-generated notebooks do
    # produce duplicates. Returning the *first* match would silently edit or
    # delete the wrong cell — exactly the messy-notebook repair scenario these
    # tools exist for — so surface the ambiguity as a typed error instead.
    found: tuple[int, dict[str, Any]] | None = None
    for index, cell in enumerate(cells):
        if not isinstance(cell, dict):
            continue
        source = _source_to_str(cell.get("source"))
        actual_id, _ = _cell_id(cell, index, source)
        if actual_id == cell_id:
            if found is not None:
                raise NotebookError(
                    f"cell_id_ambiguous: cell_id={cell_id!r} matches cells at "
                    f"indices {found[0]} and {index}; the notebook has duplicate "
                    "cell ids. Pass cell_index to target one (get_cell), or "
                    "resolve the duplication before editing."
                )
            found = (index, cell)
    if found is not None:
        return found
    raise NotebookError(f"cell_not_found: cell_id={cell_id!r}")


def get_cell(
    path: str | Path,
    *,
    cell_id: str | None = None,
    cell_index: int | None = None,
) -> dict[str, Any]:
    """Return one cell's source plus a token-economic outputs summary.

    Exactly one of ``cell_id`` or ``cell_index`` is required; both are
    accepted (they must agree, otherwise ``cell_id_index_mismatch``).
    """
    nb = load_notebook(path)
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = p.resolve()

    index, cell = _resolve_cell(
        nb["cells"], cell_id=cell_id, cell_index=cell_index
    )
    source = _source_to_str(cell.get("source"))
    actual_id, synthesized = _cell_id(cell, index, source)
    ctype = _cell_type(cell)
    outputs = cell.get("outputs") if ctype == "code" else None
    outputs_list = outputs if isinstance(outputs, list) else []
    exec_count = cell.get("execution_count") if ctype == "code" else None
    if not isinstance(exec_count, int):
        exec_count = None
    metadata = cell.get("metadata") if isinstance(cell.get("metadata"), dict) else {}

    return {
        "path": str(p),
        "cell_id": actual_id,
        "id_synthesized": synthesized,
        "index": index,
        "cell_type": ctype,
        "source": source,
        "line_count": source.count("\n") + (1 if source and not source.endswith("\n") else 0),
        "char_count": len(source),
        "execution_count": exec_count,
        "metadata": metadata,
        "outputs_summary": _summarise_outputs(outputs_list) if ctype == "code" else None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Locate (Phase 2 — search)
# ─────────────────────────────────────────────────────────────────────────────


_LOCATE_LIMIT_DEFAULT = 10
_LOCATE_LIMIT_MAX = 100
_LOCATE_PREVIEW_LINES = 3
_ERROR_TEXT_LINE_MIN_LEN = 8
# Upper bound on how many characters of one cell's source the regex engine
# scans. See :func:`_score_regex` for the rationale.
_REGEX_MAX_SCAN_CHARS = 1_000_000


def _line_with_match(source: str, idx: int) -> tuple[int, str]:
    """Return (1-based line number, line text) for a character offset."""
    line_no = source.count("\n", 0, idx) + 1
    line_start = source.rfind("\n", 0, idx) + 1
    line_end = source.find("\n", idx)
    line_text = source[line_start:] if line_end == -1 else source[line_start:line_end]
    return line_no, line_text


def _score_snippet(source: str, snippet: str) -> tuple[float, int | None]:
    """Score a cell against a literal snippet.

    Returns ``(score in [0, 1], line_number)``. Higher is better; 0 means
    no match. Exact-substring match is 1.0; longest-common-line match scores
    by `match_chars / snippet_chars`. Whitespace is normalised on each line
    so leading-indent shifts don't tank the score.
    """
    if not snippet:
        return 0.0, None
    needle = snippet.strip()
    if not needle:
        return 0.0, None

    idx = source.find(needle)
    if idx >= 0:
        line_no, _ = _line_with_match(source, idx)
        return 1.0, line_no

    # Fallback: per-line containment of any non-empty line of `snippet`.
    snippet_lines = [ln.strip() for ln in needle.split("\n") if ln.strip()]
    if not snippet_lines:
        return 0.0, None
    # Score against the non-blank content only; blank separators in `needle`
    # would otherwise inflate the denominator and tank otherwise-good matches.
    needle_chars = sum(len(ln) for ln in snippet_lines)

    best_score = 0.0
    best_line: int | None = None
    source_lines = source.split("\n")
    for src_idx, src_line in enumerate(source_lines, start=1):
        norm = src_line.strip()
        if not norm:
            continue
        for snip_line in snippet_lines:
            if snip_line and snip_line in norm:
                score = len(snip_line) / max(needle_chars, 1)
                if score > best_score:
                    best_score = score
                    best_line = src_idx
                    if best_score >= 0.99:
                        return best_score, best_line
    return best_score, best_line


def _score_regex(source: str, pattern: re.Pattern[str]) -> tuple[float, int | None]:
    # Bound the haystack the backtracking `re` engine sees. The pattern is
    # supplied by a trusted agent, not untrusted external input, so this is
    # defense-in-depth: it keeps an accidentally-huge cell (e.g. a multi-MB
    # pasted data dump) from feeding an unbounded string to `re`. It does NOT
    # make matching safe against a deliberately catastrophic pattern — stdlib
    # `re` offers no match timeout — which is acceptable for a trusted caller.
    m = pattern.search(source[:_REGEX_MAX_SCAN_CHARS])
    if m is None:
        return 0.0, None
    line_no, _ = _line_with_match(source, m.start())
    return 1.0, line_no


def _score_error_text(source: str, error_text: str) -> tuple[float, int | None]:
    """Use error text (e.g. a Stata traceback) as a content fingerprint.

    Strategy: extract substrings that look like Stata commands or identifiers
    from `error_text` and find the longest one that occurs in `source`. This
    is the workflow from the design notes — the user pastes a failure log,
    the agent uses ``error.context.failing`` (or analogous text) to locate
    the originating cell.
    """
    if not error_text:
        return 0.0, None

    # Candidate fingerprints: lines that look code-like (have alphanumerics
    # and aren't pure punctuation/traceback markers). Sort by length desc so
    # we try the most specific first.
    candidates: list[str] = []
    for raw in error_text.split("\n"):
        s = raw.strip()
        if len(s) < _ERROR_TEXT_LINE_MIN_LEN:
            continue
        if not any(c.isalnum() for c in s):
            continue
        # Drop a leading "r(123);" / "Traceback (..." / similar framing.
        s = re.sub(r"^(r\(\d+\);|>|\.\s*|--+\s*|\*+\s*)", "", s).strip()
        if len(s) < _ERROR_TEXT_LINE_MIN_LEN:
            continue
        candidates.append(s)
    candidates.sort(key=len, reverse=True)

    for s in candidates:
        idx = source.find(s)
        if idx >= 0:
            line_no, _ = _line_with_match(source, idx)
            # Capped strictly below 1.0: an error-text fingerprint is a fuzzy,
            # heuristic match, never as certain as an exact snippet or a regex
            # hit (both of which score 1.0). Keeping it below 1.0 lets an agent
            # comparing results across calls tell "located by fingerprint" from
            # "located exactly" instead of treating them as equally confident.
            return min(0.95, len(s) / 80.0 + 0.5), line_no
    return 0.0, None


def locate_cells(
    path: str | Path,
    *,
    snippet: str | None = None,
    regex: str | None = None,
    error_text: str | None = None,
    cell_type: str | None = None,
    limit: int = _LOCATE_LIMIT_DEFAULT,
) -> dict[str, Any]:
    """Find cells in a notebook by content.

    Exactly one of ``snippet`` / ``regex`` / ``error_text`` is required:

    - ``snippet`` — literal substring match (preferred for quoting code lines).
      Whitespace-normalised line-by-line fallback if the exact match fails.
    - ``regex`` — Python regex applied to the cell source (multiline mode).
      The pattern is trusted (agent-supplied); only the first ~1,000,000
      characters of each cell are scanned as a defensive bound.
    - ``error_text`` — pasted Stata/traceback text; the longest code-like line
      is treated as a fingerprint and located in the notebook.

    Optional ``cell_type`` filters to ``"code"``, ``"markdown"``, or ``"raw"``.

    Returns up to ``limit`` candidates, sorted by descending score:

        {
            "path": <abs path>,
            "query": {"snippet"|"regex"|"error_text": ...},
            "match_count": int,
            "matches": [
                {
                    "cell_id": str,
                    "id_synthesized": bool,
                    "index": int,
                    "cell_type": str,
                    "score": float,           # in (0, 1]
                    "line_in_cell": int|null, # 1-based, where the match was found
                    "preview": str,           # ±1 line around the match, ≤3 lines
                },
                ...
            ],
        }
    """
    provided = [
        ("snippet", snippet),
        ("regex", regex),
        ("error_text", error_text),
    ]
    chosen = [(k, v) for k, v in provided if v]
    if len(chosen) == 0:
        raise NotebookError(
            "locate_query_required: provide snippet, regex, or error_text"
        )
    if len(chosen) > 1:
        raise NotebookError(
            "locate_query_conflict: pass exactly one of snippet/regex/error_text"
        )
    if not isinstance(limit, int) or limit < 1:
        raise NotebookError("locate_limit_invalid: limit must be a positive integer")
    if limit > _LOCATE_LIMIT_MAX:
        limit = _LOCATE_LIMIT_MAX
    if cell_type is not None and cell_type not in ("code", "markdown", "raw"):
        raise NotebookError(
            f"cell_type_invalid: must be code|markdown|raw, got {cell_type!r}"
        )

    nb = load_notebook(path)
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = p.resolve()

    compiled_regex: re.Pattern[str] | None = None
    if regex is not None:
        try:
            compiled_regex = re.compile(regex, re.MULTILINE)
        except re.error as exc:
            raise NotebookError(f"locate_regex_invalid: {exc}") from exc

    matches: list[dict[str, Any]] = []
    for index, cell in enumerate(nb["cells"]):
        if not isinstance(cell, dict):
            continue
        ctype = _cell_type(cell)
        if cell_type is not None and ctype != cell_type:
            continue
        source = _source_to_str(cell.get("source"))
        if not source:
            continue

        if snippet is not None:
            score, line_no = _score_snippet(source, snippet)
        elif compiled_regex is not None:
            score, line_no = _score_regex(source, compiled_regex)
        else:
            score, line_no = _score_error_text(source, error_text or "")

        if score <= 0.0:
            continue

        cell_id, synthesized = _cell_id(cell, index, source)
        preview = _preview_around(source, line_no, _LOCATE_PREVIEW_LINES)
        matches.append(
            {
                "cell_id": cell_id,
                "id_synthesized": synthesized,
                "index": index,
                "cell_type": ctype,
                "score": round(score, 4),
                "line_in_cell": line_no,
                "preview": preview,
            }
        )

    matches.sort(key=lambda m: (-m["score"], m["index"]))
    matches = matches[:limit]

    query: dict[str, str] = {chosen[0][0]: chosen[0][1] or ""}
    return {
        "path": str(p),
        "query": query,
        "match_count": len(matches),
        "matches": matches,
    }


def _preview_around(source: str, line_no: int | None, max_lines: int) -> str:
    if line_no is None:
        return _source_preview(source, max_lines)
    lines = source.split("\n")
    half = max_lines // 2
    start = max(0, line_no - 1 - half)
    end = min(len(lines), line_no - 1 + (max_lines - half))
    chunk = lines[start:end]
    truncated = [
        (
            ln
            if len(ln) <= _PREVIEW_CHARS_PER_LINE
            else ln[: _PREVIEW_CHARS_PER_LINE - 1] + "…"
        )
        for ln in chunk
    ]
    return "\n".join(truncated)


# ─────────────────────────────────────────────────────────────────────────────
# Edit / insert / delete (Phase 2 — atomic mutations)
# ─────────────────────────────────────────────────────────────────────────────


_VALID_NEW_CELL_TYPES = ("code", "markdown", "raw")


def _resolve_path(path: str | Path) -> Path:
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = p.resolve()
    return p


def _path_signature(path: Path) -> tuple[int, int] | None:
    """Return ``(mtime_ns, size)`` for ``path``, or ``None`` if it can't be
    stat'd. Used as a cheap change-detection token for the lost-update guard in
    :func:`_atomic_write_notebook`."""
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def _fsync_dir(dir_path: Path) -> None:
    """Best-effort fsync of a directory so a rename is durable across a crash.

    No-op on Windows (directory handles cannot be fsynced there) and on any
    platform where opening the directory fd fails — durability is a hardening
    bonus, never a hard requirement for the write to succeed.
    """
    if os.name == "nt":
        return
    try:
        dir_fd = os.open(str(dir_path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dir_fd)
    except OSError:
        pass
    finally:
        os.close(dir_fd)


def _atomic_write_notebook(
    path: Path,
    nb: dict[str, Any],
    *,
    prev_signature: tuple[int, int] | None = None,
) -> None:
    """Write the notebook JSON atomically (temp file + rename in same dir).

    Uses ``json.dumps`` with ``indent=1`` to match the convention Jupyter
    uses on save (small diffs, line-stable). Trailing newline matches the
    nbformat saver behaviour.

    Durability: the temp file's bytes are flushed and ``fsync``'d before the
    rename, and the parent directory is ``fsync``'d after, so a crash between
    write and rename cannot leave a truncated ``.ipynb`` in place of the
    original. ``os.replace`` only guarantees the rename is atomic, not that
    the new bytes reached disk first.

    Lost-update guard: when ``prev_signature`` (the ``(mtime_ns, size)`` the
    caller captured right after it read the notebook) is supplied, the file is
    re-stat'd immediately before the rename. If it changed in between — e.g. a
    concurrent ``edit_cell`` from another session committed first — the write
    is aborted with ``notebook_changed_on_disk`` rather than silently
    clobbering that other write. This narrows, but does not fully close, the
    read-modify-write race (a sub-millisecond window remains between the
    re-stat and the rename); it converts the common concurrent-edit case from
    silent data loss into a typed, retryable error.
    """
    # Match Jupyter / nbformat.writes: indent=1 with no trailing space after
    # commas, so saved-by-stata-code notebooks diff cleanly against
    # saved-by-Jupyter notebooks.
    serialised = json.dumps(
        nb, indent=1, separators=(",", ": "), ensure_ascii=False
    )
    if not serialised.endswith("\n"):
        serialised += "\n"
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(serialised)
            f.flush()
            os.fsync(f.fileno())
        if prev_signature is not None:
            current = _path_signature(path)
            if current is not None and current != prev_signature:
                raise NotebookError(
                    "notebook_changed_on_disk: the notebook was modified since "
                    "it was read; re-read the cell and retry the edit"
                )
        os.replace(tmp_name, path)
        _fsync_dir(path.parent)
    except Exception:
        # Best-effort cleanup of the temp file on any write/rename failure
        # (including the lost-update abort above), so we never leave a stray
        # ``.nb.ipynb.*.tmp`` next to the notebook.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _new_cell_id() -> str:
    """RFC 4122 UUID4 hex — matches Jupyter's nbformat 4.5+ id format."""
    return str(uuid.uuid4())


def _build_cell(
    *,
    cell_type: str,
    source: str,
    cell_id: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if cell_type not in _VALID_NEW_CELL_TYPES:
        raise NotebookError(
            f"cell_type_invalid: must be {'|'.join(_VALID_NEW_CELL_TYPES)}, "
            f"got {cell_type!r}"
        )
    cell: dict[str, Any] = {
        "cell_type": cell_type,
        "id": cell_id,
        "metadata": metadata or {},
        "source": source,
    }
    if cell_type == "code":
        cell["execution_count"] = None
        cell["outputs"] = []
    return cell


def _ensure_native_id(cell: dict[str, Any]) -> str:
    """Make sure a cell has a real ``id`` field. Returns the id we used.

    If the notebook is pre-4.5 (cell lacks ``id``), upgrade the cell in place
    by assigning a fresh UUID. We do this only when an edit/delete actually
    needs a stable handle and the caller addressed the cell by its synthesised
    id (so the user is intentionally working with this cell).
    """
    raw = cell.get("id")
    if isinstance(raw, str) and raw:
        return raw
    new_id = _new_cell_id()
    cell["id"] = new_id
    return new_id


def _upgrade_all_pre_45_ids(cells: list[Any]) -> int:
    """Assign a fresh UUID to every cell that lacks a native ``id``.

    Synthesised ids are derived from the cell's array index; any structural
    mutation (insert / delete) shifts indices, silently invalidating every
    synth id the caller is holding. Upgrading the whole notebook to nbformat
    4.5+ ids on first mutation makes every cell handle stable from then on.

    Returns the number of cells that were upgraded.
    """
    upgraded = 0
    for cell in cells:
        if not isinstance(cell, dict):
            continue
        raw = cell.get("id")
        if isinstance(raw, str) and raw:
            continue
        cell["id"] = _new_cell_id()
        upgraded += 1
    return upgraded


def _ensure_nbformat_minor_5(nb: dict[str, Any]) -> None:
    """Raise ``nbformat_minor`` to at least 5 once a notebook carries cell ids.

    The ``id`` field is an nbformat 4.5 feature. Assigning UUID ids to a
    pre-4.5 notebook (which ``_ensure_native_id`` / ``_upgrade_all_pre_45_ids``
    do on first mutation) would otherwise leave the file internally
    inconsistent — declaring ``nbformat_minor < 5`` yet carrying 4.5-style
    ids — which some stricter nbformat validators reject. ``nbformat`` (major)
    is left untouched."""
    minor = nb.get("nbformat_minor")
    if not isinstance(minor, int) or minor < 5:
        nb["nbformat_minor"] = 5


def _all_cells_have_native_ids(cells: list[Any]) -> bool:
    """True iff every dict cell carries a non-empty native ``id``.

    nbformat 4.5 *requires* an id on every cell, so we only declare a notebook
    to be 4.5 (via :func:`_ensure_nbformat_minor_5`) once that holds — bumping
    the minor version while some cells are still id-less would produce a file
    that is invalid at 4.5 but was valid at 4.4."""
    for cell in cells:
        if not isinstance(cell, dict):
            continue
        raw = cell.get("id")
        if not (isinstance(raw, str) and raw):
            return False
    return True


def edit_cell(
    path: str | Path,
    *,
    cell_id: str,
    new_source: str,
    expected_source: str | None = None,
) -> dict[str, Any]:
    """Atomically replace one cell's source.

    Preserves the cell's ``id`` and ``metadata``. For code cells, clears
    ``outputs`` and ``execution_count`` (because the previous outputs no
    longer correspond to the new source).

    ``expected_source`` is an optional optimistic-concurrency guard: when
    provided, the call fails with ``edit_source_drift`` if the current
    on-disk source differs. Use this when the agent read the cell some
    time ago — it prevents clobbering edits the user made in between. The
    comparison is newline-insensitive (CRLF/LF/CR are normalised) so a
    Windows line-ending mismatch doesn't read as content drift.

    Returns the updated cell summary (same shape as :func:`get_cell`'s
    return, minus ``outputs_summary`` since outputs were cleared).
    """
    if not isinstance(new_source, str):
        raise NotebookError("edit_source_invalid: new_source must be a string")

    p = _resolve_path(path)
    # Capture the on-disk signature BEFORE reading so the lost-update guard can
    # detect a concurrent write that lands between our read and our rename.
    prev_signature = _path_signature(p)
    nb = load_notebook(p)
    cells = nb["cells"]
    index, cell = _resolve_cell(cells, cell_id=cell_id, cell_index=None)
    current_source = _source_to_str(cell.get("source"))

    if expected_source is not None and _normalize_newlines(
        expected_source
    ) != _normalize_newlines(current_source):
        raise NotebookError(
            "edit_source_drift: on-disk source no longer matches expected_source; "
            "re-read the cell before editing"
        )

    # Only upgrade the cell we are about to mutate. ``insert_cell`` and
    # ``delete_cell`` upgrade the whole notebook because they shift array
    # indices; ``edit_cell`` does not change cell positions, so leaving
    # the other pre-4.5 synthetic ids stable preserves any other handles
    # the caller is holding.
    cell_lacked_id = not (isinstance(cell.get("id"), str) and cell.get("id"))
    actual_id = _ensure_native_id(cell)
    if cell_lacked_id and _all_cells_have_native_ids(cells):
        # We just completed the notebook's id coverage; only now is it a valid
        # 4.5 notebook, so it's safe to bump nbformat_minor. (Other cells may
        # still be id-less in a multi-cell pre-4.5 file — edit_cell upgrades
        # only its target — in which case we leave the minor version alone.)
        _ensure_nbformat_minor_5(nb)
    ctype = _cell_type(cell)
    cell["source"] = new_source
    if ctype == "code":
        cell["outputs"] = []
        cell["execution_count"] = None

    _atomic_write_notebook(p, nb, prev_signature=prev_signature)

    return {
        "path": str(p),
        "cell_id": actual_id,
        "id_synthesized": False,  # we just upgraded if it was synth
        "index": index,
        "cell_type": ctype,
        "source": new_source,
        "line_count": new_source.count("\n")
        + (1 if new_source and not new_source.endswith("\n") else 0),
        "char_count": len(new_source),
        "execution_count": None,
        "metadata": cell.get("metadata") or {},
        "previous_source": current_source,
    }


def insert_cell(
    path: str | Path,
    *,
    source: str,
    cell_type: str = "code",
    after_cell_id: str | None = None,
    before_cell_id: str | None = None,
    at_start: bool = False,
    at_end: bool = False,
) -> dict[str, Any]:
    """Insert a new cell with a fresh nbformat 4.5+ UUID.

    Exactly one anchor must be specified:

    - ``after_cell_id`` — insert immediately after the named cell
    - ``before_cell_id`` — insert immediately before the named cell
    - ``at_start=True`` — insert at index 0
    - ``at_end=True`` — append to the end

    Returns the new cell's id, index, and a `get_cell`-shaped summary.
    """
    if not isinstance(source, str):
        raise NotebookError("insert_source_invalid: source must be a string")
    anchors = [
        ("after_cell_id", after_cell_id),
        ("before_cell_id", before_cell_id),
        ("at_start", at_start or None),
        ("at_end", at_end or None),
    ]
    chosen = [(k, v) for k, v in anchors if v]
    if len(chosen) != 1:
        raise NotebookError(
            "insert_anchor_required: pass exactly one of after_cell_id, "
            "before_cell_id, at_start, at_end"
        )
    if cell_type not in _VALID_NEW_CELL_TYPES:
        raise NotebookError(
            f"cell_type_invalid: must be {'|'.join(_VALID_NEW_CELL_TYPES)}, "
            f"got {cell_type!r}"
        )

    p = _resolve_path(path)
    prev_signature = _path_signature(p)
    nb = load_notebook(p)
    cells = nb["cells"]

    # Resolve the anchor BEFORE upgrading other ids — the caller may have
    # passed a synth id that depends on the current array indices.
    if at_start:
        target_index = 0
    elif at_end:
        target_index = len(cells)
    elif after_cell_id:
        anchor_index, anchor_cell = _resolve_cell(
            cells, cell_id=after_cell_id, cell_index=None
        )
        target_index = anchor_index + 1
    else:
        anchor_index, anchor_cell = _resolve_cell(
            cells, cell_id=before_cell_id, cell_index=None
        )
        target_index = anchor_index

    # Insertion shifts every cell at >= target_index by one slot, which
    # would silently invalidate every synth id the caller is holding.
    # Upgrade the whole notebook to nbformat 4.5+ ids in one shot so every
    # cell handle remains stable across this and future mutations.
    _upgrade_all_pre_45_ids(cells)

    new_id = _new_cell_id()
    new_cell = _build_cell(cell_type=cell_type, source=source, cell_id=new_id)
    cells.insert(target_index, new_cell)
    # _upgrade_all_pre_45_ids gave every prior cell an id and the inserted cell
    # carries one too, so the notebook is now fully id'd — i.e. a valid 4.5+
    # file. Keep nbformat_minor consistent (no-op if it was already >= 5).
    _ensure_nbformat_minor_5(nb)
    _atomic_write_notebook(p, nb, prev_signature=prev_signature)

    return {
        "path": str(p),
        "cell_id": new_id,
        "id_synthesized": False,
        "index": target_index,
        "cell_type": cell_type,
        "source": source,
        "line_count": source.count("\n")
        + (1 if source and not source.endswith("\n") else 0),
        "char_count": len(source),
        "execution_count": None,
        "metadata": {},
    }


def delete_cell(
    path: str | Path,
    *,
    cell_id: str,
    expected_source: str | None = None,
) -> dict[str, Any]:
    """Remove a cell by id. Returns the deleted cell's summary so the agent
    can announce or undo.

    ``expected_source`` is an optional concurrency guard, same semantics as
    :func:`edit_cell`.
    """
    p = _resolve_path(path)
    prev_signature = _path_signature(p)
    nb = load_notebook(p)
    cells = nb["cells"]
    index, cell = _resolve_cell(cells, cell_id=cell_id, cell_index=None)
    current_source = _source_to_str(cell.get("source"))
    if expected_source is not None and _normalize_newlines(
        expected_source
    ) != _normalize_newlines(current_source):
        raise NotebookError(
            "delete_source_drift: on-disk source no longer matches expected_source"
        )
    actual_id, synthesized = _cell_id(cell, index, current_source)
    ctype = _cell_type(cell)
    cells.pop(index)
    # The pop shifts every later cell's array index, invalidating any synth
    # ids the caller might still be holding for those cells. Upgrade them
    # all to fresh UUIDs in one shot.
    if _upgrade_all_pre_45_ids(cells):
        _ensure_nbformat_minor_5(nb)
    _atomic_write_notebook(p, nb, prev_signature=prev_signature)

    return {
        "path": str(p),
        "cell_id": actual_id,
        "id_synthesized": synthesized,
        "index": index,
        "cell_type": ctype,
        "deleted_source": current_source,
        "remaining_cell_count": len(cells),
    }
