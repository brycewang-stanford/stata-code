"""End-to-end MCP transport checks using the official Python client."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

pytest.importorskip("mcp", reason="mcp package not installed")

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

_ROOT = Path(__file__).resolve().parents[1]


async def _exercise_server() -> None:
    env = os.environ.copy()
    current_path = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        str(_ROOT) if not current_path else str(_ROOT) + os.pathsep + current_path
    )
    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "stata_code.mcp"],
        cwd=_ROOT,
        env=env,
    )

    async with stdio_client(server) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            listed = await session.list_tools()
            names = {tool.name for tool in listed.tools}
            assert {"stata_info", "stata_run", "list_sessions"} <= names

            info = await session.call_tool("stata_info", {})
            assert info.isError is False
            assert info.structuredContent["schema_version"] == "1.0"

            # A second request on the same session proves stdin remained open
            # after the first response and the stdio server kept serving work.
            listed_again = await session.list_tools()
            assert len(listed_again.tools) == len(listed.tools)


def test_stdio_server_round_trip() -> None:
    asyncio.run(_exercise_server())
