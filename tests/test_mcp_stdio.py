"""JSON-RPC stdio smoke tests for the MCP server.

These tests exercise the transport boundary that direct ``_dispatch`` tests do
not cover: process startup, initialize/initialized sequencing, stdout flushing,
and JSON-RPC response delivery while stdin remains open.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("mcp", reason="mcp package not installed")


_ROOT = Path(__file__).resolve().parents[1]


class StdioMcpClient:
    def __init__(self) -> None:
        env = os.environ.copy()
        env["PYTHONPATH"] = (
            str(_ROOT)
            if not env.get("PYTHONPATH")
            else str(_ROOT) + os.pathsep + env["PYTHONPATH"]
        )
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "stata_code.mcp"],
            cwd=_ROOT,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        self.stdout: queue.Queue[str] = queue.Queue()
        self.stderr: queue.Queue[str] = queue.Queue()
        self._stdout_thread = threading.Thread(
            target=self._read_lines, args=(self.proc.stdout, self.stdout), daemon=True
        )
        self._stderr_thread = threading.Thread(
            target=self._read_lines, args=(self.proc.stderr, self.stderr), daemon=True
        )
        self._stdout_thread.start()
        self._stderr_thread.start()

    @staticmethod
    def _read_lines(stream: Any, target: queue.Queue[str]) -> None:
        if stream is None:
            return
        for line in stream:
            target.put(line.rstrip("\n"))

    def close(self) -> None:
        if self.proc.stdin is not None:
            try:
                self.proc.stdin.close()
            except OSError:
                pass
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=5)

    def send(self, message: dict[str, Any]) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()

    def recv_response(self, request_id: int, *, timeout: float = 10.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        seen: list[str] = []
        while time.monotonic() < deadline:
            remaining = max(0.01, deadline - time.monotonic())
            try:
                line = self.stdout.get(timeout=remaining)
            except queue.Empty:
                break
            seen.append(line)
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if payload.get("id") == request_id:
                return payload
        stderr = list(self.stderr.queue)
        raise AssertionError(
            f"no JSON-RPC response for id={request_id}; stdout={seen!r}; stderr={stderr!r}"
        )


def _initialize(client: StdioMcpClient) -> None:
    client.send(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "pytest-stdio", "version": "0.1.0"},
            },
        }
    )
    response = client.recv_response(1, timeout=10)
    assert "result" in response
    client.send({"jsonrpc": "2.0", "method": "notifications/initialized"})


@pytest.fixture
def mcp_stdio_client() -> StdioMcpClient:
    client = StdioMcpClient()
    try:
        yield client
    finally:
        client.close()


def test_stdio_tools_list_returns_expected_tools(mcp_stdio_client: StdioMcpClient) -> None:
    _initialize(mcp_stdio_client)

    mcp_stdio_client.send(
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
    )
    response = mcp_stdio_client.recv_response(2, timeout=10)

    tools = response["result"]["tools"]
    names = {tool["name"] for tool in tools}
    assert {"stata_info", "stata_run", "list_sessions"}.issubset(names)


def test_stdio_stata_info_returns_without_closing_stdin(
    mcp_stdio_client: StdioMcpClient,
) -> None:
    _initialize(mcp_stdio_client)

    mcp_stdio_client.send(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "stata_info", "arguments": {}},
        }
    )
    response = mcp_stdio_client.recv_response(2, timeout=30)

    content = response["result"]["content"]
    body = json.loads(next(item["text"] for item in content if item["type"] == "text"))
    assert isinstance(body["available"], bool)
    assert body["schema_version"] == "1.0"
