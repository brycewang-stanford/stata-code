---
name: stata-code
description: Use this skill whenever the user asks to run Stata code, debug a `.do` file, work with a Stata-backed Jupyter notebook, repair a Stata error, or interpret `r()` / `e()` results — and the `stata-code` MCP server is available. The skill teaches Claude the v1.0 RunResult schema, the 15 MCP tools, token-economy defaults, and the typed-error repair loop so Stata work is done via structured tool calls instead of guessed log parsing.
---

# stata-code Skill

`stata-code` is an agent-native Stata bridge. This skill briefs Claude on how to drive Stata efficiently through it. **Do not regress to log-grepping; the schema is the contract.**

## 1. When this skill applies

Activate this skill whenever the user mentions Stata in a way that implies execution, inspection, or repair, e.g.:

- "Run this regression in Stata."
- "Why does `summarize mpgg` fail?"
- "Fix this do-file and rerun it until it passes."
- "Open `analysis.ipynb` cell 3 and replace it with a robust SE specification."
- "Pull `e(b)` and the residual variance after my last `regress`."
- "What does `r2_a` look like across these three specifications?"

Confirm the MCP server is wired up with `stata_info()` once per session. If it returns `available: false`, surface the install hint (`pip install "stata-code[mcp]"`) and stop — do not try to shell out to Stata directly.

## 2. The 15 MCP tools (cheat sheet)

| Tool | Use it when… |
|---|---|
| `stata_run(code, session_id?, …)` | The user wants Stata code executed. Default to `session_id="main"`. |
| `stata_info()` | At session start, or when capabilities / Stata edition matter. |
| `get_log(ref)` | A prior `stata_run` returned `log.truncated: true` and you need the full log. |
| `get_graph(ref, format?)` | The user wants graph bytes (export, display, embed). |
| `get_matrix(ref)` | A matrix in `results.r.matrices` or `results.e.matrices` came back with `values: null` (over 10k cells). |
| `list_sessions()` | The user mentions multiple parallel Stata "tabs" or you need to find a session by id. |
| `cancel_session(session_id)` | A run is hung or the user said "stop". Subprocess workers terminate; in-flight code is killed. |
| `reset_session(session_id?)` | The user wants `clear all`-style fresh state for a session. |
| `notebook_outline(path)` | The user references a `.ipynb` and you need to know which cells exist. |
| `notebook_get_cell(path, cell_id)` | Read one cell's source plus a compact outputs summary. |
| `notebook_locate(path, snippet/regex/error_text)` | Find which cell contains a given snippet or the cell that produced an error message. |
| `notebook_edit_cell(path, cell_id, source, expected_source?)` | Atomic cell replace. Pass `expected_source` for optimistic concurrency. |
| `notebook_insert_cell(path, after_cell_id, source, cell_type?)` | Insert a new cell with a fresh nbformat 4.5 uuid. |
| `notebook_delete_cell(path, cell_id)` | Remove a cell. |
| `list_runs(log_dir or origin_path, cell_id?, session_id?, ok?, since?, limit?, offset?)` | Search the on-disk run-bundle index — "show me my last failed run on this file". Use `offset` to page through long histories. |

There are also MCP resources (`stata://schema/run-result`, `log://...`, `graph://...`, `matrix://...`) and prompts (`run_do_file_and_report`, `debug_stata_error`, `fix_and_rerun_until_passes`, `replication_audit`, `summarize_estimation_results`).

## 3. The v1.0 RunResult schema (read this once)

Every `stata_run` reply has this shape (full spec: `stata://schema/run-result` or `SCHEMA.md` in the repo):

```jsonc
{
  "ok": true,                      // ← branch on this first
  "rc": 0,                         // Stata _rc; -1 adapter crash, -2 timeout, -3 cancelled
  "session_id": "main",
  "request_id": "01HX…",
  "started_at": "2026-…Z",
  "elapsed_ms": 234,
  "stata_elapsed_ms": 198,
  "stata": {"version": "18.0", "edition": "MP", "backend": "pystata"},

  "log": {
    "head": "...",                 // first 20 lines by default
    "tail": "...",                 // last 20 lines (empty when not truncated)
    "lines_total": 42,
    "bytes_total": 2380,
    "truncated": true,
    "error_window": null,          // ~10 lines around the failure on errors
    "ref": "log://run-7f3a9b"      // fetch full via get_log(ref)
  },

  "results": {
    "r": {"scalars": {…}, "macros": {…}, "matrices": {…}},
    "e": {"scalars": {…}, "macros": {…}, "matrices": {"b": {rows, cols, values, ref}, …}},
    "last_estimation_cmd": "regress"
  },

  "dataset": {"frame": "default", "n_obs": 74, "n_vars": 12, "changed": false, …},
  "graphs":   [{"ref": "graph://…", "format": "png", "source_command": "scatter …", "source_line": 5}],
  "warnings": [{"kind": "convergence", "message": "…"}],

  "error": null,                   // populated iff ok=false; see §5
  "origin": null,                  // echoes origin_* request fields
  "schema_version": "1.0",
  "capabilities": ["log_truncation", "graph_ref", "matrix_ref", "multi_session", …]
}
```

**Key invariants:**
- Branch on `ok` first; never grep `log.head` to decide success.
- Scalars are native numbers. Missing is JSON `null`, not `"."`.
- Matrices over ~10k cells arrive with `values: null` + a `matrix://` ref — call `get_matrix` lazily.
- Graphs default to refs, not base64 bytes. Only ask for `include_graphs: "inline"` if you genuinely need the bytes.

## 4. Token-economy defaults — keep responses small

`stata-code` is already aggressive about this; do not undo its work:

- **Do not** pass `include_full_log: true` unless the user asked for the full log or the head/tail clearly miss the relevant content.
- **Do not** pass `include_graphs: "inline"` unless the agent needs the bytes (rare; usually you want to surface the `ref` so the editor / user can fetch it).
- **Do not** read `get_log(ref)` proactively; only when you actually need lines beyond `head` + `tail` + `error_window`.
- **Do** quote specific numbers from `results.e.scalars` / `results.r.scalars` rather than dumping JSON.

## 5. The typed-error taxonomy (32 kinds)

On failure, the `error` block looks like:

```jsonc
{
  "kind": "varname_not_found",      // ← branch on this, not on rc or message
  "rc": 111,
  "rc_label": "variable not found",
  "message": "variable mpgg not found",
  "command": "summarize mpgg",
  "line": 3,
  "context": {"before": ["use auto"], "failing": "summarize mpgg", "after": []},
  "commands_executed": 1,
  "varname": "mpgg",               // populated for varname_* / file_* / name_* kinds
  "suggestions": [{"action": "Did you mean `mpg`?", "command": "describe"}]
}
```

Kinds you will see most often:

- `varname_not_found` (rc 111) — `varname` is filled; check `dataset.variables` for the right name.
- `syntax` (rc 9/100/198) — usually a typo; inspect `context.failing`.
- `command_not_found` (rc 199) — often needs `ssc install <pkg>` or `net install`.
- `file_not_found` / `file_exists` / `file_corrupt` (rc 322/601/602/604) — `path` is filled.
- `not_sorted` (rc 119) — append `sort <var>` before the failing command.
- `name_conflict` (rc 110) — use `replace` or pick a fresh name.
- `convergence` / `infeasible` (rc 430/491) — model issue, not code typo.
- `no_estimation_results` (rc 301) — likely `predict`/`margins` before any `regress`.
- `timeout` (synthetic rc -2) / `cancelled` (-3) / `adapter_crash` (-1) — system-level; do not retry blindly.

Use `error.suggestions` as hints, **not** directives. Apply a fix automatically only if the user explicitly asked you to repair and rerun.

## 6. The two big workflows

### 6.1 Diagnose-only (default)

```text
1. stata_run(code)
2. If ok: report scalars/warnings. Done.
3. If not ok:
   - State error.kind, error.line, error.context.failing.
   - List error.suggestions verbatim.
   - Ask the user how to proceed (do not edit source files).
```

### 6.2 Fix-and-rerun-until-passes (only when the user said so)

```text
loop:
  result = stata_run(current_code)
  if result.ok: break
  apply minimal fix derived from result.error (kind + varname/path/context):
    - varname_not_found → use error.varname / dataset.variables to suggest the closest match
    - file_not_found    → fix the path or generate the missing file
    - syntax            → fix the line in result.error.line
    - not_sorted        → prepend `sort <var>`
    - name_conflict     → add `replace` or drop the conflicting object first
  rewrite the .do file or notebook cell
  guard against infinite loops (cap at ~5 iterations; bail with a summary if stuck)
```

For notebook repair, use `notebook_edit_cell(path, cell_id, source, expected_source=<old>)` with optimistic-concurrency so a user-side edit aborts your write rather than silently overwriting it.

## 7. Multi-session etiquette

- Default session is `"main"`. Long analyses with conflicting state belong in named sessions (`session_id="model_a"` or `"model-a"`). Valid ids match `[A-Za-z0-9_-]+`; the backend maps ids that are not legal Stata frame names to private frames and still echoes the public id.
- The VS Code extension calls sessions "tabs". A new session lazily spawns or maps to a Stata frame; data does not cross.
- After heavy state changes, prefer `reset_session(session_id)` over rerunning the file with `clear all` — it is cheaper and clears refs.

## 8. Origin metadata (helpful for run-bundle audit)

When the user supplies a source file or notebook cell, pass:

- `origin_path`: absolute path of the `.do` / `.ipynb`
- `origin_kind`: `"file"`, `"selection"`, `"line"`, `"cell"`, `"section"`, `"code"`
- `origin_label`: `"analysis/main.do:42"` or similar
- `origin_cell_id`: nbformat 4.5 cell uuid when it's a notebook cell

The runner echoes these into `result.origin` and writes them to the on-disk run-bundle manifest. `list_runs` then finds prior runs by cell or by file.

## 9. Things to NOT do

- Do not shell out to `stata` / `do-file editor` / `pystata` directly. Use `stata_run`.
- Do not parse English from `log.head` to detect success — use `ok` / `rc` / `error.kind`.
- Do not retry a failing command unchanged. The taxonomy tells you why it failed; act on it or report it.
- Do not assume `e()` is populated after a non-estimation command. Check `results.last_estimation_cmd` first.
- Do not rewrite a `.do` file or `.ipynb` cell unless the user asked for repair. Diagnostics first.
- Do not paste graph base64 into chat unless the user explicitly asked for bytes; graphs go through `get_graph(ref)`.

## 10. Reference

- Full schema: `SCHEMA.md` in the repo or the MCP resource `stata://schema/run-result`.
- Server capabilities + tool list: MCP resource `stata://server/capabilities`.
- Examples (DiD, IV, graphs, multi-session, large matrices): `examples/` in the repo.
- License: MIT (`stata-code` itself); Stata is a registered trademark of StataCorp LLC.
