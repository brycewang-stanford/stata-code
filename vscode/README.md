# stata-code — VSCode extension

Run Stata code from VSCode through the agent-native [stata-code](https://github.com/brycewang-stanford/stata-code) MCP server.

> **Status: v0.3 (May 2026).** Full UI surface — orange Stata title-bar
> buttons, editor context menu, status bar with session/cancel actions, activity-bar
> sidebar with sessions / last result / run history / logs / graphs panels,
> inline error squigglies, code-lens cells, graph webview Save/Open actions,
> run-bundle export, and Stata working-directory helpers. Source-only —
> build with `npm install && npm run compile`. Marketplace publishing still
> pending.

## What it does

A thin VSCode transport in front of the same `stata-code-mcp` server
that Claude Code / Cursor use. The extension owns no Stata logic;
everything goes through MCP `stata_run`, `get_log`, `get_graph`,
`get_matrix`, `list_sessions`, `cancel_session`, `reset_session`.

## UI surface

### Editor

| Affordance | What it does |
| --- | --- |
| Orange toolbar buttons in editor title bar | Run selection/current line, view data preview, stop, new tab, switch tab, restart, show output, and working-directory actions |
| Run button in editor title bar | Run all / active file |
| Right-click menu → *Stata: Run Selection / Run Active File / View Data Preview* | Same, from the editor |
| Right-click menu → *Stata: Working Directory...* | Show or change Stata's current directory |
| `Cmd/Ctrl+Enter` | Run selection (or current line if no selection) |
| Inline `▶ Run Cell` code-lens above any `* %%` line | Run that cell |
| Red squiggle on the failing line | After a failed run; hover to see the typed-error message and suggestions |
| *Stata: View Data Preview* | Opens the first 100 observations from the active session as a side text document, plus dataset metadata and variables |

**Cell convention.** A line whose trimmed text is `* %%` (optionally
followed by a title, e.g. `* %% setup`) marks the start of a cell. The
marker line is a Stata comment, so plain Stata still runs the file
unchanged. The cell ends at the next marker or EOF. This is the Stata
analog of Python's `# %%` cells.

### Status bar

A left-aligned `$(database) Stata: <session>` chip is always visible
once the extension activates. Click it for a QuickPick with compact
actions:

- New Stata tab… / Switch tab… (live sessions + locally-known
  "not started" tabs, plus *New tab…*)
- View data preview
- Export latest run / Open latest log / Show latest graphs
- Working directory… / Show output channel
- Cancel `<sid>` / Reset `<sid>` / Close tab `<sid>`

While a run is in flight, the icon swaps to `$(sync~spin)` and the
notification toast has a Cancel button (cooperative cancellation via
the MCP `cancel_session` tool).

### Activity bar sidebar

A new entry on the activity bar opens a sidebar with five sections:

- **Sessions** — live `list_sessions` view with the current session
  highlighted; header buttons for New / Refresh and per-item actions for
  Switch / Cancel / Reset / Close. Sessions you've used in this workspace
  but that aren't currently live still appear as "not started".
- **Last Result** — collapsible groups for `r()` / `e()` returns,
  matrices, warnings, dataset summary (with variable list), a clickable
  `log` leaf (opens the run's full log via `get_log(ref)`), a clickable
  `graphs` leaf, and on failure a typed error block with the failing line
  and suggestions. Matrix entries open on demand via `get_matrix(ref)` as
  TSV text.
- **Run History** — recent runs (capped at 64). Each item can rerun the
  exact submitted code, copy the code, open the JSON result, or export a
  reproducible run bundle.
- **Logs** — recent runs (capped at 64) tagged with success/failure and
  line count; click to open the full log in an editor pane, or save it
  from the item context menu.
- **Graphs** — every captured graph in reverse-chronological order
  (capped at 64); click any item to open it in the graph webview, or
  save it directly from the item context menu.

### Run bundles

Any run in **Run History** can be exported to a folder:

```text
stata-run-20260507T213000Z-abc123/
  code.do
  log.txt
  result.json
  manifest.json
  graphs/
    01-scatter.png
```

This is intentionally simple: it captures exactly what was submitted and
what came back, without turning the extension into a notebook system.

### Working directory

`Stata: Working Directory...` offers four focused actions:

- show current Stata `pwd`
- `cd` to the workspace folder
- `cd` to the current file's folder
- choose a folder with VS Code's native picker

These utility commands run in the active Stata session but do not add
noise to Run History / Logs / Graphs.

### Graph webview

Successful runs with one or more graphs auto-open a side webview that
fetches each graph's bytes via `get_graph(ref)` lazily. v0.2 adds:

- Per-graph **Save as…** button → native save dialog → writes PNG / SVG / PDF
- Per-graph **Open externally** button → writes to OS temp + opens with the
  default app
- Per-graph collapsible result block for hiding old figures while comparing
- Top-level **Refresh** button → re-fetches the current panel

The webview uses a strict CSP with a per-render nonce. Graph bytes
never leave the local pystata / MCP boundary.

## Setup

```bash
cd vscode
npm install
npm run compile
```

For local development, open this `vscode/` folder in VSCode and `F5` to
launch an Extension Development Host. The first command invocation
spawns `stata-code-mcp` lazily (one process per workspace). Make sure
`stata-code-mcp` is on `PATH` or override:

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
| `stataCode.sessionId` | `"main"` | Session id passed to `stata_run` (also driven by the status bar's *Switch session…*) |
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

```text
VSCode editor / sidebar / status bar / webview
   │
   ▼  (high-level callTool)
StataMcpClient (src/mcpClient.ts)
   │
   ▼  (stdio JSON-RPC, MCP)
stata-code-mcp (Python subprocess)
   │
   ▼  (in-process)
pystata → Stata 17+
```

UI modules:

| File | Responsibility |
| --- | --- |
| `src/extension.ts` | activation, command registration, run pipeline |
| `src/mcpClient.ts` | MCP transport (stdio child process) |
| `src/statusBar.ts` | status bar item + running-state spinner |
| `src/treeProviders.ts` | sessions / last-result / run-history / logs / graphs sidebar trees |
| `src/cellLens.ts` | `* %%` code-lens provider |
| `src/diagnostics.ts` | inline error squigglies |
| `src/graphPanel.ts` | graph webview (Save / Open / Refresh) |

## Status / not yet shipped

- Marketplace publishing
- `.do` notebook editor (kept deliberately minimal in favor of code-lens cells)
- Full paged/filterable dataset table view (the current button is a first-100-row preview)
- Rich matrix formatting beyond TSV
- Extension-host tests (planned)

## License

MIT, same as the parent project.
