# Changelog

All notable changes to `stata-code` are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the project adheres
to semver-major.minor for the result schema (see `SCHEMA.md` §6).

## [Unreleased]

## [0.3.2] — 2026-05-08

### Changed

- **VSCode toolbar ordering.** Editor title-bar actions now live in one
  contiguous `navigation` group so `stata-code` buttons stay together. The
  order prioritizes run commands first, then data/output views, session
  controls, cancellation/reset, and working-directory actions.

## [0.3.1] — 2026-05-07

### Changed

- **VSCode extension polish.** Custom SVG toolbar icons (sessions / output /
  graphs / data / run / stop / reset / new-tab / switch-tab) replace the
  generic codicons; toolbar buttons render in the editor title bar with
  consistent visual weight. Adds `View Data Preview` command surfaced from
  the command palette and the editor right-click menu for opening the
  current `Last Result` dataset preview without re-running.
- **Run history retains origin URI and base line** so reruns from the
  Sessions / Last Result views replay the correct file/selection rather
  than re-resolving against whatever editor happens to be focused.
- **Marketplace publishing pipeline** (`.github/workflows/vscode-release.yml`).
  Tagging `v*` now packages `vscode/` into a VSIX and publishes via `vsce`
  using a stored `VSCE_PAT`. `vscode/.vscodeignore` tightened to exclude
  `.git/`, stale `.vsix` artifacts, and `.npmignore`. `vscode/LICENSE`
  vendored from the repo root so the VSIX ships its declared MIT license.
- **README docs.** English-first / Chinese-second bilingual layout, with
  expanded Claude Code MCP install instructions (`claude mcp add` patterns)
  and VS Code Marketplace install steps now that the extension is live.

### Fixed

- **mcpClient handshake** version string aligned with `package.json` so
  the VSCode client and MCP server announce matching versions.

## [0.3.0] — 2026-05-07

### Changed

- **PyPI distribution renamed to `stata-code`.** Previously published as
  `stata_code`. Install with `pip install stata-code` going forward; the
  Python import name remains `stata_code` (Python identifier rules — same
  pattern as `scikit-learn` → `import sklearn`). Existing users on
  `pip install stata_code` will keep working until that PyPI project
  stops receiving new versions, but should migrate.
- **Project URLs in `pyproject.toml` corrected** to
  `github.com/brycewang-stanford/stata-code` (the actual repository
  URL — the previous metadata had `stata_code`).
- **MCP server announces itself as `stata-code`** (was `stata_code`).
  This is the protocol-level server name; tool ids
  (`stata_run`, `get_log`, etc.) are unchanged.
- **VSCode extension display name unified to `stata-code`** in the
  Marketplace, activity-bar tile, command-palette `category`, output
  channel, all toast messages, and webview title. Code identifiers
  (`stataCode.*` command / view / setting ids; npm `name`
  `stata-code-vscode`) are unchanged so existing keybindings keep
  working.
- **Version aligned across surfaces.** `pyproject.toml`,
  `stata_code/__init__.py`, `stata_code/mcp/server.py`,
  `vscode/package.json`, and the VSCode MCP-client handshake all
  declare `0.3.0`.

### Added

- **VSCode extension v0.3 — full UI surface** (`vscode/`). Beyond the
  v0.1 "run from command palette" scaffold, the extension now ships
  every common GUI affordance, so users who don't drive Stata through
  Claude Code / Cursor can still operate the same MCP server from the
  editor:
  - **Editor title-bar ▶ button** (`editor/title/run` menu) and
    editor right-click menu entries (`Run Selection` / `Run Active File`).
  - **Status bar item** showing the current session; click for a
    QuickPick (`Switch session…` / `Cancel` / `Reset`). The icon
    swaps to a spinner during runs and the run progress notification
    now has a Cancel button (cooperative cancellation through the
    MCP `cancel_session` tool).
  - **Activity-bar sidebar** with four views: live `Sessions` (with
    inline Cancel/Reset/Close per item — `main` is non-closable;
    locally-known but not-yet-started sessions persist via
    `workspaceState`), `Last Result` (collapsible
    `r()` / `e()` / warnings / dataset / log / graphs), `Graphs`
    history (click-to-open + per-item Save…), and `Logs`
    history (click-to-open + per-item Save…). Section-header buttons
    for Clear (logs / graphs) and New / Refresh (sessions).
  - **Inline error decorations.** Failed runs now publish a
    `DiagnosticCollection` entry on the failing file/line, complete
    with the typed error message, failing snippet, and any
    suggestions surfaced in `runResult.error.suggestions`. Hover
    shows the full text; the Problems panel lists the entry under
    `source: stata-code, code: <error.kind>`.
  - **Code-lens "Run Cell" support.** Lines starting with `* %%`
    get an inline `▶ Run Cell` lens; clicking submits the code
    between markers. Cell ranges map back to the original file
    lines so error squigglies still anchor correctly.
  - **Graph webview action buttons.** The webview now uses a strict
    nonce-based CSP and exposes `Save as…`, `Open externally`, and
    `Refresh` per-graph and panel-level buttons. PNG/SVG/PDF bytes
    still flow lazily through `get_graph(ref)`.
  - Bumped the extension version to `0.2.0`.

- **Matrix size cap + `get_matrix(ref)`.** Matrices larger than
  `MATRIX_INLINE_CELL_CAP` (default 10,000 cells) now drop their
  `values` from the envelope and surface a `matrix://<request_id>/<r|e>/
  <name>` ref instead. Callers fetch the values via `get_matrix(ref)`,
  which mirrors the existing `get_log` / `get_graph` pattern. The MCP
  server gains a seventh tool, `get_matrix`, returning JSON
  `{rows, cols, values}`. Closes the last open §3.4 todo from
  SCHEMA.md and prevents pathological commands (e.g., `correlate` over
  hundreds of variables) from blowing up the result envelope.

- **VSCode extension scaffold** (`vscode/`). TypeScript extension that
  spawns `stata-code-mcp` over stdio and registers four commands
  (`Run Selection`, `Run Active File`, `Show Graphs`, `Show Last
  Result`). Hand-rolled TypeScript types in
  `vscode/src/types/runResult.ts` mirror the Pydantic envelope;
  `npm run gen-types` regenerates a full copy from
  `schema/run_result.schema.json` for cross-checking. Source-only —
  build with `npm install && npm run compile`.

- **VSCode graph webview** (`vscode/src/graphPanel.ts`). Successful
  runs that capture graphs auto-open a side-by-side webview that
  renders PNG / SVG / PDF inline. The webview lazily fetches each
  graph's bytes via `get_graph(ref)` rather than embedding them in
  the original `RunResult`, so token economy is preserved end-to-end
  (an agent driving the same MCP server pays nothing extra for
  inlining). Strict CSP (`default-src 'none'`, no scripts).
  Marketplace publishing still deferred.

- **`stata_required` pytest marker.** Integration tests against a
  real Stata installation are now tagged with the marker; CI runs
  `pytest -m "not stata_required"`, completing in ~1.5s instead of
  ~19s. Local without Stata, the same tests still skip cleanly.

- **Cooperative cancellation.** New `cancel(session_id)` /
  `clear_cancel(session_id)` / `is_cancel_pending(session_id)` Python
  API plus the MCP `cancel_session` tool (eighth tool). A pending
  cancel short-circuits the next `execute()` call for that session
  and returns a `RunResult` with `ok=false`, `rc=-3` (synthetic),
  `error.kind="cancelled"`. The flag is one-shot per cancel, isolated
  per session, and thread-safe. Note: this is *cooperative* — it does
  not interrupt code that is currently mid-`stata.run()` (pystata is
  in-process and has no clean cancel primitive). Hard interruption
  remains deferred to the subprocess-based runtime planned for v0.3+.

### Changed

- **MCP server tool count is now 8** (added `get_matrix`,
  `cancel_session`).

## [0.2.0] — 2026-05-07

The first release that actually ships an end-to-end Stata pipeline. The v1.0
result schema is the load-bearing artifact; everything below is implemented
against it and end-to-end-tested on Stata 18 MP.

### Added

- **`SCHEMA.md` v1.0** — normative result-envelope contract: `ok` / `rc`,
  typed `error` (32 `kind` values), structured `r()` / `e()` (scalars,
  macros, matrices), `dataset` snapshot with variable list, log
  head+tail+ref, graph refs with PNG/SVG/PDF support, multi-session id,
  forward-compat clauses.
- **`stata_code.run()`** (= `execute()`) — the real-Stata pipeline. Uses
  pystata in-process; collects native-typed return values via `sfi`;
  builds a `RunResult` end to end.
- **`get_log` / `get_graph` / `list_sessions` / `reset_session`** —
  auxiliary tools per `SCHEMA.md` §5.
- **MCP server** (`stata_code.mcp.server`) — six tools: `stata_run`,
  `stata_info`, `get_log`, `get_graph`, `list_sessions`,
  `reset_session`. Console script: `stata-code-mcp`. Module entry:
  `python -m stata_code.mcp`.
- **Jupyter kernel** (`stata_code.kernel`) rewired to the v1.0 pipeline.
  Defaults tuned for notebooks (`include_full_log=True`,
  `include_graphs="inline"`). Console script: `stata-code-kernel`.
  Module entry: `python -m stata_code.kernel`.
- **Multi-session via Stata frames**. `session_id="main"` maps to the
  default frame; other ids create/route to same-named frames.
- **Per-line error attribution** — `error.line`, `commands_executed`,
  and `context.{before, failing, after}` are populated by parsing
  pystata's multi-line transcript.
- **Warning extraction** — five built-in patterns
  (`omitted_collinear`, `convergence`, `singular`, `boundary`, generic
  `note`) + dedup.
- **Graph capture pipeline** — `graph dir` snapshot delta + `graph
  display` + `graph export`; PNG `width`/`height` parsed from IHDR;
  bytes stored under a `_refs` LRU.
- **`_refs` LRU eviction** — bounded ref store (default 256 entries)
  to keep long-running MCP processes from growing unboundedly.
- **`LICENSE-POLICY.md`** — clean-room policy that forbids opening
  AGPL/GPL Stata project source.
- **138 tests** covering schema, runner integration, MCP, kernel,
  `_refs`, and error helpers. Real-Stata tests run against Stata 18 MP
  when available.

### Changed

- Top-level `stata_code.run()` now returns the new `RunResult` (Pydantic
  v2). The legacy `StataResult` dataclass and the `capture_graphs`/
  `capture_log`/`timeout` keyword arguments are gone.
- Wheel build now ships **all** of `stata_code` (`core`, `mcp`,
  `kernel`). Previously the wheel only contained `core`.

### Removed

- **Legacy modules** — `core/pystata_adapter.py`, `core/console_fallback.py`,
  `core/result.py`, `core/version.py`. Their behavior is now provided by
  `core/runner.py`, `core/_runtime.py`, `core/schema.py`.
- **Legacy tests** — `tests/test_result.py`, `tests/test_version.py`,
  `tests/test_integration.py`. Coverage moved to `tests/test_runner.py`,
  `tests/test_schema.py`, and `tests/test_mcp.py`.

### Migration notes

| Before (v0.1) | After (v0.2) |
| --- | --- |
| `from stata_code import run` returns `StataResult` | Returns `RunResult` |
| `result.log` (string) | `result.log.head` / `result.log.tail` (and `get_log(ref)` for full) |
| `result.results["r(mean)"]` | `result.results.r.scalars["mean"]` (native float) |
| `result.error` (string) | `result.error.kind` (typed) + `result.error.message` |
| `result.graphs[0].data` (bytes) | `result.graphs[0].ref` + `get_graph(ref)` |
| `run(code, capture_graphs=True)` | `run(code, include_graphs="ref" \| "inline" \| "none")` |
| `run(code, timeout=120)` | `run(code, timeout_ms=120_000)` |

`pystata` is no longer declared as a runtime dependency in
`pyproject.toml` — it is sourced from your local Stata install per the
documented `_runtime` discovery path.

## [0.1.0] — 2026-04

Initial scaffolding. `pystata_adapter`, `console_fallback`, basic kernel
and MCP server, `References-tools.md` survey, project vision in
`README.md`. Largely superseded by 0.2.
