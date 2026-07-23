"""Write MCP client configs so `stata-code` wires into an agent in one command.

`stata-code doctor` is deliberately read-only; this module is the opt-in,
mutating counterpart behind `stata-code setup`. It merges a ``stata-code`` MCP
server entry into a client's JSON config, preserving any servers already there
and backing up the file it overwrites.

Only JSON-schema clients are written here (Claude Code, Cursor, VS Code). Codex
(TOML) and Claude Desktop (absolute platform paths) are surfaced as copy-paste
snippets by the CLI rather than edited in place, so a fragile format round-trip
can never corrupt a user's config.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

SERVER_KEY = "stata-code"

SchemaKind = Literal["mcpServers", "servers"]


@dataclass(frozen=True)
class ClientSpec:
    """Where and how one client stores its MCP server config."""

    name: str
    #: Config path relative to the workspace root.
    rel_path: str
    #: Top-level object the server entry lives under.
    top_key: SchemaKind
    #: Human label for messages.
    label: str


CLIENTS: dict[str, ClientSpec] = {
    "claude": ClientSpec("claude", ".mcp.json", "mcpServers", "Claude Code (project)"),
    "cursor": ClientSpec("cursor", ".cursor/mcp.json", "mcpServers", "Cursor (project)"),
    "vscode": ClientSpec("vscode", ".vscode/mcp.json", "servers", "VS Code (project)"),
}


@dataclass(frozen=True)
class ChangeReport:
    """Outcome of applying one client's config."""

    client: str
    path: str
    action: Literal["created", "updated", "unchanged", "error"]
    backup: str | None = None
    detail: str | None = None


def resolve_server_command(python: str | None = None) -> list[str]:
    """Resolve the argv that launches the MCP server.

    With ``python`` given, use ``<python> -m stata_code.mcp`` so a specific
    interpreter / virtualenv is pinned. Otherwise prefer an absolute
    ``stata-code-mcp`` on PATH (robust when a GUI client has a thin PATH),
    falling back to the bare command name.
    """
    if python:
        return [python, "-m", "stata_code.mcp"]
    found = shutil.which("stata-code-mcp")
    return [found or "stata-code-mcp"]


def _server_entry(command: list[str], top_key: SchemaKind) -> dict[str, object]:
    entry: dict[str, object] = {"command": command[0], "args": command[1:]}
    if top_key == "servers":
        # VS Code's mcp.json requires an explicit transport type.
        return {"type": "stdio", **entry}
    return entry


def _load_json(path: Path) -> tuple[dict, str | None]:
    """Load a JSON object from ``path``; return ``({}, error)`` on trouble."""
    if not path.is_file():
        return {}, None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {}, f"{type(exc).__name__}: {exc}"
    if not isinstance(data, dict):
        return {}, "existing config is not a JSON object"
    return data, None


def apply_client(
    client: str,
    *,
    workspace: Path,
    command: list[str],
    dry_run: bool = False,
) -> ChangeReport:
    """Merge the ``stata-code`` server entry into one client's config."""
    spec = CLIENTS[client]
    path = workspace / spec.rel_path
    existing, load_err = _load_json(path)
    if load_err is not None:
        return ChangeReport(client, str(path), "error", detail=load_err)

    entry = _server_entry(command, spec.top_key)
    servers = existing.get(spec.top_key)
    if not isinstance(servers, dict):
        servers = {}

    if servers.get(SERVER_KEY) == entry:
        return ChangeReport(client, str(path), "unchanged")

    had_file = path.is_file()
    if dry_run:
        return ChangeReport(
            client, str(path), "updated" if had_file else "created",
            detail="dry-run: no file written",
        )

    updated = dict(existing)
    updated[spec.top_key] = {**servers, SERVER_KEY: entry}

    backup: str | None = None
    if had_file:
        backup_path = path.with_suffix(path.suffix + ".bak")
        try:
            shutil.copy2(path, backup_path)
            backup = str(backup_path)
        except OSError as exc:
            return ChangeReport(client, str(path), "error", detail=f"backup failed: {exc}")

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(updated, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        return ChangeReport(client, str(path), "error", detail=f"write failed: {exc}")

    return ChangeReport(
        client, str(path), "updated" if had_file else "created", backup=backup
    )


def codex_snippet(command: list[str]) -> str:
    """A copy-paste ``~/.codex/config.toml`` block (Codex uses TOML)."""
    args = ", ".join(json.dumps(a) for a in command[1:])
    return (
        "# add to ~/.codex/config.toml\n"
        f"[mcp_servers.{SERVER_KEY}]\n"
        f"command = {json.dumps(command[0])}\n"
        f"args = [{args}]\n"
    )


def claude_desktop_snippet(command: list[str]) -> str:
    """A copy-paste ``claude_desktop_config.json`` block."""
    block = {SERVER_KEY: {"command": command[0], "args": command[1:]}}
    return (
        "# add under \"mcpServers\" in claude_desktop_config.json\n"
        + json.dumps(block, indent=2)
    )


__all__ = [
    "CLIENTS",
    "SERVER_KEY",
    "ChangeReport",
    "ClientSpec",
    "apply_client",
    "claude_desktop_snippet",
    "codex_snippet",
    "resolve_server_command",
]
