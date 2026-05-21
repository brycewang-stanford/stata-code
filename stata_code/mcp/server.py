"""MCP server exposing the stata_code v1.0 pipeline.

Server features:
- Tools: execute Stata, inspect runtime/session state, and fetch deferred
  logs/graphs/matrices.
- Structured tool results: `structuredContent` plus JSON text compatibility.
- Resources: expose RunResult schema, server capabilities, live sessions, and
  dynamic `log://`, `graph://`, and `matrix://` refs.
- Prompts: provide user-controlled workflows for validation, debugging,
  repair loops, replication audits, and estimation summaries.

The result envelope, token-economy defaults (log head+tail+ref, graph refs
not inline), session model, and error taxonomy follow SCHEMA.md.
"""

from __future__ import annotations

import asyncio
import base64
import json
import sys
from typing import Any, cast

try:
    from mcp.server import Server
    from mcp.server.lowlevel.helper_types import ReadResourceContents
    from mcp.server.stdio import stdio_server
    from mcp.types import (
        CallToolResult,
        GetPromptResult,
        ImageContent,
        Prompt,
        PromptArgument,
        PromptMessage,
        Resource,
        ResourceTemplate,
        TextContent,
        Tool,
        ToolAnnotations,
    )

    _MCP_AVAILABLE = True
except ImportError:  # pragma: no cover - environment without mcp installed
    Server = None  # type: ignore[assignment,misc]
    Tool = None  # type: ignore[assignment,misc]
    TextContent = None  # type: ignore[assignment,misc]
    ImageContent = None  # type: ignore[assignment,misc]
    CallToolResult = None  # type: ignore[assignment,misc]
    ToolAnnotations = None  # type: ignore[assignment,misc]
    GetPromptResult = None  # type: ignore[assignment,misc]
    Prompt = None  # type: ignore[assignment,misc]
    PromptArgument = None  # type: ignore[assignment,misc]
    PromptMessage = None  # type: ignore[assignment,misc]
    Resource = None  # type: ignore[assignment,misc]
    ResourceTemplate = None  # type: ignore[assignment,misc]
    ReadResourceContents = None  # type: ignore[assignment,misc]
    stdio_server = None  # type: ignore[assignment]
    _MCP_AVAILABLE = False

from stata_code.core import _refs
from stata_code.core._pool import get_default_pool, pool_execute, pool_stata_info
from stata_code.core._runtime import PystataNotAvailable
from stata_code.core.notebook import (
    NotebookError,
)
from stata_code.core.notebook import (
    delete_cell as _notebook_delete_cell,
)
from stata_code.core.notebook import (
    edit_cell as _notebook_edit_cell,
)
from stata_code.core.notebook import (
    get_cell as _notebook_get_cell,
)
from stata_code.core.notebook import (
    insert_cell as _notebook_insert_cell,
)
from stata_code.core.notebook import (
    locate_cells as _notebook_locate_cells,
)
from stata_code.core.notebook import (
    outline_notebook as _notebook_outline,
)
from stata_code.core.run_index import (
    RunIndexError,
)
from stata_code.core.run_index import (
    list_runs as _list_runs,
)
from stata_code.core.runner import (
    RefNotFound,
    get_graph,
    get_log,
    get_matrix,
)
from stata_code.core.schema import RunResult

__version__ = "0.6.4"

SERVER_INSTRUCTIONS = (
    "Use stata-code for running and inspecting Stata code. Prefer structuredContent "
    "over parsing logs. Run code first for validation-only requests; edit source "
    "files only when the user explicitly asks for repair or iteration. Large logs, "
    "graphs, and matrices are returned by reference and can be fetched on demand."
)

APP: Any = (
    Server("stata-code", version=__version__, instructions=SERVER_INSTRUCTIONS)
    if _MCP_AVAILABLE
    else None
)


def _object_schema(
    properties: dict[str, Any],
    required: list[str] | None = None,
    *,
    additional_properties: bool = True,
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": additional_properties,
    }


_INFO_OUTPUT_SCHEMA = _object_schema(
    {
        "available": {"type": "boolean"},
        "schema_version": {"type": "string"},
        "capabilities": {"type": "array", "items": {"type": "string"}},
        "stata": {"type": "object"},
        "edition": {"type": ["string", "null"]},
        "version": {"type": ["string", "null"]},
        "backend": {"type": "string"},
        # Present only when an operational error (worker timeout / crash /
        # broken pipe) prevented a successful query — distinguished from
        # "Stata is not installed", which omits this field.
        "error": {"type": "string"},
    },
    ["available", "schema_version", "capabilities"],
)

_LOG_OUTPUT_SCHEMA = _object_schema(
    {
        "text": {"type": "string"},
        "lines_total": {"type": "integer"},
        "bytes_total": {"type": "integer"},
    },
    ["text", "lines_total", "bytes_total"],
)

_GRAPH_OUTPUT_SCHEMA = _object_schema(
    {
        "ref": {"type": "string"},
        "format": {"type": "string"},
        "mimeType": {"type": "string"},
        "width": {"type": ["integer", "null"]},
        "height": {"type": ["integer", "null"]},
    },
    ["ref", "format", "mimeType"],
)

_MATRIX_OUTPUT_SCHEMA = _object_schema(
    {
        "rows": {"type": "array", "items": {"type": "string"}},
        "cols": {"type": "array", "items": {"type": "string"}},
        "values": {
            "type": "array",
            "items": {
                "type": "array",
                "items": {"type": ["number", "null"]},
            },
        },
    },
    ["rows", "cols", "values"],
)

_SESSIONS_OUTPUT_SCHEMA = _object_schema(
    {
        "sessions": {
            "type": "array",
            "items": _object_schema(
                {
                    "session_id": {"type": "string"},
                    "frame": {"type": "string"},
                    "n_obs": {"type": "integer"},
                },
                ["session_id", "frame", "n_obs"],
            ),
        },
        # Optional: emitted only when one or more workers failed to respond
        # to the list-sessions probe. Each entry has the worker's session
        # key and a short reason string ("timeout" or "worker_error: …").
        "warnings": {
            "type": "array",
            "items": _object_schema(
                {
                    "session_id": {"type": "string"},
                    "reason": {"type": "string"},
                },
                ["session_id", "reason"],
            ),
        },
    },
    ["sessions"],
)

_CANCEL_SESSION_OUTPUT_SCHEMA = _object_schema(
    {
        "session_id": {"type": "string"},
        "was_pending": {"type": "boolean"},
        "is_pending": {"type": "boolean"},
        "killed_worker": {"type": "boolean"},
    },
    ["session_id", "was_pending", "is_pending", "killed_worker"],
)

_RESET_SESSION_OUTPUT_SCHEMA = _object_schema(
    {
        "session_id": {"type": "string"},
        "dropped_frame": {"type": "boolean"},
    },
    ["session_id", "dropped_frame"],
)

_NOTEBOOK_CELL_OUTLINE_ITEM_SCHEMA = _object_schema(
    {
        "cell_id": {"type": "string"},
        "id_synthesized": {"type": "boolean"},
        "index": {"type": "integer"},
        "cell_type": {"type": "string"},
        "source_preview": {"type": "string"},
        "line_count": {"type": "integer"},
        "char_count": {"type": "integer"},
        "execution_count": {"type": ["integer", "null"]},
        "has_outputs": {"type": "boolean"},
        "has_error_output": {"type": "boolean"},
    },
    [
        "cell_id",
        "id_synthesized",
        "index",
        "cell_type",
        "source_preview",
        "line_count",
        "char_count",
        "has_outputs",
        "has_error_output",
    ],
)

_NOTEBOOK_OUTLINE_OUTPUT_SCHEMA = _object_schema(
    {
        "path": {"type": "string"},
        "nbformat": {"type": ["integer", "null"]},
        "kernelspec": {"type": ["object", "null"]},
        "cell_count": {"type": "integer"},
        "cells": {
            "type": "array",
            "items": _NOTEBOOK_CELL_OUTLINE_ITEM_SCHEMA,
        },
    },
    ["path", "cell_count", "cells"],
)

_NOTEBOOK_GET_CELL_OUTPUT_SCHEMA = _object_schema(
    {
        "path": {"type": "string"},
        "cell_id": {"type": "string"},
        "id_synthesized": {"type": "boolean"},
        "index": {"type": "integer"},
        "cell_type": {"type": "string"},
        "source": {"type": "string"},
        "line_count": {"type": "integer"},
        "char_count": {"type": "integer"},
        "execution_count": {"type": ["integer", "null"]},
        "metadata": {"type": "object"},
        "outputs_summary": {"type": ["object", "null"]},
    },
    [
        "path",
        "cell_id",
        "id_synthesized",
        "index",
        "cell_type",
        "source",
        "line_count",
        "char_count",
    ],
)

_NOTEBOOK_LOCATE_MATCH_SCHEMA = _object_schema(
    {
        "cell_id": {"type": "string"},
        "id_synthesized": {"type": "boolean"},
        "index": {"type": "integer"},
        "cell_type": {"type": "string"},
        "score": {"type": "number"},
        "line_in_cell": {"type": ["integer", "null"]},
        "preview": {"type": "string"},
    },
    [
        "cell_id",
        "id_synthesized",
        "index",
        "cell_type",
        "score",
        "preview",
    ],
)

_NOTEBOOK_LOCATE_OUTPUT_SCHEMA = _object_schema(
    {
        "path": {"type": "string"},
        "query": {"type": "object"},
        "match_count": {"type": "integer"},
        "matches": {
            "type": "array",
            "items": _NOTEBOOK_LOCATE_MATCH_SCHEMA,
        },
    },
    ["path", "query", "match_count", "matches"],
)

_NOTEBOOK_EDIT_OUTPUT_SCHEMA = _object_schema(
    {
        "path": {"type": "string"},
        "cell_id": {"type": "string"},
        "id_synthesized": {"type": "boolean"},
        "index": {"type": "integer"},
        "cell_type": {"type": "string"},
        "source": {"type": "string"},
        "line_count": {"type": "integer"},
        "char_count": {"type": "integer"},
        "execution_count": {"type": ["integer", "null"]},
        "metadata": {"type": "object"},
        "previous_source": {"type": "string"},
    },
    [
        "path",
        "cell_id",
        "index",
        "cell_type",
        "source",
        "previous_source",
    ],
)

_NOTEBOOK_INSERT_OUTPUT_SCHEMA = _object_schema(
    {
        "path": {"type": "string"},
        "cell_id": {"type": "string"},
        "id_synthesized": {"type": "boolean"},
        "index": {"type": "integer"},
        "cell_type": {"type": "string"},
        "source": {"type": "string"},
        "line_count": {"type": "integer"},
        "char_count": {"type": "integer"},
        "execution_count": {"type": ["integer", "null"]},
        "metadata": {"type": "object"},
    },
    [
        "path",
        "cell_id",
        "index",
        "cell_type",
        "source",
    ],
)

_NOTEBOOK_DELETE_OUTPUT_SCHEMA = _object_schema(
    {
        "path": {"type": "string"},
        "cell_id": {"type": "string"},
        "id_synthesized": {"type": "boolean"},
        "index": {"type": "integer"},
        "cell_type": {"type": "string"},
        "deleted_source": {"type": "string"},
        "remaining_cell_count": {"type": "integer"},
    },
    [
        "path",
        "cell_id",
        "index",
        "cell_type",
        "deleted_source",
        "remaining_cell_count",
    ],
)

# All manifest-derived fields are nullable because we tolerate older or
# partially-populated manifests (run_index._read_manifest only enforces a
# small core of required fields). `directory` and `manifest_path` are the
# only non-nullable strings: they are derived from the on-disk path that
# was just scanned, never from manifest content, so they cannot be missing.
_LIST_RUNS_ENTRY_SCHEMA = _object_schema(
    {
        "request_id": {"type": ["string", "null"]},
        "session_id": {"type": ["string", "null"]},
        "started_at": {"type": ["string", "null"]},
        "elapsed_ms": {"type": ["integer", "null"]},
        "ok": {"type": ["boolean", "null"]},
        "rc": {"type": ["integer", "null"]},
        "source_path": {"type": ["string", "null"]},
        "origin_kind": {"type": ["string", "null"]},
        "origin_label": {"type": ["string", "null"]},
        "origin_cell_id": {"type": ["string", "null"]},
        "directory": {"type": "string"},
        "manifest_path": {"type": "string"},
        "log_path": {"type": ["string", "null"]},
        "code_path": {"type": ["string", "null"]},
    },
    ["directory", "manifest_path"],
)

_LIST_RUNS_OUTPUT_SCHEMA = _object_schema(
    {
        "log_dir": {"type": "string"},
        "scanned_count": {"type": "integer"},
        "match_count": {"type": "integer"},
        "skipped_count": {"type": "integer"},
        "limit": {"type": "integer"},
        # Echoed only when the original ``limit`` exceeded the server-side
        # max (``_LIMIT_MAX``) and was clamped — lets callers distinguish a
        # genuine "more rows than asked for" truncation from a silent clamp.
        "requested_limit": {"type": "integer"},
        "truncated": {"type": "boolean"},
        "runs": {"type": "array", "items": _LIST_RUNS_ENTRY_SCHEMA},
    },
    [
        "log_dir",
        "scanned_count",
        "match_count",
        "skipped_count",
        "limit",
        "truncated",
        "runs",
    ],
)


# ─────────────────────────────────────────────────────────────────────────────
# Tool registry
# ─────────────────────────────────────────────────────────────────────────────


def _tool_definitions() -> list[Tool]:
    return [
        Tool(
            name="stata_run",
            title="Run Stata Code",
            description=(
                "Execute Stata code and return a v1.0 stata_code RunResult "
                "(see SCHEMA.md). The result is a JSON object with ok, rc, "
                "error (typed), log (head+tail+ref by default), results.r/e "
                "(scalars/macros/matrices, native types), dataset metadata, "
                "graphs, warnings, and capabilities. Use the structured "
                "fields rather than parsing the log. On ok=false, surface "
                "error.kind/message/line/suggestions. Use suggestions for "
                "iterative debug/fix loops only when the user asked for that "
                "or approved changes; for run/validate-only requests, report "
                "diagnostics without rewriting source code."
            ),
            inputSchema={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "Stata code to execute. Multi-line OK.",
                    },
                    "session_id": {
                        "type": "string",
                        "default": "main",
                        "description": (
                            "Session name. 'main' is the master frame; "
                            "other names create/route to that Stata frame "
                            "(data isolation only; r()/e() remain global)."
                        ),
                    },
                    "include_graphs": {
                        "type": "string",
                        "enum": ["ref", "inline", "none"],
                        "default": "ref",
                    },
                    "graph_format": {
                        "type": "string",
                        "enum": ["png", "svg", "pdf"],
                        "default": "png",
                    },
                    "log_lines_head": {"type": "integer", "default": 20},
                    "log_lines_tail": {"type": "integer", "default": 20},
                    "include_full_log": {"type": "boolean", "default": False},
                    "include_dataset_variables": {
                        "type": "boolean",
                        "default": True,
                    },
                    "persist_log_files": {
                        "type": "boolean",
                        "default": False,
                        "description": (
                            "When true and origin_path is provided, write "
                            "immutable .log/.smcl artifacts under "
                            "<origin dir>/log-files/<run>/."
                        ),
                    },
                    "persist_generated_files": {
                        "type": "boolean",
                        "default": True,
                        "description": (
                            "When log files are persisted, also copy newly "
                            "created or modified output files from the run "
                            "working directory into the run bundle's outputs/ "
                            "folder, and save captured graphs into graphs/."
                        ),
                    },
                    "origin_path": {
                        "type": "string",
                        "description": (
                            "Absolute path of the source .do file. Used for "
                            "the default Stata working directory, run-bundle "
                            "placement, and metadata."
                        ),
                    },
                    "use_origin_workdir": {
                        "type": "boolean",
                        "default": True,
                        "description": (
                            "When origin_path is provided, cd Stata to the "
                            "source .do file's directory before running. This "
                            "makes relative table/graph exports land next to "
                            "the .do file by default."
                        ),
                    },
                    "working_dir": {
                        "type": "string",
                        "description": (
                            "Optional explicit Stata working directory. "
                            "Overrides origin_path's directory."
                        ),
                    },
                    "origin_kind": {
                        "type": "string",
                        "enum": [
                            "file",
                            "selection",
                            "line",
                            "cell",
                            "section",
                            "code",
                            "unknown",
                        ],
                        "description": "Which editor surface produced the submitted code.",
                    },
                    "origin_label": {
                        "type": "string",
                        "description": (
                            "Human-readable source label, for example "
                            "demo/test1.do:1."
                        ),
                    },
                    "origin_cell_id": {
                        "type": "string",
                        "description": (
                            "Stable nbformat 4.5+ cell id when the submitted "
                            "code is one cell of a Jupyter notebook. The "
                            "runner does not interpret this value — it is "
                            "echoed in result.origin and recorded in the "
                            "run-bundle manifest so notebook-aware agents "
                            "can correlate runs with cells."
                        ),
                    },
                },
                "required": ["code"],
            },
            outputSchema=RunResult.model_json_schema(),
            annotations=ToolAnnotations(
                title="Run Stata Code",
                readOnlyHint=False,
                destructiveHint=True,
                idempotentHint=False,
                openWorldHint=True,
            ),
        ),
        Tool(
            name="stata_info",
            title="Inspect Stata Runtime",
            description=(
                "Report installed Stata edition, version, backend, and "
                "whether the runtime is initialized."
            ),
            inputSchema={
                "type": "object",
                "additionalProperties": False,
                "properties": {},
            },
            outputSchema=_INFO_OUTPUT_SCHEMA,
            annotations=ToolAnnotations(
                title="Inspect Stata Runtime",
                readOnlyHint=True,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=False,
            ),
        ),
        Tool(
            name="get_log",
            title="Fetch Stata Log",
            description=(
                "Fetch the full log text behind a log:// ref returned by a "
                "prior stata_run call. Returns JSON {text, lines_total, "
                "bytes_total}."
            ),
            inputSchema={
                "type": "object",
                "additionalProperties": False,
                "properties": {"ref": {"type": "string"}},
                "required": ["ref"],
            },
            outputSchema=_LOG_OUTPUT_SCHEMA,
            annotations=ToolAnnotations(
                title="Fetch Stata Log",
                readOnlyHint=True,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=False,
            ),
        ),
        Tool(
            name="get_graph",
            title="Fetch Stata Graph",
            description=(
                "Fetch graph bytes behind a graph:// ref. Returns an "
                "ImageContent (base64 bytes + mimeType) suitable for direct "
                "display by vision-capable clients."
            ),
            inputSchema={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "ref": {"type": "string"},
                    "format": {
                        "type": "string",
                        "enum": ["png", "svg", "pdf"],
                    },
                },
                "required": ["ref"],
            },
            outputSchema=_GRAPH_OUTPUT_SCHEMA,
            annotations=ToolAnnotations(
                title="Fetch Stata Graph",
                readOnlyHint=True,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=False,
            ),
        ),
        Tool(
            name="get_matrix",
            title="Fetch Stata Matrix",
            description=(
                "Fetch a matrix's values, rows, and cols behind a matrix:// "
                "ref. Producers emit a ref instead of inlining values when "
                "the matrix exceeds ~10,000 cells. Returns JSON {rows, cols, "
                "values}."
            ),
            inputSchema={
                "type": "object",
                "additionalProperties": False,
                "properties": {"ref": {"type": "string"}},
                "required": ["ref"],
            },
            outputSchema=_MATRIX_OUTPUT_SCHEMA,
            annotations=ToolAnnotations(
                title="Fetch Stata Matrix",
                readOnlyHint=True,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=False,
            ),
        ),
        Tool(
            name="list_sessions",
            title="List Stata Sessions",
            description=(
                "Enumerate live sessions. Each entry has session_id, frame "
                "(Stata frame name), and n_obs."
            ),
            inputSchema={
                "type": "object",
                "additionalProperties": False,
                "properties": {},
            },
            outputSchema=_SESSIONS_OUTPUT_SCHEMA,
            annotations=ToolAnnotations(
                title="List Stata Sessions",
                readOnlyHint=True,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=False,
            ),
        ),
        Tool(
            name="cancel_session",
            title="Cancel Stata Session",
            description=(
                "Request cancellation for this subprocess-backed session. "
                "If a run is in flight, the worker process is terminated; "
                "otherwise the flag is consumed by the next call and returns "
                "a RunResult with ok=false, rc=-3, error.kind='cancelled'. "
                "Returns JSON {session_id, was_pending, is_pending, "
                "killed_worker}."
            ),
            inputSchema={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "session_id": {"type": "string", "default": "main"},
                },
            },
            outputSchema=_CANCEL_SESSION_OUTPUT_SCHEMA,
            annotations=ToolAnnotations(
                title="Cancel Stata Session",
                readOnlyHint=False,
                destructiveHint=True,
                idempotentHint=False,
                openWorldHint=False,
            ),
        ),
        Tool(
            name="reset_session",
            title="Reset Stata Session",
            description=(
                "Drop a session's data. session_id='main' performs `clear "
                "all` in place (default frame cannot be dropped); other "
                "names drop the corresponding Stata frame."
            ),
            inputSchema={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "session_id": {"type": "string", "default": "main"},
                },
            },
            outputSchema=_RESET_SESSION_OUTPUT_SCHEMA,
            annotations=ToolAnnotations(
                title="Reset Stata Session",
                readOnlyHint=False,
                destructiveHint=True,
                idempotentHint=False,
                openWorldHint=False,
            ),
        ),
        Tool(
            name="notebook_outline",
            title="Outline Jupyter Notebook",
            description=(
                "Read a .ipynb file and return a compact per-cell index: "
                "cell_id (nbformat 4.5+ UUID, or a synthesized fallback for "
                "older notebooks), index, cell_type, source preview, line/"
                "char counts, execution_count, and whether outputs/error "
                "outputs are present. Read-only; does not execute or modify "
                "the notebook. Use this before notebook_get_cell to avoid "
                "pulling the full notebook into context."
            ),
            inputSchema={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute or workspace path to the .ipynb file.",
                    },
                    "preview_lines": {
                        "type": "integer",
                        "default": 2,
                        "minimum": 0,
                        "description": (
                            "Number of leading source lines to include per "
                            "cell as a preview. Long lines are truncated."
                        ),
                    },
                },
                "required": ["path"],
            },
            outputSchema=_NOTEBOOK_OUTLINE_OUTPUT_SCHEMA,
            annotations=ToolAnnotations(
                title="Outline Jupyter Notebook",
                readOnlyHint=True,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=False,
            ),
        ),
        Tool(
            name="notebook_get_cell",
            title="Read Notebook Cell",
            description=(
                "Read one cell of a .ipynb by cell_id (preferred) or "
                "cell_index. Returns the cell's full source plus a token-"
                "economic outputs summary (count, types, whether an image "
                "is present, head/tail of stream/text outputs, error ename/"
                "evalue with truncated traceback). Read-only; does not "
                "execute or modify the notebook."
            ),
            inputSchema={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute or workspace path to the .ipynb file.",
                    },
                    "cell_id": {
                        "type": "string",
                        "description": (
                            "Stable nbformat 4.5+ cell id, or a synthesized "
                            "id from a prior notebook_outline call."
                        ),
                    },
                    "cell_index": {
                        "type": "integer",
                        "minimum": 0,
                        "description": (
                            "0-based array index. Use only when the notebook "
                            "lacks cell ids; cell_id is more stable."
                        ),
                    },
                },
                "required": ["path"],
            },
            outputSchema=_NOTEBOOK_GET_CELL_OUTPUT_SCHEMA,
            annotations=ToolAnnotations(
                title="Read Notebook Cell",
                readOnlyHint=True,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=False,
            ),
        ),
        Tool(
            name="notebook_locate",
            title="Locate Notebook Cells",
            description=(
                "Find cells in a .ipynb by content. Pass exactly one of "
                "snippet (literal substring with whitespace-tolerant fall-"
                "back), regex (Python regex, multiline), or error_text "
                "(pasted Stata/traceback text — the longest code-like line "
                "is used as a fingerprint). Returns up to `limit` candidates "
                "ranked by match score, each with cell_id, line_in_cell, "
                "and a short preview. Read-only."
            ),
            inputSchema={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute or workspace path to the .ipynb file.",
                    },
                    "snippet": {
                        "type": "string",
                        "description": (
                            "Literal substring to match. Whitespace is "
                            "normalised line-by-line as a fallback if the "
                            "exact substring is not found."
                        ),
                    },
                    "regex": {
                        "type": "string",
                        "description": "Python regex applied to the cell source (multiline mode).",
                    },
                    "error_text": {
                        "type": "string",
                        "description": (
                            "Pasted error/traceback text. The longest code-"
                            "like line is treated as a fingerprint and "
                            "located in the notebook."
                        ),
                    },
                    "cell_type": {
                        "type": "string",
                        "enum": ["code", "markdown", "raw"],
                        "description": "Optional filter by cell type.",
                    },
                    "limit": {
                        "type": "integer",
                        "default": 10,
                        "minimum": 1,
                        "maximum": 100,
                        "description": "Maximum number of candidates to return.",
                    },
                },
                "required": ["path"],
                # Schema-level expression of the "exactly one of snippet /
                # regex / error_text" rule. The runtime guard in
                # notebook.locate_cells still enforces this for clients that
                # don't validate inputs against the schema.
                "oneOf": [
                    {"required": ["snippet"]},
                    {"required": ["regex"]},
                    {"required": ["error_text"]},
                ],
            },
            outputSchema=_NOTEBOOK_LOCATE_OUTPUT_SCHEMA,
            annotations=ToolAnnotations(
                title="Locate Notebook Cells",
                readOnlyHint=True,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=False,
            ),
        ),
        Tool(
            name="notebook_edit_cell",
            title="Edit Notebook Cell",
            description=(
                "Atomically replace one cell's source. Preserves cell.id and "
                "metadata. For code cells, clears outputs and "
                "execution_count. Optionally pass expected_source for "
                "optimistic-concurrency: the call fails with "
                "'edit_source_drift' if the on-disk source no longer "
                "matches. Writes the whole notebook via temp file + rename. "
                "Note: if the cell was addressed by a synthesised id (pre-"
                "nbformat-4.5 notebook), the cell is upgraded to a real UUID "
                "during this call. The old synth id is no longer valid — "
                "use the cell_id returned in this result for follow-up calls."
            ),
            inputSchema={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "path": {"type": "string"},
                    "cell_id": {
                        "type": "string",
                        "description": (
                            "Cell to edit. If addressing a pre-4.5 cell by "
                            "synthesised id, the cell is upgraded with a "
                            "fresh nbformat 4.5+ UUID before saving."
                        ),
                    },
                    "new_source": {
                        "type": "string",
                        "description": "Replacement source text.",
                    },
                    "expected_source": {
                        "type": "string",
                        "description": (
                            "Optional concurrency guard. When provided, the "
                            "current on-disk source must match exactly."
                        ),
                    },
                },
                "required": ["path", "cell_id", "new_source"],
            },
            outputSchema=_NOTEBOOK_EDIT_OUTPUT_SCHEMA,
            annotations=ToolAnnotations(
                title="Edit Notebook Cell",
                readOnlyHint=False,
                destructiveHint=True,
                idempotentHint=False,
                openWorldHint=False,
            ),
        ),
        Tool(
            name="notebook_insert_cell",
            title="Insert Notebook Cell",
            description=(
                "Insert a new cell with a fresh nbformat 4.5+ UUID. Pass "
                "exactly one anchor: after_cell_id, before_cell_id, "
                "at_start, or at_end. Default cell_type is 'code'."
            ),
            inputSchema={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "path": {"type": "string"},
                    "source": {"type": "string"},
                    "cell_type": {
                        "type": "string",
                        "enum": ["code", "markdown", "raw"],
                        "default": "code",
                    },
                    "after_cell_id": {"type": "string"},
                    "before_cell_id": {"type": "string"},
                    "at_start": {"type": "boolean"},
                    "at_end": {"type": "boolean"},
                },
                "required": ["path", "source"],
                # Exactly one anchor must be present. Booleans for
                # at_start/at_end must additionally be `true` to count as
                # "specified" — the JSON Schema below treats them as
                # required-presence; the runtime guard in
                # notebook.insert_cell additionally rejects the false-y form.
                "oneOf": [
                    {"required": ["after_cell_id"]},
                    {"required": ["before_cell_id"]},
                    {"required": ["at_start"]},
                    {"required": ["at_end"]},
                ],
            },
            outputSchema=_NOTEBOOK_INSERT_OUTPUT_SCHEMA,
            annotations=ToolAnnotations(
                title="Insert Notebook Cell",
                readOnlyHint=False,
                destructiveHint=True,
                idempotentHint=False,
                openWorldHint=False,
            ),
        ),
        Tool(
            name="notebook_delete_cell",
            title="Delete Notebook Cell",
            description=(
                "Remove a cell by id. Returns the deleted cell's source so "
                "the caller can announce or undo. Optionally pass "
                "expected_source for optimistic-concurrency."
            ),
            inputSchema={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "path": {"type": "string"},
                    "cell_id": {"type": "string"},
                    "expected_source": {"type": "string"},
                },
                "required": ["path", "cell_id"],
            },
            outputSchema=_NOTEBOOK_DELETE_OUTPUT_SCHEMA,
            annotations=ToolAnnotations(
                title="Delete Notebook Cell",
                readOnlyHint=False,
                destructiveHint=True,
                idempotentHint=False,
                openWorldHint=False,
            ),
        ),
        Tool(
            name="list_runs",
            title="List Run Bundles",
            description=(
                "Query the on-disk run-bundle manifests under a log-files "
                "directory. Pass either log_dir directly or origin_path "
                "(then the dir is inferred as <origin_path parent>/log-"
                "files). Filters compose with AND: cell_id, session_id, "
                "ok, since (ISO 8601 UTC, lexicographic compare on "
                "started_at). Read-only; never re-runs anything. Returns "
                "compact summaries newest-first; callers fetch the full "
                "manifest from the returned manifest_path if needed."
            ),
            inputSchema={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "log_dir": {
                        "type": "string",
                        "description": "Explicit log-files directory to scan.",
                    },
                    "origin_path": {
                        "type": "string",
                        "description": (
                            "Source path (.do or .ipynb). Used both to "
                            "locate <dirname>/log-files (when log_dir is "
                            "absent) and as a filter on source_path."
                        ),
                    },
                    "cell_id": {
                        "type": "string",
                        "description": (
                            "Filter by origin_cell_id recorded on the run."
                        ),
                    },
                    "session_id": {"type": "string"},
                    "ok": {"type": "boolean"},
                    "since": {
                        "type": "string",
                        "description": (
                            "ISO 8601 UTC string, e.g. "
                            "'2026-05-08T01:00:00.000Z'. Lexicographic "
                            "compare against started_at; the boundary is "
                            "inclusive (runs at exactly `since` are returned)."
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "default": 50,
                        "minimum": 1,
                        "maximum": 500,
                    },
                },
            },
            outputSchema=_LIST_RUNS_OUTPUT_SCHEMA,
            annotations=ToolAnnotations(
                title="List Run Bundles",
                readOnlyHint=True,
                destructiveHint=False,
                idempotentHint=True,
                # `list_runs` reads from caller-specified filesystem paths,
                # which is "open world" the same way stata_run is — the
                # surface is constrained (read-only, manifest.json only) but
                # the input is not bounded by the server's own state.
                openWorldHint=True,
            ),
        ),
    ]


def _resource_templates() -> list[ResourceTemplate]:
    return [
        ResourceTemplate(
            name="stata_log_ref",
            title="Stata Log Reference",
            uriTemplate="log://{request_id}",
            description="Full Stata log text captured from a truncated stata_run result.",
            mimeType="text/plain",
        ),
        ResourceTemplate(
            name="stata_graph_ref",
            title="Stata Graph Reference",
            uriTemplate="graph://{request_id}/{index}",
            description="Graph image bytes captured from a stata_run result.",
            mimeType="image/png",
        ),
        ResourceTemplate(
            name="stata_matrix_ref",
            title="Stata Matrix Reference",
            uriTemplate="matrix://{request_id}/{scope}/{name}",
            description="Large Stata r()/e() matrix payload captured by reference.",
            mimeType="application/json",
        ),
    ]


def _static_resources() -> list[Resource]:
    return [
        Resource(
            uri=cast(Any, "stata://schema/run-result"),
            name="run-result-schema",
            title="stata-code RunResult JSON Schema",
            description="JSON Schema for structuredContent returned by stata_run.",
            mimeType="application/schema+json",
        ),
        Resource(
            uri=cast(Any, "stata://server/capabilities"),
            name="server-capabilities",
            title="stata-code Server Capabilities",
            description="Tools, schema version, server instructions, and feature hints.",
            mimeType="application/json",
        ),
        Resource(
            uri=cast(Any, "stata://sessions"),
            name="stata-sessions",
            title="Live Stata Sessions",
            description="Current session/frame inventory from the subprocess pool.",
            mimeType="application/json",
        ),
    ]


def _resource_for_ref(ref: str, payload: Any) -> Resource | None:
    if ref.startswith("log://"):
        return Resource(
            uri=cast(Any, ref),
            name=ref,
            title=f"Stata log {ref.removeprefix('log://')}",
            description="Full Stata log text captured by reference.",
            mimeType="text/plain",
        )
    if ref.startswith("graph://"):
        fmt = payload.get("format", "png") if isinstance(payload, dict) else "png"
        mime = _GRAPH_MIME.get(fmt, "image/png")
        return Resource(
            uri=cast(Any, ref),
            name=ref,
            title=f"Stata graph {ref.removeprefix('graph://')}",
            description="Graph image captured by reference.",
            mimeType=mime,
        )
    if ref.startswith("matrix://"):
        return Resource(
            uri=cast(Any, ref),
            name=ref,
            title=f"Stata matrix {ref.removeprefix('matrix://')}",
            description="Large Stata matrix captured by reference.",
            mimeType="application/json",
        )
    return None


_LIST_RESOURCES_REFS_CAP = 256


def _list_mcp_resources() -> list[Resource]:
    """Enumerate static resources plus the most recent ref-backed payloads.

    The ref store is unbounded by time and bounded by capacity (it evicts
    LRU). Returning every entry from a long-lived server would balloon a
    `list_resources` reply with stale logs from sessions the agent has
    already abandoned. ``_LIST_RESOURCES_REFS_CAP`` keeps the wire
    payload compact and biased toward the most recently used refs.
    """
    resources = _static_resources()
    # `_refs.snapshot()` returns entries in LRU order (oldest first); take
    # the tail so the most-recently-touched refs survive when we cap.
    snapshot = _refs.snapshot()
    if len(snapshot) > _LIST_RESOURCES_REFS_CAP:
        snapshot = snapshot[-_LIST_RESOURCES_REFS_CAP:]
    for ref, payload in snapshot:
        resource = _resource_for_ref(ref, payload)
        if resource is not None:
            resources.append(resource)
    return resources


def _read_resource_payload(uri: str) -> ReadResourceContents:
    if uri == "stata://schema/run-result":
        return ReadResourceContents(
            content=json.dumps(RunResult.model_json_schema(), indent=2),
            mime_type="application/schema+json",
        )
    if uri == "stata://server/capabilities":
        payload = {
            "name": "stata-code",
            "version": __version__,
            "schema_version": "1.0",
            "instructions": SERVER_INSTRUCTIONS,
            "tools": [
                tool.model_dump(mode="json", by_alias=True)
                for tool in _tool_definitions()
            ],
            "resource_templates": [
                tmpl.model_dump(mode="json", by_alias=True)
                for tmpl in _resource_templates()
            ],
            # Prompts live behind a separate ``list_prompts`` round-trip,
            # but clients reading the capabilities resource expect a
            # single discoverability snapshot. Embed the prompt manifest
            # here so they don't need a second request to enumerate the
            # full surface area.
            "prompts": [
                prompt.model_dump(mode="json", by_alias=True)
                for prompt in _prompt_definitions()
            ],
        }
        return ReadResourceContents(
            content=json.dumps(payload),
            mime_type="application/json",
        )
    if uri == "stata://sessions":
        return ReadResourceContents(
            content=json.dumps(_list_sessions_payload(get_default_pool())),
            mime_type="application/json",
        )
    if uri.startswith("log://"):
        payload = cast(dict[str, Any], get_log(uri))
        return ReadResourceContents(
            content=cast(str, payload["text"]),
            mime_type="text/plain",
            meta={
                "lines_total": payload["lines_total"],
                "bytes_total": payload["bytes_total"],
            },
        )
    if uri.startswith("graph://"):
        payload = cast(dict[str, Any], get_graph(uri))
        graph_format = cast(str, payload["format"])
        mime = _GRAPH_MIME.get(graph_format, "image/png")
        return ReadResourceContents(
            content=base64.b64decode(cast(str, payload["bytes_b64"])),
            mime_type=mime,
            meta={
                "format": graph_format,
                "width": payload["width"],
                "height": payload["height"],
            },
        )
    if uri.startswith("matrix://"):
        return ReadResourceContents(
            content=json.dumps(cast(dict[str, Any], get_matrix(uri))),
            mime_type="application/json",
        )
    raise ValueError(f"Unknown resource URI: {uri}")


def _prompt_definitions() -> list[Prompt]:
    return [
        Prompt(
            name="run_do_file_and_report",
            title="Run Do-file and Report",
            description=(
                "Run a Stata do-file through stata_run and report success, "
                "typed errors, generated artifacts, and next steps without "
                "editing source files."
            ),
            arguments=[
                PromptArgument(
                    name="path",
                    description="Absolute or workspace-relative path to the .do file.",
                    required=True,
                ),
                PromptArgument(
                    name="session_id",
                    description="Optional Stata session id; defaults to main.",
                    required=False,
                ),
            ],
        ),
        Prompt(
            name="debug_stata_error",
            title="Debug Stata Error",
            description=(
                "Use a RunResult error object and the relevant code to diagnose "
                "a Stata failure without making edits unless the user asks."
            ),
            arguments=[
                PromptArgument(
                    name="code_or_path",
                    description="Failing Stata code or path to the source file.",
                    required=True,
                ),
                PromptArgument(
                    name="error_json",
                    description="Optional RunResult.error JSON from stata_run.",
                    required=False,
                ),
            ],
        ),
        Prompt(
            name="fix_and_rerun_until_passes",
            title="Fix and Rerun Until Passes",
            description=(
                "Iteratively edit Stata source code, run it, use structured "
                "diagnostics, and stop when the run passes or a blocker is clear."
            ),
            arguments=[
                PromptArgument(
                    name="path",
                    description="Path to the .do file or source file to repair.",
                    required=True,
                ),
                PromptArgument(
                    name="session_id",
                    description="Optional Stata session id; defaults to main.",
                    required=False,
                ),
            ],
        ),
        Prompt(
            name="replication_audit",
            title="Replication Audit",
            description=(
                "Run Stata analysis as a reproducibility check and summarize "
                "data dependencies, outputs, warnings, and failures."
            ),
            arguments=[
                PromptArgument(
                    name="path",
                    description="Path to the primary .do file or replication entrypoint.",
                    required=True,
                ),
            ],
        ),
        Prompt(
            name="summarize_estimation_results",
            title="Summarize Estimation Results",
            description=(
                "Turn a stata_run RunResult into a concise statistical summary "
                "using structured r()/e() fields before consulting logs."
            ),
            arguments=[
                PromptArgument(
                    name="run_result_json",
                    description="JSON text from stata_run, or a resource/ref to inspect.",
                    required=True,
                ),
            ],
        ),
        Prompt(
            name="run_notebook_cell_and_report",
            title="Run Notebook Cell and Report",
            description=(
                "Read one cell of a .ipynb and execute it through stata_run "
                "with origin_cell_id metadata. Report ok, rc, typed errors, "
                "warnings, and any artifacts without editing the cell."
            ),
            arguments=[
                PromptArgument(
                    name="path",
                    description="Path to the .ipynb file.",
                    required=True,
                ),
                PromptArgument(
                    name="cell_id",
                    description="Stable nbformat 4.5+ cell id (or a synthesised id).",
                    required=True,
                ),
                PromptArgument(
                    name="session_id",
                    description="Optional Stata session id; defaults to main.",
                    required=False,
                ),
            ],
        ),
        Prompt(
            name="fix_and_rerun_notebook_cell",
            title="Fix and Rerun a Notebook Cell",
            description=(
                "Iteratively repair one notebook cell: notebook_get_cell → "
                "stata_run with origin_cell_id → on failure, edit the cell "
                "with notebook_edit_cell using expected_source as a "
                "concurrency guard → rerun. Stop on success, on a small "
                "retry budget, or with a recommendation to restart the "
                "kernel if the same cell keeps failing."
            ),
            arguments=[
                PromptArgument(
                    name="path",
                    description="Path to the .ipynb file.",
                    required=True,
                ),
                PromptArgument(
                    name="cell_id",
                    description="Cell to repair (nbformat 4.5+ id).",
                    required=True,
                ),
                PromptArgument(
                    name="session_id",
                    description="Optional Stata session id; defaults to main.",
                    required=False,
                ),
                PromptArgument(
                    name="max_attempts",
                    description="Optional retry budget; defaults to 3.",
                    required=False,
                ),
            ],
        ),
    ]


def _prompt_text(name: str, arguments: dict[str, str] | None) -> tuple[str, str]:
    args = arguments or {}
    if name == "run_do_file_and_report":
        path = args.get("path", "<path>")
        session_id = args.get("session_id", "main")
        return (
            "Run the Stata do-file and report",
            (
                f"Run `{path}` in Stata session `{session_id}` using stata-code. "
                "Treat this as validation unless I explicitly ask for repairs: read "
                "the file, call `stata_run` with `origin_path`, `origin_kind='file'`, "
                "and `persist_log_files=true`, then report `ok`, `rc`, typed error "
                "details, warnings, generated logs/graphs/outputs, and any concise "
                "next steps. Prefer structuredContent fields over parsing the log; "
                "fetch full logs or graphs only if needed."
            ),
        )
    if name == "debug_stata_error":
        code_or_path = args.get("code_or_path", "<code-or-path>")
        error_json = args.get("error_json")
        extra = f" The RunResult.error JSON is: {error_json}" if error_json else ""
        return (
            "Diagnose the Stata error",
            (
                f"Diagnose this Stata failure from `{code_or_path}`.{extra} Use "
                "`stata_run` only if more evidence is needed. Explain the likely "
                "cause using `error.kind`, `error.line`, `error.context`, and "
                "`error.suggestions`. Do not edit source files unless I ask."
            ),
        )
    if name == "fix_and_rerun_until_passes":
        path = args.get("path", "<path>")
        session_id = args.get("session_id", "main")
        return (
            "Fix and rerun the Stata code",
            (
                f"Repair `{path}` in Stata session `{session_id}`. Read the source, "
                "make the smallest defensible edit, run it with `stata_run` using "
                "`origin_path`, inspect structured errors and warnings, and iterate "
                "until it passes or a concrete blocker remains. Preserve unrelated "
                "user changes. Report changed files, final status, artifacts, and "
                "any residual risk."
            ),
        )
    if name == "replication_audit":
        path = args.get("path", "<path>")
        return (
            "Audit Stata replication",
            (
                f"Audit the Stata replication entrypoint `{path}`. Run it with "
                "`persist_log_files=true`, inspect structured results, logs, graphs, "
                "and output artifacts, and summarize reproducibility risks: missing "
                "data/packages, path assumptions, nondeterminism, warnings, and "
                "failed commands. Do not rewrite source unless separately asked."
            ),
        )
    if name == "summarize_estimation_results":
        run_result_json = args.get("run_result_json", "<run-result-json-or-ref>")
        return (
            "Summarize Stata estimation results",
            (
                f"Summarize this stata-code RunResult: {run_result_json}. Use "
                "`results.e`, `results.r`, `dataset`, `warnings`, and `graphs` "
                "first; consult `log.ref` only for context missing from structured "
                "fields. Keep statistical claims tied to available coefficients, "
                "sample size, model command, and warnings."
            ),
        )
    if name == "run_notebook_cell_and_report":
        path = args.get("path", "<path>")
        cell_id = args.get("cell_id", "<cell-id>")
        session_id = args.get("session_id", "main")
        return (
            "Run a notebook cell and report",
            (
                f"Read cell `{cell_id}` of `{path}` via `notebook_get_cell`, "
                f"then execute it with `stata_run` in session `{session_id}`. "
                "Always pass `origin_path`, `origin_kind='cell'`, "
                "`origin_cell_id` (the same id), and `persist_log_files=true` "
                "so the run is traceable via `list_runs`. Report `ok`, `rc`, "
                "any `error.kind/line/context`, warnings, and the run-bundle "
                "directory. Do not edit the cell — this is a validation "
                "workflow, not a repair workflow."
            ),
        )
    if name == "fix_and_rerun_notebook_cell":
        path = args.get("path", "<path>")
        cell_id = args.get("cell_id", "<cell-id>")
        session_id = args.get("session_id", "main")
        max_attempts = args.get("max_attempts", "3")
        return (
            "Fix and rerun a notebook cell",
            (
                f"Repair cell `{cell_id}` of `{path}` in session "
                f"`{session_id}`. Loop:\n"
                "1. `notebook_get_cell(path, cell_id)` → capture `source`.\n"
                "2. `stata_run(code=source, origin_path=path, "
                "origin_kind='cell', origin_cell_id=cell_id, "
                "persist_log_files=true)`.\n"
                "3. If `ok=true`, stop and report.\n"
                "4. Otherwise use `error.line` (already cell-relative) and "
                "`error.context.failing` to make the smallest defensible "
                "edit, then call `notebook_edit_cell(path, cell_id, "
                "new_source, expected_source=source)` — the "
                "`expected_source` guard catches concurrent human edits.\n"
                f"5. Repeat up to {max_attempts} times. If still failing, "
                "stop and recommend `restart kernel + run all from top` "
                "rather than continuing to edit — repeated failure on one "
                "cell usually signals upstream-state pollution, not a code "
                "bug.\n"
                "Report changed cell, attempts made, final status, and any "
                "residual risk."
            ),
        )
    raise ValueError(f"Unknown prompt: {name}")


def _get_mcp_prompt(name: str, arguments: dict[str, str] | None = None) -> GetPromptResult:
    title, text = _prompt_text(name, arguments)
    return GetPromptResult(
        description=title,
        messages=[
            PromptMessage(
                role="user",
                content=TextContent(type="text", text=text),
            )
        ],
    )


if _MCP_AVAILABLE:

    @APP.list_tools()
    async def list_tools() -> list[Tool]:
        return _tool_definitions()

    @APP.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> Any:
        return await _dispatch(name, arguments)

    @APP.list_resources()
    async def list_resources() -> list[Resource]:
        return _list_mcp_resources()

    @APP.list_resource_templates()
    async def list_resource_templates() -> list[ResourceTemplate]:
        return _resource_templates()

    @APP.read_resource()
    async def read_resource(uri: Any) -> list[ReadResourceContents]:
        return [_read_resource_payload(str(uri))]

    @APP.list_prompts()
    async def list_prompts() -> list[Prompt]:
        return _prompt_definitions()

    @APP.get_prompt()
    async def get_prompt(
        name: str,
        arguments: dict[str, str] | None,
    ) -> GetPromptResult:
        return _get_mcp_prompt(name, arguments)


# ─────────────────────────────────────────────────────────────────────────────
# Dispatch (kept module-level for testability)
# ─────────────────────────────────────────────────────────────────────────────


_GRAPH_MIME = {
    "png": "image/png",
    "svg": "image/svg+xml",
    "pdf": "application/pdf",
}


def _list_sessions_payload(pool: Any) -> dict[str, Any]:
    """Build the ``list_sessions`` MCP response from a pool.

    Prefers the detailed view (which carries per-worker warnings) when the
    pool exposes it; falls back to the flat list for legacy mocks /
    older pool implementations. Warnings are omitted when empty so the
    happy-path response shape stays compact.
    """
    detailed = getattr(pool, "list_session_info_detailed", None)
    if callable(detailed):
        info = detailed()
        sessions = list(info.get("sessions") or [])
        warnings = list(info.get("warnings") or [])
    else:
        sessions = list(pool.list_session_info() or [])
        warnings = []
    payload: dict[str, Any] = {"sessions": sessions}
    if warnings:
        payload["warnings"] = warnings
    return payload


def _json_result(payload: dict[str, Any], text_payload: Any | None = None) -> Any:
    """Return structured MCP content while preserving a JSON text block."""
    return CallToolResult(
        content=[
            TextContent(
                type="text",
                text=json.dumps(payload if text_payload is None else text_payload),
            )
        ],
        structuredContent=payload,
        isError=False,
    )


def _error_result(message: str, *, kind: str = "tool_error") -> Any:
    payload = {"error": message, "kind": kind}
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(payload))],
        structuredContent=payload,
        isError=True,
    )


def _validate_tool_arguments(name: str, arguments: dict[str, Any]) -> Any | None:
    """Reject undeclared top-level tool arguments before dispatch.

    ``inputSchema`` is primarily an advertised contract; not every MCP
    client validates it before sending a call. Enforcing the same
    top-level ``additionalProperties: false`` rule here keeps typo
    detection server-side and deterministic.
    """
    for tool in _tool_definitions():
        if tool.name != name:
            continue
        schema = tool.inputSchema or {}
        if schema.get("additionalProperties") is not False:
            return None
        allowed = set((schema.get("properties") or {}).keys())
        unknown = sorted(set(arguments) - allowed)
        if not unknown:
            return None
        joined = ", ".join(unknown)
        return _error_result(
            f"Unknown argument(s) for {name}: {joined}",
            kind="invalid_request",
        )
    return None


async def _dispatch(name: str, arguments: dict[str, Any]) -> Any:
    validation_error = _validate_tool_arguments(name, arguments)
    if validation_error is not None:
        return validation_error

    try:
        if name == "stata_run":
            return await asyncio.to_thread(_run_tool, arguments)
        if name == "stata_info":
            return _json_result(json.loads(await _info_payload_async()))
        if name == "get_log":
            payload = get_log(arguments["ref"])
            return _json_result(payload)
        if name == "get_graph":
            ref = arguments["ref"]
            payload = get_graph(arguments["ref"])
            mime = _GRAPH_MIME.get(payload["format"], "image/png")
            metadata = {
                "ref": ref,
                "format": payload["format"],
                "mimeType": mime,
                "width": payload["width"],
                "height": payload["height"],
            }
            return CallToolResult(
                content=[
                    ImageContent(
                        type="image", data=payload["bytes_b64"], mimeType=mime
                    ),
                    TextContent(type="text", text=json.dumps(metadata)),
                ],
                structuredContent=metadata,
                isError=False,
            )
        if name == "get_matrix":
            payload = get_matrix(arguments["ref"])
            return _json_result(payload)
        if name == "list_sessions":
            # In subprocess-pool mode each session lives in its own worker
            # process, so the parent's `list_sessions()` (which queries the
            # parent's pystata frames) is empty. Authoritative source is
            # `pool.list_session_info()`, which round-trips a no-payload
            # `list_sessions` op to each live worker and aggregates. The
            # detailed variant additionally surfaces a `warnings` list
            # populated by workers that failed to respond, so callers can
            # distinguish "no other sessions" from "some workers timed
            # out". The plain `list_session_info()` method is still
            # advertised for older callers.
            result = await asyncio.to_thread(
                lambda: _list_sessions_payload(get_default_pool())
            )
            return _json_result(result, text_payload=result["sessions"])
        if name == "cancel_session":
            sid = arguments.get("session_id", "main")
            registered, killed_worker = await asyncio.to_thread(
                lambda: get_default_pool().request_cancel(sid)
            )
            was_pending = not registered
            # `is_pending` is reported as the post-registration state, which
            # is True by definition: `request_cancel` always adds the session
            # to `_cancel_pending` inside its own lock. A naive second call
            # to `is_cancel_pending` here would race a concurrent `execute()`
            # that consumes the flag between the two lock acquisitions and
            # could report False even though the cancel was successfully
            # registered. Trust the registration.
            return _json_result(
                {
                    "session_id": sid,
                    "was_pending": was_pending,
                    "is_pending": True,
                    "killed_worker": killed_worker,
                }
            )
        if name == "reset_session":
            sid = arguments.get("session_id", "main")
            # Pool-mode: killing the session's worker drops its data and
            # all in-memory state; the next stata_run for that session
            # respawns a fresh worker. For "main" this is equivalent to
            # `clear all` (both wipe data + r()/e()), with the wrinkle
            # that ref-store entries this session produced stay valid in
            # the parent's `_refs` LRU until naturally evicted.
            dropped = await asyncio.to_thread(
                lambda: get_default_pool().reset_session(sid)
            )
            return _json_result(
                {
                    "session_id": sid,
                    "dropped_frame": dropped,
                }
            )
        if name == "notebook_outline":
            return _notebook_outline_tool(arguments)
        if name == "notebook_get_cell":
            return _notebook_get_cell_tool(arguments)
        if name == "notebook_locate":
            return _notebook_locate_tool(arguments)
        if name == "notebook_edit_cell":
            return _notebook_edit_cell_tool(arguments)
        if name == "notebook_insert_cell":
            return _notebook_insert_cell_tool(arguments)
        if name == "notebook_delete_cell":
            return _notebook_delete_cell_tool(arguments)
        if name == "list_runs":
            return _list_runs_tool(arguments)
        return _error_result(f"Unknown tool: {name}", kind="unknown_tool")
    except NotebookError as exc:
        return _error_result(str(exc), kind=exc.kind)
    except RunIndexError as exc:
        return _error_result(str(exc), kind=exc.kind)
    except RefNotFound as exc:
        return _error_result(f"Unknown ref: {exc.ref}", kind=exc.kind)
    except KeyError as exc:
        # Defensive: a plain KeyError from refs (shouldn't happen after the
        # RefNotFound migration, but keep the safety net so we never bubble
        # a stack trace out of the MCP server).
        return _error_result(f"Unknown ref: {exc}", kind="unknown_ref")
    except PystataNotAvailable as exc:
        return _error_result(f"Stata not available: {exc}", kind="stata_unavailable")
    except (ValueError, NotImplementedError) as exc:
        return _error_result(f"{type(exc).__name__}: {exc}", kind="invalid_request")
    except Exception as exc:  # noqa: BLE001 - last-resort safety net
        return _error_result(f"Error: {type(exc).__name__}: {exc}", kind="internal_error")


_RUN_BOOL_KEYS: tuple[tuple[str, bool], ...] = (
    ("include_full_log", False),
    ("include_dataset_variables", True),
    ("persist_log_files", False),
    ("persist_generated_files", True),
    ("use_origin_workdir", True),
)


def _run_tool(arguments: dict[str, Any]) -> Any:
    args = dict(arguments)
    code = args.pop("code", None)
    if not code:
        return _error_result("code is required", kind="missing_argument")
    # Validate booleans up front rather than letting truthy strings ("false",
    # "no", …) silently flip behaviour inside the runner.
    for key, default in _RUN_BOOL_KEYS:
        if key not in args:
            continue
        value, err = _bool_arg(args, key, default=default)
        if err is not None:
            return err
        args[key] = value
    try:
        result = pool_execute(code, **args)
    except (ValueError, NotImplementedError) as exc:
        return _error_result(f"{type(exc).__name__}: {exc}", kind="invalid_request")
    payload = json.loads(result.model_dump_json())
    return _json_result(payload)


def _notebook_outline_tool(arguments: dict[str, Any]) -> Any:
    path = arguments.get("path")
    if not isinstance(path, str) or not path:
        return _error_result("path is required", kind="missing_argument")
    preview_lines = arguments.get("preview_lines", 2)
    if not isinstance(preview_lines, int) or preview_lines < 0:
        return _error_result(
            "preview_lines must be a non-negative integer",
            kind="invalid_request",
        )
    payload = _notebook_outline(path, preview_lines=preview_lines)
    return _json_result(payload)


def _notebook_get_cell_tool(arguments: dict[str, Any]) -> Any:
    path = arguments.get("path")
    if not isinstance(path, str) or not path:
        return _error_result("path is required", kind="missing_argument")
    cell_id = arguments.get("cell_id")
    cell_index = arguments.get("cell_index")
    if cell_id is None and cell_index is None:
        return _error_result(
            "either cell_id or cell_index is required",
            kind="missing_argument",
        )
    if cell_id is not None and not isinstance(cell_id, str):
        return _error_result("cell_id must be a string", kind="invalid_request")
    if cell_index is not None and not isinstance(cell_index, int):
        return _error_result("cell_index must be an integer", kind="invalid_request")
    payload = _notebook_get_cell(
        path,
        cell_id=cell_id,
        cell_index=cell_index,
    )
    return _json_result(payload)


def _notebook_locate_tool(arguments: dict[str, Any]) -> Any:
    path = arguments.get("path")
    if not isinstance(path, str) or not path:
        return _error_result("path is required", kind="missing_argument")
    snippet = arguments.get("snippet")
    regex = arguments.get("regex")
    error_text = arguments.get("error_text")
    cell_type = arguments.get("cell_type")
    limit = arguments.get("limit", 10)
    for label, value in (("snippet", snippet), ("regex", regex), ("error_text", error_text)):
        if value is not None and not isinstance(value, str):
            return _error_result(f"{label} must be a string", kind="invalid_request")
    if cell_type is not None and not isinstance(cell_type, str):
        return _error_result("cell_type must be a string", kind="invalid_request")
    if not isinstance(limit, int):
        return _error_result("limit must be an integer", kind="invalid_request")
    payload = _notebook_locate_cells(
        path,
        snippet=snippet,
        regex=regex,
        error_text=error_text,
        cell_type=cell_type,
        limit=limit,
    )
    return _json_result(payload)


def _require_str(arguments: dict[str, Any], key: str) -> tuple[str, Any]:
    """Read a required non-empty string argument.

    On success: ``(value, None)``. On failure: ``("", error_result)`` —
    callers MUST check ``err is not None`` and early-return before using
    the first element. The empty-string sentinel keeps the success-path
    type as plain ``str`` (no Optional) so the dispatchers don't need
    runtime assertions to satisfy the type checker.
    """
    value = arguments.get(key)
    if not isinstance(value, str) or not value:
        return "", _error_result(f"{key} is required", kind="missing_argument")
    return value, None


_SENTINEL = object()


def _bool_arg(
    arguments: dict[str, Any], key: str, *, default: bool
) -> tuple[bool, Any]:
    """Read an optional boolean argument with strict JSON-bool typing.

    JSON has a real boolean type, and the inputSchema declares these args
    as ``"type": "boolean"``. Many MCP clients do not enforce the schema,
    so we reject coerced values like ``"true"``, ``1``, or ``"yes"`` here
    rather than silently truthy-coerce them (``bool("false") is True``).

    ``isinstance(x, bool)`` happens to also gate against ``int`` even
    though ``bool`` is a subclass of ``int`` — only ``True`` / ``False``
    pass the check. Returns ``(value, None)`` on success, or
    ``(default, error_result)`` when the type is wrong.
    """
    value = arguments.get(key, _SENTINEL)
    if value is _SENTINEL or value is None:
        return default, None
    if not isinstance(value, bool):
        return default, _error_result(
            f"{key} must be a boolean (true/false), got {type(value).__name__}",
            kind="invalid_request",
        )
    return value, None


def _notebook_edit_cell_tool(arguments: dict[str, Any]) -> Any:
    path, err = _require_str(arguments, "path")
    if err is not None:
        return err
    cell_id, err = _require_str(arguments, "cell_id")
    if err is not None:
        return err
    new_source = arguments.get("new_source")
    if not isinstance(new_source, str):
        return _error_result(
            "new_source is required and must be a string",
            kind="missing_argument",
        )
    expected_source = arguments.get("expected_source")
    if expected_source is not None and not isinstance(expected_source, str):
        return _error_result(
            "expected_source must be a string", kind="invalid_request"
        )
    payload = _notebook_edit_cell(
        path,
        cell_id=cell_id,
        new_source=new_source,
        expected_source=expected_source,
    )
    return _json_result(payload)


def _notebook_insert_cell_tool(arguments: dict[str, Any]) -> Any:
    path, err = _require_str(arguments, "path")
    if err is not None:
        return err
    source = arguments.get("source")
    if not isinstance(source, str):
        return _error_result(
            "source is required and must be a string",
            kind="missing_argument",
        )
    cell_type = arguments.get("cell_type", "code")
    if not isinstance(cell_type, str):
        return _error_result("cell_type must be a string", kind="invalid_request")
    after_cell_id = arguments.get("after_cell_id")
    before_cell_id = arguments.get("before_cell_id")
    at_start, err = _bool_arg(arguments, "at_start", default=False)
    if err is not None:
        return err
    at_end, err = _bool_arg(arguments, "at_end", default=False)
    if err is not None:
        return err
    for label, value in (
        ("after_cell_id", after_cell_id),
        ("before_cell_id", before_cell_id),
    ):
        if value is not None and not isinstance(value, str):
            return _error_result(f"{label} must be a string", kind="invalid_request")
    payload = _notebook_insert_cell(
        path,
        source=source,
        cell_type=cell_type,
        after_cell_id=after_cell_id,
        before_cell_id=before_cell_id,
        at_start=at_start,
        at_end=at_end,
    )
    return _json_result(payload)


def _notebook_delete_cell_tool(arguments: dict[str, Any]) -> Any:
    path, err = _require_str(arguments, "path")
    if err is not None:
        return err
    cell_id, err = _require_str(arguments, "cell_id")
    if err is not None:
        return err
    expected_source = arguments.get("expected_source")
    if expected_source is not None and not isinstance(expected_source, str):
        return _error_result(
            "expected_source must be a string", kind="invalid_request"
        )
    payload = _notebook_delete_cell(
        path,
        cell_id=cell_id,
        expected_source=expected_source,
    )
    return _json_result(payload)


def _list_runs_tool(arguments: dict[str, Any]) -> Any:
    log_dir = arguments.get("log_dir")
    origin_path = arguments.get("origin_path")
    if log_dir is None and origin_path is None:
        return _error_result(
            "either log_dir or origin_path is required",
            kind="missing_argument",
        )
    for label, value in (("log_dir", log_dir), ("origin_path", origin_path)):
        if value is not None and not isinstance(value, str):
            return _error_result(f"{label} must be a string", kind="invalid_request")

    cell_id = arguments.get("cell_id")
    session_id = arguments.get("session_id")
    since = arguments.get("since")
    for label, value in (
        ("cell_id", cell_id),
        ("session_id", session_id),
        ("since", since),
    ):
        if value is not None and not isinstance(value, str):
            return _error_result(f"{label} must be a string", kind="invalid_request")

    ok = arguments.get("ok")
    if ok is not None and not isinstance(ok, bool):
        return _error_result("ok must be a boolean", kind="invalid_request")

    limit = arguments.get("limit", 50)
    # bool is a subclass of int — reject explicitly so True/False don't slip
    # past as 1/0.
    if isinstance(limit, bool) or not isinstance(limit, int):
        return _error_result("limit must be an integer", kind="invalid_request")

    payload = _list_runs(
        log_dir=log_dir,
        origin_path=origin_path,
        cell_id=cell_id,
        session_id=session_id,
        ok=ok,
        since=since,
        limit=limit,
    )
    return _json_result(payload)


async def _info_payload_async() -> str:
    return await asyncio.to_thread(_info_payload_from_pool)


def _info_payload_from_pool() -> str:
    try:
        stata = pool_stata_info()
    except Exception as exc:  # noqa: BLE001
        # PystataNotAvailable from the worker is the legitimate "Stata not
        # installed" case — report cleanly. Other failures (subprocess crash,
        # timeout, broken pipe) also surface as available=false so existing
        # clients keep their happy path, but we attach an `error` field so
        # users can distinguish operational problems from "no Stata here".
        payload: dict[str, Any] = {
            "available": False,
            "schema_version": "1.0",
            "capabilities": [],
        }
        if not _is_pystata_unavailable_error(exc):
            payload["error"] = f"{type(exc).__name__}: {exc}"
        return json.dumps(payload)

    # `stata.edition` is the StataEdition enum value (e.g. "MP"). Mirror it
    # at the top level for backward-compat consumers; lowercasing here would
    # contradict `stata.edition` in the same payload.
    edition_alias = stata.get("edition") if isinstance(stata.get("edition"), str) else None
    return _info_payload_from_stata(stata, edition_alias=edition_alias)


def _is_pystata_unavailable_error(exc: BaseException) -> bool:
    """Return True iff ``exc`` ultimately means "pystata couldn't be loaded".

    The worker formats its errors as ``f"{type(exc).__name__}: {exc}"`` and
    ``send_simple_op`` wraps that as ``"worker reported failure: <error>"``.
    A ``PystataNotAvailable`` raised directly in the parent process matches
    the same ``"PystataNotAvailable: …"`` pattern. We anchor on the exact
    class-name prefix to avoid matching unrelated errors that happen to
    mention the word in passing (e.g. a debug message).
    """
    if isinstance(exc, PystataNotAvailable):
        return True
    text = str(exc)
    name = PystataNotAvailable.__name__
    return f"{name}:" in text or text == name


def _info_payload_from_stata(
    stata: dict[str, Any],
    *,
    edition_alias: str | None = None,
    version_alias: str | None = None,
) -> str:
    version = version_alias if version_alias is not None else stata.get("version")
    edition = edition_alias if edition_alias is not None else stata.get("edition")
    return json.dumps(
        {
            "available": True,
            "stata": stata,
            # Backward-compatible flat aliases retained for older clients.
            "edition": edition,
            "version": version,
            "backend": "pystata",
            "schema_version": "1.0",
            "capabilities": [
                "log_truncation",
                "graph_ref",
                "matrix_ref",
                "multi_session",
                "subprocess_timeout",
                "log_files",
                "run_artifacts",
                # Notebook side-channel — present when the server registers
                # the corresponding `notebook_*` and `list_runs` tools.
                # Clients can feature-detect rather than calling each tool
                # blind. The protocol's execution path stays cell-agnostic;
                # these advertise the side-channel surface only.
                "notebook_navigation",  # notebook_outline, notebook_get_cell
                "notebook_search",       # notebook_locate
                "notebook_edit",         # edit / insert / delete cell
                "run_index",             # list_runs over manifest bundles
                "origin_echo",           # RunResult.origin round-trip
            ],
        }
    )


# ─────────────────────────────────────────────────────────────────────────────
# Entrypoint
# ─────────────────────────────────────────────────────────────────────────────


async def main() -> None:
    if not _MCP_AVAILABLE:
        print(
            'ERROR: MCP support is not installed. Install with: python -m pip install "stata-code[mcp]"',
            file=sys.stderr,
        )
        sys.exit(1)
    async with stdio_server() as (read, write):
        await APP.run(read, write, APP.create_initialization_options())


def run_main() -> None:
    """Synchronous entry point for the `stata-code-mcp` console script."""
    asyncio.run(main())


if __name__ == "__main__":  # pragma: no cover
    run_main()
