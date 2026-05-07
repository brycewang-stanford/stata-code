# stata_code — VSCode extension (scaffold)

Run Stata code from VSCode through the agent-native [stata_code](https://github.com/brycewang-stanford/stata_code) MCP server.

> **Status: scaffold (v0.1).** Compiles to a working extension once
> dependencies are installed, but has not yet been published to the
> VSCode Marketplace. Intended for development use against a local
> `stata-code-mcp` install.

## What it does

The extension is a thin VSCode transport in front of the same
`stata-code-mcp` server that Claude Code / Cursor use. It owns no
Stata logic; everything goes through the MCP `stata_run`, `get_log`,
`get_graph`, `get_matrix`, `list_sessions`, `reset_session` tools.

Three commands are registered:

| Command | Default keybinding | Purpose |
| --- | --- | --- |
| `Stata: Run Selection` | `Cmd/Ctrl+Enter` | Run the selection (or current line) |
| `Stata: Run Active File` | — | Run the entire file |
| `Stata: Show Last Result (JSON)` | — | Open the last `RunResult` envelope as JSON |

## Setup

This directory ships **source only**. To build a working extension:

```bash
cd vscode
npm install
npm run compile
```

To launch the extension host for local development:

1. Open this `vscode/` folder in VSCode.
2. `F5` to start an Extension Development Host.
3. In the new window, open a `.do` file and `Cmd+Enter` on a line.

The first command invocation spawns `stata-code-mcp` lazily (one
process per workspace). Make sure `stata-code-mcp` is on your `PATH`
or override:

```jsonc
// settings.json
{
  "stataCode.serverCommand": "/abs/path/to/python -m stata_code.mcp"
}
```

## Configuration

| Key | Default | Purpose |
| --- | --- | --- |
| `stataCode.serverCommand` | `stata-code-mcp` | Command to spawn the MCP server |
| `stataCode.serverArgs` | `[]` | Extra args passed to the server process |
| `stataCode.sessionId` | `"main"` | Session id passed to `stata_run` |
| `stataCode.includeFullLog` | `false` | Inline full log instead of fetching via `get_log(ref)` |

## TypeScript types

`src/types/runResult.ts` is the **hand-rolled** subset the extension
consumes. The full machine-readable schema lives at
`../schema/run_result.schema.json` (generated from the Pydantic model
by `python scripts/export_schema.py` in the repo root). To regenerate
the full TypeScript types side-by-side as `src/types/runResult.generated.ts`:

```bash
npm run gen-types
```

Diff the two and pull in any newly-added fields by hand.

## Architecture

```
VSCode editor
   │
   ▼  (text/code via callTool)
StataMcpClient (src/mcpClient.ts)
   │
   ▼  (stdio JSON-RPC, MCP)
stata-code-mcp (Python subprocess)
   │
   ▼  (in-process)
pystata → Stata 17+
```

## Status

This is the v0.4-roadmap scaffold from the parent project's
[README](../README.md#roadmap). It deliberately stops at:

- The `Stata: Run Selection` command (line / selection / file modes)
- Output-channel rendering of `RunResult` (logs, errors, warnings, graph counts)
- Last-result JSON viewer

Not yet wired:

- Webview rendering of graphs (would call `get_graph` and decode base64)
- Matrix / dataset table views
- Inline error decorations on failing lines
- Marketplace publishing

## License

MIT, same as the parent project.
