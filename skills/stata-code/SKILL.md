---
name: stata-code
description: Use this skill whenever the user asks to run Stata code, debug a `.do` file, work with a Stata-backed Jupyter notebook, repair a Stata error, interpret `r()` / `e()` results, or write/plan a Stata analysis — and the `stata-code` MCP server is available (or, when it is not, to generate self-contained do-files). The skill teaches Claude the v1.0 RunResult schema, the 18 MCP tools, token-economy defaults, the typed-error repair loop, and a routing table into an on-demand Stata reference library (syntax, data management, econometrics, causal inference, panel/time series, graphics, tables, error codes, defensive coding, and key packages).
---

# stata-code Skill

`stata-code` is an agent-native Stata bridge. This skill briefs Claude on how to drive Stata efficiently through it. **Do not regress to log-grepping; the schema is the contract.**

## 1. When this skill applies

Activate this skill whenever the user mentions Stata in a way that implies execution, inspection, repair, or authoring, e.g.:

- "Run this regression in Stata."
- "Why does `summarize mpgg` fail?"
- "Fix this do-file and rerun it until it passes."
- "Open `analysis.ipynb` cell 3 and replace it with a robust SE specification."
- "Pull `e(b)` and the residual variance after my last `regress`."
- "Write a staggered DiD / event-study in Stata."

It also applies on context carry-over: if earlier turns were about Stata, keep the skill active even when a later message doesn't say "Stata".

Confirm the MCP server is wired up with `stata_info()` once per session — this also selects your execution mode (§2).

## 2. Execution mode: live vs offline

**Live (MCP) mode — default.** `stata_info()` returns `available: true`. Execute code with `stata_run`, read the structured result, and repair using typed errors (§7–8). This is the normal path; everything below assumes it.

**Offline (code-gen) mode.** No stata-code server, or `stata_info()` returns `available: false`. You cannot execute Stata. Do **not** pretend to. Instead:

- Surface the install hint once (`pip install "stata-code[mcp]"`) if the user expected execution.
- Produce a **complete, self-contained `.do` file**: start with `version 18`, load the data, `set seed` before any randomness, comment each block, and bake in defensive guards (see `references/defensive-coding.md`).
- Present the code plus *expected* outputs, clearly labeled as not-yet-run.

## 3. Reference library — read on demand

The skill ships a `references/` library of dense Stata domain knowledge. **Progressive disclosure: read at most 1–3 files relevant to the current task — never preload them all.** The schema and tool sections below are always in effect regardless of which references you open.

| If the task is about… | Read |
|---|---|
| Core syntax, macros, missing values, loops, factor/time-series operators | `references/syntax-core.md` |
| Loading, cleaning, merging, reshaping, labeling data | `references/data-management.md` |
| Regression, GLM, IV mechanics, fixed effects, postestimation, margins | `references/econometrics.md` |
| Causal designs — DiD, event study, RDD, matching/weighting, synthetic control | `references/causal-inference.md` |
| Panel data / time series — `xtset`, `tsset`, `xtreg`, dynamic panels, `arima` | `references/panel-timeseries.md` |
| Plots and visualization | `references/graphics.md` |
| Regression/summary tables, LaTeX/Word/Excel/Markdown export | `references/tables-export.md` |
| Choosing public data sources or documenting source provenance | `references/data-sources.md` |
| Data fetched by external MCPs should become Stata inputs | `references/data-mcp-handoff.md` |
| Cross-package or cross-language robustness / parity checks | `references/parity-audit.md` |
| Turnkey empirical recipes — DiD/event study, IV/2SLS, RDD, publication tables, cross-validation | `references/recipes/<recipe>.md` |
| Interpreting `results.estimation` (coefficient table, diagnostics), `error.recovery`, reproducible-do / submission bundles, `verify_dataset` | `references/structured-results.md` |
| Diagnosing a failed run, Stata `rc` codes, the self-repair loop | `references/error-codes.md` |
| Writing correct, reproducible Stata that fails loudly (not silently) | `references/defensive-coding.md` |
| A specific community package | `references/packages/<pkg>.md` — `reghdfe`, `csdid`, `drdid`, `did_imputation`, `eventstudyinteract`, `did_multiplegt_dyn`, `rdrobust`, `ivreg2`, `ivreghdfe`, `boottest`, `estout`, `outreg2`, `coefplot`, `gtools` |

Routing examples: "panel regression with clustered SEs" → `econometrics.md` (+ `panel-timeseries.md`); "my merge gives wrong N" → `defensive-coding.md`; "command not found: reghdfe" → `error-codes.md` + `packages/reghdfe.md`; "make a publication table" → `tables-export.md` + `recipes/publication-tables.md`; "compare Stata csdid against R did" → `parity-audit.md` + `packages/csdid.md`; "OECD MCP pulled a CSV; now analyze it in Stata" → `data-mcp-handoff.md`; "run the full DiD/event-study workflow" → `recipes/did-event-study.md`.

## 4. The 18 MCP tools (cheat sheet)

| Tool | Use it when… |
|---|---|
| `stata_run(code, session_id?, …)` | The user wants Stata code executed. Default to `session_id="main"`. |
| `stata_info()` | At session start (also picks live vs offline mode), or when capabilities / Stata edition matter. |
| `get_log(ref)` | A prior `stata_run` returned `log.truncated: true` and you need the full log. |
| `search_log(ref, pattern, is_regex?, ignore_case?, context?, max_matches?)` | You need only specific lines from a truncated `log://` ref — grep it instead of pulling the whole log back with `get_log`. |
| `get_graph(ref, format?)` | The user wants graph bytes (export, display, embed). |
| `get_matrix(ref)` | A matrix in `results.r.matrices` / `results.e.matrices` came back with `values: null` (over 10k cells). |
| `inspect_data(varlist?, detail?, session_id?)` | "What's in this dataset?" Runs `describe` + `codebook`; returns the structured `dataset` block plus the codebook log. |
| `install_package(name, source?, url?, replace?, session_id?)` | A run failed with `command_not_found` (rc 199) for a community package, or the user asks to install one. Builds `ssc`/`net install`, then verifies with `which`. |
| `list_sessions()` | The user mentions multiple parallel Stata "tabs", or you need to find a session by id. |
| `cancel_session(session_id)` | A run is hung or the user said "stop". Subprocess workers terminate; in-flight code is killed. |
| `reset_session(session_id?)` | The user wants `clear all`-style fresh state for a session. |
| `notebook_outline(path)` | The user references a `.ipynb` and you need to know which cells exist. |
| `notebook_get_cell(path, cell_id)` | Read one cell's source plus a compact outputs summary. |
| `notebook_locate(path, snippet/regex/error_text)` | Find which cell contains a snippet or produced an error message. |
| `notebook_edit_cell(path, cell_id, new_source, expected_source?)` | Atomic cell replace. Pass `expected_source` for optimistic concurrency. |
| `notebook_insert_cell(path, source, after_cell_id?, before_cell_id?, at_start?, at_end?, cell_type?)` | Insert a new cell with a fresh nbformat 4.5 uuid. |
| `notebook_delete_cell(path, cell_id, expected_source?)` | Remove a cell. Pass `expected_source` when guarding against drift. |
| `list_runs(log_dir or origin_path, …)` | Search the on-disk run-bundle index — "show me my last failed run on this file". |

There are also MCP resources (`stata://schema/run-result`, `log://...`, `graph://...`, `matrix://...`) and prompts (`run_do_file_and_report`, `debug_stata_error`, `fix_and_rerun_until_passes`, `replication_audit`, `summarize_estimation_results`, `run_notebook_cell_and_report`, `fix_and_rerun_notebook_cell`, `plan_cross_stack_parity_audit`, `data_mcp_to_stata_handoff`, `did_event_study`, `iv_2sls`, `rdd`, `publication_table`, `cross_validate_did`).

## 5. The v1.0 RunResult schema (read this once)

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
    "ref": "log://run-7f3a9b"      // fetch full via get_log(ref) / grep via search_log(ref)
  },

  "results": {
    "r": {"scalars": {…}, "macros": {…}, "matrices": {…}},
    "e": {"scalars": {…}, "macros": {…}, "matrices": {"b": {rows, cols, values, ref}, …}},
    "last_estimation_cmd": "regress"
  },

  "dataset": {"frame": "default", "n_obs": 74, "n_vars": 12, "changed": false, …},
  "graphs":   [{"ref": "graph://…", "format": "png", "source_command": "scatter …", "source_line": 5}],
  "warnings": [{"kind": "convergence", "message": "…"}],

  "error": null,                   // populated iff ok=false; see §7
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

## 6. Token-economy defaults — keep responses small

`stata-code` is already aggressive about this; do not undo its work:

- **Do not** pass `include_full_log: true` unless the user asked for the full log or the head/tail clearly miss the relevant content.
- **Do not** pass `include_graphs: "inline"` unless the agent needs the bytes (rare; usually surface the `ref`).
- **Do not** read `get_log(ref)` proactively; prefer `search_log(ref, pattern)` to pull just the lines you need, and only fall back to `get_log` for the full transcript.
- **Do** quote specific numbers from `results.e.scalars` / `results.r.scalars` rather than dumping JSON.

## 7. The typed-error taxonomy (32 kinds)

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
- `command_not_found` (rc 199) — often a community package: `install_package(name=...)`.
- `file_not_found` / `file_exists` / `file_corrupt` (rc 322/601/602/604) — `path` is filled.
- `not_sorted` (rc 119) — prepend `sort <var>`.
- `name_conflict` (rc 110) — use `replace` or pick a fresh name.
- `convergence` / `infeasible` (rc 430/491) — model issue, not a typo; do not loop on it.
- `no_estimation_results` (rc 301) — likely `predict`/`margins` before any `regress`.
- `timeout` (−2) / `cancelled` (−3) / `adapter_crash` (−1) — system-level; do not retry blindly.

**The full rc → kind → fix table and the self-repair algorithm live in `references/error-codes.md`.** Read it whenever you hit a non-trivial failure. Use `error.suggestions` as hints, **not** directives — apply a fix automatically only if the user asked you to repair and rerun.

## 8. The two big workflows

### 8.1 Diagnose-only (default)

```text
1. stata_run(code)
2. If ok: report scalars/warnings. Done.
3. If not ok:
   - State error.kind, error.line, error.context.failing.
   - List error.suggestions verbatim.
   - Ask the user how to proceed (do not edit source files).
```

### 8.2 Fix-and-rerun-until-passes (only when the user said so)

Drive the loop from `error.kind` (full version in `references/error-codes.md`):

```text
loop (cap ~5 iterations):
  result = stata_run(current_code)
  if result.ok: break
  switch result.error.kind:
    command_not_found    → if community pkg: install_package(name); else fix spelling
    varname_not_found    → closest match from error.varname / dataset.variables
    syntax               → fix the line at error.line
    not_sorted           → prepend `sort <var>`
    name_conflict        → add `replace` or drop the conflicting object first
    file_not_found       → fix error.path or generate the missing file
    convergence/infeasible/estimation_failure → MODEL issue: respecify, do NOT loop
    adapter_crash/timeout/cancelled → STOP and surface to the user
  rewrite the .do file or notebook cell; re-run
  if the same kind+line repeats unchanged twice → STOP with a summary
```

For notebook repair, use `notebook_edit_cell(path, cell_id, new_source, expected_source=<old>)` with optimistic concurrency so a user-side edit aborts your write rather than silently overwriting it.

## 9. Multi-session etiquette

- Default session is `"main"`. Long analyses with conflicting state belong in named sessions (`session_id="model_a"`). Valid ids match `[A-Za-z0-9_-]+`; ids that are not legal Stata frame names are mapped to private frames and still echo the public id.
- The VS Code extension calls sessions "tabs". A new session lazily spawns or maps to a Stata frame; data does not cross.
- After heavy state changes, prefer `reset_session(session_id)` over rerunning with `clear all` — it is cheaper and clears refs.

## 10. Origin metadata (helpful for run-bundle audit)

When the user supplies a source file or notebook cell, pass:

- `origin_path`: absolute path of the `.do` / `.ipynb`
- `origin_kind`: `"file"`, `"selection"`, `"line"`, `"cell"`, `"section"`, `"code"`
- `origin_label`: `"analysis/main.do:42"` or similar
- `origin_cell_id`: nbformat 4.5 cell uuid when it's a notebook cell

The runner echoes these into `result.origin` and writes them to the run-bundle manifest. `list_runs` then finds prior runs by cell or by file.

## 11. Things to NOT do

- Do not shell out to `stata` / `do-file editor` / `pystata` directly. Use `stata_run`.
- Do not parse English from `log.head` to detect success — use `ok` / `rc` / `error.kind`.
- Do not retry a failing command unchanged. The taxonomy tells you why it failed; act on it or report it.
- Do not loop the fix-and-rerun routine on a model problem (`convergence`, `infeasible`).
- Do not assume `e()` is populated after a non-estimation command. Check `results.last_estimation_cmd` first.
- Do not rewrite a `.do` file or `.ipynb` cell unless the user asked for repair. Diagnostics first.
- Do not paste graph base64 into chat unless the user explicitly asked for bytes; graphs go through `get_graph(ref)`.
- Do not preload the whole `references/` library — open only the 1–3 files the task needs.

## 12. Reference

- Domain knowledge: the `references/` library (routing table in §3).
- Full schema: `SCHEMA.md` in the repo or the MCP resource `stata://schema/run-result`.
- Server capabilities + tool list: MCP resource `stata://server/capabilities`.
- Examples (DiD, IV, graphs, multi-session, large matrices, parity audits, data-MCP handoff): `examples/` in the repo.
- License: MIT (`stata-code` itself); Stata is a registered trademark of StataCorp LLC.
