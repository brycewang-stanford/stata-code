"""MCP server for stata_code — exposes the core adapter via the Model Context Protocol."""

from __future__ import annotations

import json
import sys
from typing import Any

# MCP protocol types — install via: pip install mcp
try:
    from mcp.server import Server
    from mcp.types import Tool, TextContent
    from mcp.server.stdio import serve_stdio
except ImportError:
    Server = None
    Tool = None
    TextContent = None

from stata_code import run, StataResult
from stata_code.core.version import detect_stata

__version__ = "0.1.0"

# ─────────────────────────────────────────────────────────────────────────────
# Server setup
# ─────────────────────────────────────────────────────────────────────────────

APP = Server("stata_code")


@APP.list_tools()
async def list_tools() -> list[Tool]:
    """
    Declare the tools this MCP server exposes to LLM agents.

    Currently defined tools:
    - ``stata_run`` — execute Stata code and return structured results
    - ``stata_version`` — return installed Stata edition and version
    """
    return [
        Tool(
            name="stata_run",
            description=(
                "Execute Stata code and return structured results. "
                "Use this tool to run any Stata command or .do file content. "
                "Returns stdout log, e()/r() scalars, graphs as base64 data URIs, "
                "and any error messages. Best for: regression, data manipulation, "
                "summary statistics, estimation results, graph generation."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "Stata command(s) to execute. Can include newlines.",
                    },
                    "capture_graphs": {
                        "type": "boolean",
                        "description": "If true, capture graph files as base64 data URIs.",
                        "default": True,
                    },
                    "timeout": {
                        "type": "number",
                        "description": "Timeout in seconds (default 120).",
                        "default": 120,
                    },
                },
                "required": ["code"],
            },
        ),
        Tool(
            name="stata_version",
            description=(
                "Return the detected Stata installation info: "
                "edition (MP/SE/IC/BE), version string (e.g. '18.0'), "
                "and whether pystata is available."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
    ]


@APP.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    """Handle tool invocation requests from the MCP client."""
    if name == "stata_run":
        return await _stata_run(arguments)
    elif name == "stata_version":
        return await _stata_version(arguments)
    else:
        return [TextContent(type="text", text=f"Unknown tool: {name}")]


async def _stata_run(args: dict[str, Any]) -> list[TextContent]:
    """Run Stata code and return results as structured text."""
    code = args.get("code", "")
    capture_graphs = args.get("capture_graphs", True)
    timeout = args.get("timeout", 120.0)

    try:
        result: StataResult = run(
            code,
            capture_graphs=capture_graphs,
            timeout=timeout,
        )
        return [_result_to_text(result)]
    except Exception as exc:
        return [TextContent(type="text", text=f"Error: {exc}")]


async def _stata_version(_args: dict[str, Any]) -> list[TextContent]:
    """Return Stata version info."""
    info = detect_stata()
    text = (
        f"edition={info.edition.value}, "
        f"version={info.version}, "
        f"pystata_available={info.supports_pystata}"
    )
    return [TextContent(type="text", text=text)]


def _result_to_text(result: StataResult) -> TextContent:
    """
    Convert a StataResult into a human-readable TextContent for MCP transport.

    Graphs are included as base64 data URIs so agents can render them inline.
    """
    parts = []

    # Status line
    status = "OK" if result.success else f"ERR({result.return_code})"
    parts.append(f"[stata_code] {status}  elapsed={result.elapsed_seconds:.2f}s")

    if result.log:
        parts.append(f"\n--- STATA LOG ---\n{result.log}")

    if result.results:
        parts.append(f"\n--- RETURN VALUES ---\n{_format_results(result.results)}")

    if result.graphs:
        for g in result.graphs:
            parts.append(f"\n--- GRAPH ({g.format}) ---\n{g.to_data_uri()}")

    if result.error:
        parts.append(f"\n!!! ERROR: {result.error}")

    if result.warnings:
        parts.append(f"\nwarnings: {', '.join(result.warnings)}")

    return TextContent(type="text", text="\n".join(parts))


def _format_results(results: dict[str, Any]) -> str:
    """Format r()/e() dict as a readable string."""
    lines = []
    for key, val in results.items():
        if isinstance(val, list) and len(val) > 5:
            val_str = f"list[{len(val)}]"
        elif isinstance(val, str) and len(val) > 80:
            val_str = val[:80] + "..."
        else:
            val_str = str(val)
        lines.append(f"  {key} = {val_str}")
    return "\n".join(lines) if lines else "(no return values)"


# ─────────────────────────────────────────────────────────────────────────────
# Entrypoint
# ─────────────────────────────────────────────────────────────────────────────

async def main() -> None:
    """Serve the MCP server on stdio."""
    if Server is None:
        print(
            "ERROR: mcp package not installed. "
            "Install with: pip install mcp",
            file=sys.stderr,
        )
        sys.exit(1)
    await serve_stdio(APP)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())