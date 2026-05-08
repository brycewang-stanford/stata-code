"""MCP server exposing the stata_code v1.0 pipeline.

Tools registered:
- stata_run        — execute Stata code, return a v1.0 RunResult JSON
- stata_info       — report Stata edition / version / capabilities
- get_log          — fetch full log behind a `log://` ref
- get_graph        — fetch graph bytes behind a `graph://` ref (ImageContent)
- list_sessions    — enumerate live sessions (frames)
- reset_session    — drop a session's data

The result envelope, token-economy defaults (log head+tail+ref, graph refs
not inline), session model, and error taxonomy follow SCHEMA.md.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import ImageContent, TextContent, Tool

    _MCP_AVAILABLE = True
except ImportError:  # pragma: no cover - environment without mcp installed
    Server = None  # type: ignore[assignment,misc]
    Tool = None  # type: ignore[assignment,misc]
    TextContent = None  # type: ignore[assignment,misc]
    ImageContent = None  # type: ignore[assignment,misc]
    stdio_server = None  # type: ignore[assignment]
    _MCP_AVAILABLE = False

from stata_code.core._pool import get_default_pool, pool_execute
from stata_code.core._runtime import PystataNotAvailable, is_available
from stata_code.core.runner import (
    cancel,
    get_graph,
    get_log,
    get_matrix,
    is_cancel_pending,
)

__version__ = "0.4.0"

APP: Any = Server("stata-code") if _MCP_AVAILABLE else None


# ─────────────────────────────────────────────────────────────────────────────
# Tool registry
# ─────────────────────────────────────────────────────────────────────────────


def _tool_definitions() -> list[Tool]:
    return [
        Tool(
            name="stata_run",
            description=(
                "Execute Stata code and return a v1.0 stata_code RunResult "
                "(see SCHEMA.md). The result is a JSON object with ok, rc, "
                "error (typed), log (head+tail+ref by default), results.r/e "
                "(scalars/macros/matrices, native types), dataset metadata, "
                "graphs, warnings, and capabilities. Use the structured "
                "fields rather than parsing the log."
            ),
            inputSchema={
                "type": "object",
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
                        "enum": ["file", "selection", "line", "cell", "code", "unknown"],
                        "description": "Which editor surface produced the submitted code.",
                    },
                    "origin_label": {
                        "type": "string",
                        "description": (
                            "Human-readable source label, for example "
                            "demo/test1.do:1."
                        ),
                    },
                },
                "required": ["code"],
            },
        ),
        Tool(
            name="stata_info",
            description=(
                "Report installed Stata edition, version, backend, and "
                "whether the runtime is initialized."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="get_log",
            description=(
                "Fetch the full log text behind a log:// ref returned by a "
                "prior stata_run call. Returns JSON {text, lines_total, "
                "bytes_total}."
            ),
            inputSchema={
                "type": "object",
                "properties": {"ref": {"type": "string"}},
                "required": ["ref"],
            },
        ),
        Tool(
            name="get_graph",
            description=(
                "Fetch graph bytes behind a graph:// ref. Returns an "
                "ImageContent (base64 bytes + mimeType) suitable for direct "
                "display by vision-capable clients."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "ref": {"type": "string"},
                    "format": {
                        "type": "string",
                        "enum": ["png", "svg", "pdf"],
                    },
                },
                "required": ["ref"],
            },
        ),
        Tool(
            name="get_matrix",
            description=(
                "Fetch a matrix's values, rows, and cols behind a matrix:// "
                "ref. Producers emit a ref instead of inlining values when "
                "the matrix exceeds ~10,000 cells. Returns JSON {rows, cols, "
                "values}."
            ),
            inputSchema={
                "type": "object",
                "properties": {"ref": {"type": "string"}},
                "required": ["ref"],
            },
        ),
        Tool(
            name="list_sessions",
            description=(
                "Enumerate live sessions. Each entry has session_id, frame "
                "(Stata frame name), and n_obs."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="cancel_session",
            description=(
                "Request cooperative cancellation of the next stata_run for "
                "this session. The flag is consumed by the next call and "
                "produces a RunResult with ok=false, rc=-3, "
                "error.kind='cancelled'. Does NOT interrupt code that is "
                "currently mid-execution (pystata is in-process). Returns "
                "JSON {session_id, was_pending, is_pending}."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "default": "main"},
                },
            },
        ),
        Tool(
            name="reset_session",
            description=(
                "Drop a session's data. session_id='main' performs `clear "
                "all` in place (default frame cannot be dropped); other "
                "names drop the corresponding Stata frame."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "default": "main"},
                },
            },
        ),
    ]


if _MCP_AVAILABLE:

    @APP.list_tools()
    async def list_tools() -> list[Tool]:
        return _tool_definitions()

    @APP.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[Any]:
        return await _dispatch(name, arguments)


# ─────────────────────────────────────────────────────────────────────────────
# Dispatch (kept module-level for testability)
# ─────────────────────────────────────────────────────────────────────────────


_GRAPH_MIME = {
    "png": "image/png",
    "svg": "image/svg+xml",
    "pdf": "application/pdf",
}


async def _dispatch(name: str, arguments: dict[str, Any]) -> list[Any]:
    try:
        if name == "stata_run":
            return _run_tool(arguments)
        if name == "stata_info":
            return [TextContent(type="text", text=_info_payload())]
        if name == "get_log":
            payload = get_log(arguments["ref"])
            return [TextContent(type="text", text=json.dumps(payload))]
        if name == "get_graph":
            payload = get_graph(arguments["ref"])
            mime = _GRAPH_MIME.get(payload["format"], "image/png")
            return [
                ImageContent(
                    type="image", data=payload["bytes_b64"], mimeType=mime
                )
            ]
        if name == "get_matrix":
            payload = get_matrix(arguments["ref"])
            return [TextContent(type="text", text=json.dumps(payload))]
        if name == "list_sessions":
            # In subprocess-pool mode each session lives in its own worker
            # process, so the parent's `list_sessions()` (which queries the
            # parent's pystata frames) is empty. Authoritative source is
            # `pool.list_session_info()`, which round-trips a no-payload
            # `list_sessions` op to each live worker and aggregates. Dead
            # or unresponsive workers are skipped silently — partial info
            # beats failing the whole list call.
            sessions = get_default_pool().list_session_info()
            return [TextContent(type="text", text=json.dumps(sessions))]
        if name == "cancel_session":
            sid = arguments.get("session_id", "main")
            was_pending = not cancel(sid)  # cancel() returns False if already pending
            # In subprocess-pool mode, also SIGTERM the worker so an in-flight
            # call that's blocked inside Stata C-land actually terminates rather
            # than waiting for the next inter-command cooperative checkpoint.
            killed_worker = get_default_pool().kill_session(sid)
            return [
                TextContent(
                    type="text",
                    text=json.dumps(
                        {
                            "session_id": sid,
                            "was_pending": was_pending,
                            "is_pending": is_cancel_pending(sid),
                            "killed_worker": killed_worker,
                        }
                    ),
                )
            ]
        if name == "reset_session":
            sid = arguments.get("session_id", "main")
            # Pool-mode: killing the session's worker drops its data and
            # all in-memory state; the next stata_run for that session
            # respawns a fresh worker. For "main" this is equivalent to
            # `clear all` (both wipe data + r()/e()), with the wrinkle
            # that ref-store entries this session produced stay valid in
            # the parent's `_refs` LRU until naturally evicted.
            dropped = get_default_pool().kill_session(sid)
            return [
                TextContent(
                    type="text",
                    text=json.dumps(
                        {
                            "session_id": sid,
                            "dropped_frame": dropped,
                        }
                    ),
                )
            ]
        return [TextContent(type="text", text=f"Unknown tool: {name}")]
    except KeyError as exc:
        return [TextContent(type="text", text=f"Unknown ref: {exc}")]
    except PystataNotAvailable as exc:
        return [TextContent(type="text", text=f"Stata not available: {exc}")]
    except (ValueError, NotImplementedError) as exc:
        return [TextContent(type="text", text=f"{type(exc).__name__}: {exc}")]
    except Exception as exc:  # noqa: BLE001 - last-resort safety net
        return [TextContent(type="text", text=f"Error: {type(exc).__name__}: {exc}")]


def _run_tool(arguments: dict[str, Any]) -> list[Any]:
    args = dict(arguments)
    code = args.pop("code", None)
    if not code:
        return [TextContent(type="text", text='{"error": "code is required"}')]
    try:
        result = pool_execute(code, **args)
    except (ValueError, NotImplementedError) as exc:
        return [
            TextContent(
                type="text",
                text=json.dumps({"error": f"{type(exc).__name__}: {exc}"}),
            )
        ]
    return [TextContent(type="text", text=result.model_dump_json())]


def _info_payload() -> str:
    if not is_available():
        return json.dumps({"available": False})
    from stata_code.core._runtime import get_runtime

    rt = get_runtime()
    return json.dumps(
        {
            "available": True,
            "edition": rt.edition,
            "backend": "pystata",
            "schema_version": "1.0",
        }
    )


# ─────────────────────────────────────────────────────────────────────────────
# Entrypoint
# ─────────────────────────────────────────────────────────────────────────────


async def main() -> None:
    if not _MCP_AVAILABLE:
        print(
            "ERROR: mcp package not installed. Install with: pip install mcp",
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
