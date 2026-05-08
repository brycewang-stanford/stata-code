"""Read-only helpers for Jupyter notebooks (.ipynb).

stata-code's MCP execution protocol stays cell-agnostic: `stata_run` accepts a
string and returns a `RunResult`. This module provides side-channel tools so
agents can navigate a notebook without pulling the whole file into context:

- :func:`outline_notebook` — one-line preview per cell (id, type, head)
- :func:`get_cell` — single cell's full source plus a token-economic summary
  of its outputs

Both functions only read from disk. They do not mutate the notebook and never
re-run a cell. Editing/inserting/deleting cells lives in Phase 2.

Cell identity follows nbformat 4.5+: every cell SHOULD have a stable ``id``
field. For older notebooks (pre-4.5) we synthesise a deterministic id from
the cell's array index plus a short hash of its source so agents still get
a stable handle within a single read; ``id_synthesized=True`` flags this so
callers can warn the user.
"""

from __future__ import annotations

import hashlib
import json
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
    """Raised for any problem reading or interpreting a notebook file."""


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
        text = p.read_text(encoding="utf-8")
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
    if t in ("code", "markdown", "raw"):
        return t  # type: ignore[return-value]
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


def _truncate(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[:limit] + "…[truncated]", True


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

    TODO(phase 2): stream `text_chunks` and stop early once the preview budget
    is hit so we don't materialise multi-MB output blobs in memory before
    truncating to 4 KB. The current worst case is bounded by the on-disk
    notebook size, but a streaming version would be cheaper for very chatty
    cells (50× 2 KB stream events).
    """
    types: list[str] = []
    text_chunks: list[str] = []
    has_image = False
    error_summary: dict[str, Any] | None = None

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
        if payload:
            text_chunks.append(payload)

    full_text = "".join(text_chunks)
    text_preview, truncated = _truncate(full_text, _MAX_OUTPUT_TEXT_CHARS)

    return {
        "count": len(outputs),
        "types": types,
        "has_image": has_image,
        "has_error": error_summary is not None,
        "error": error_summary,
        "text_preview": text_preview,
        "text_chars_total": len(full_text),
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

    # cell_id only: scan, supporting both real and synth ids.
    for index, cell in enumerate(cells):
        if not isinstance(cell, dict):
            continue
        source = _source_to_str(cell.get("source"))
        actual_id, _ = _cell_id(cell, index, source)
        if actual_id == cell_id:
            return index, cell
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
