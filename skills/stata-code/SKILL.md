---
name: stata-code
description: Use this skill whenever the user asks to run Stata code, debug a `.do` file, work with a Stata-backed Jupyter notebook, repair a Stata error, interpret `r()` / `e()` results, or write/plan a Stata analysis — and the `stata-code` MCP server is available (or, when it is not, to generate self-contained do-files). The skill teaches Claude the v1.0 RunResult schema, the 21 MCP tools, token-economy defaults, the typed-error repair loop, and a routing table into an on-demand Stata reference library (syntax, data management, econometrics, causal inference, panel/time series, graphics, tables, error codes, defensive coding, and key packages).
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

## 4. The 21 MCP tools (cheat sheet)

| Tool | Use it when… |
|---|---|
| `stata_run(code, session_id?, …)` | The user wants Stata code executed. Default to `session_id="main"`. See §4.1 for the payload/timeout knobs. |
| `stata_run_status(job_id, wait_ms?)` | Poll a run you submitted with `run_in_background: true`. `wait_ms` blocks up to 60 s — far cheaper than a tight poll loop. |
| `list_background_runs()` | See which background runs are still going. Summaries only; fetch a result with `stata_run_status`. |
| `stata_info()` | At session start (also picks live vs offline mode), or when capabilities / Stata edition matter. |
| `get_log(ref)` | A prior `stata_run` returned `log.truncated: true` and you need the full log. |
| `search_log(ref, pattern, is_regex?, ignore_case?, context?, max_matches?)` | You need only specific lines from a truncated `log://` ref — grep it instead of pulling the whole log back with `get_log`. |
| `get_graph(ref, format?)` | The user wants graph bytes (export, display, embed). |
| `get_matrix(ref)` | A matrix in `results.r.matrices` / `results.e.matrices` came back with `values: null`. By default *every* matrix is a stub, so this is the normal way to get raw `e(V)` / `e(b)` numbers. |
| `inspect_data(varlist?, detail?, session_id?)` | "What's in this dataset?" Runs `describe` + `codebook`; returns the structured `dataset` block plus the codebook log. |
| `lint_do(code? / path?)` | Before running a long or generated do-file, statically check it (unbalanced braces, missing `end`, dangling `///`). Cheap, Stata-free; catches structural mistakes without spending a run. Advisory — a clean result is not a guarantee. |
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

### 4.1 `stata_run` options worth knowing

Defaults are already tuned for you; reach for these only when the situation calls for it.

| Option | Default | Reach for it when… |
|---|---|---|
| `include_results` | `"scalars"` | You genuinely need raw matrix numbers inline: `"full"`. Under the default, scalars and macros are inline and every matrix is a `matrix://` stub with its shape — because one estimation otherwise ships the same numbers four times (`e(b)`, `e(V)`, `e(beta)`, `r(table)`) on top of `results.estimation`. `"none"` drops `r()`/`e()` entirely and still gives you `estimation`. |
| `include_estimation` | `"full"` | The model is dominated by `i.year i.firm` nuisance terms and you only want the model-level block: `"summary"`. |
| `max_coefficients` | unset | You want the first N coefficient rows. `estimation.n_coefficients` still reports the true count and `coefficients_truncated` flags the cut, so you can always tell. |
| `timeout_ms` | `600000` | A run legitimately needs longer, or you want to fail fast. The budget covers **queueing**, so a call waiting on a busy session returns `rc: -5` rather than hanging. |
| `run_in_background` | `false` | Bootstraps, permutation tests, `prodest` loops — anything multi-minute. Returns a `job_id` immediately; poll `stata_run_status`. **Give the job its own `session_id`** if you want to keep working, since one Stata process serves one session. |
| `track_output_files` | `true` | Leave it on: `result.outputs` tells you which `esttab` tables / exports / `.dta` files the run wrote, so you don't have to go hunting with shell tools. |
| `auto_close_logs` | `true` | Leave it on. It closes log handles a *failed* run leaked; without it an aborted `log using` makes every later run in that session die with r(604). |

There are also MCP resources (`stata://schema/run-result`, `log://...`, `graph://...`, `matrix://...`) and prompts (`run_do_file_and_report`, `debug_stata_error`, `fix_and_rerun_until_passes`, `replication_audit`, `summarize_estimation_results`, `run_notebook_cell_and_report`, `fix_and_rerun_notebook_cell`, `plan_cross_stack_parity_audit`, `data_mcp_to_stata_handoff`, `did_event_study`, `iv_2sls`, `rdd`, `publication_table`, `cross_validate_did`).

## 5. The v1.0 RunResult schema (read this once)

Every `stata_run` reply has this shape (full spec: `stata://schema/run-result` or `SCHEMA.md` in the repo):

```jsonc
{
  "ok": true,                      // ← branch on this first
  "rc": 0,                         // Stata _rc; -1 adapter crash, -2 timeout, -3 cancelled,
                                   //   -4 policy blocked, -5 session busy
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
    // By default every matrix is a STUB: no values, no labels, just shape + ref.
    "e": {"scalars": {…}, "macros": {…},
          "matrices": {"b": {"rows": [], "cols": [], "values": null,
                             "ref": "matrix://…", "n_rows": 1, "n_cols": 2}, …}},
    "last_estimation_cmd": "regress",
    // ← THE typed view. Read this instead of the raw matrices.
    "estimation": {
      "command": "regress", "command_family": "ols", "depvar": "price",
      "n_obs": 74, "statistic_kind": "t", "source": "r_table",
      "coefficients": [{"term": "mpg", "b": …, "se": …, "statistic": …,
                        "p_value": …, "ci_low": …, "ci_high": …}, …],
      "n_coefficients": 2,            // true term count, even if rows were trimmed
      "coefficients_truncated": false,
      "model_stats":  {"N": 74, "r2": 0.219, …},
      "diagnostics":  {"weak_id_F": …, "hansen_p": …}   // command-aware, never fabricated
    }
  },

  "dataset": {"frame": "default", "n_obs": 74, "n_vars": 12, "changed": false, …},
  "graphs":   [{"ref": "graph://…", "format": "png", "source_command": "scatter …", "source_line": 5}],
  "outputs":  [{"path": "/w/tables/t1.tex", "bytes": 4552, "created": true}],  // files the run wrote
  "warnings": [{"kind": "convergence", "message": "…"}],

  "error": null,                   // populated iff ok=false; see §7
  "origin": null,                  // echoes origin_* request fields
  "schema_version": "1.0",
  "capabilities": ["log_truncation", "graph_ref", "matrix_ref", "multi_session",
                   "result_budget", "background_runs", "output_tracking", "log_hygiene", …]
}
```

**Key invariants:**
- Branch on `ok` first; never grep `log.head` to decide success.
- Scalars are native numbers. Missing is JSON `null`, not `"."` and never `8.988e+307`. The same holds for every matrix cell.
- **`results.estimation` is the coefficient table to read.** It is always derived from the complete values, so it carries real `se` / `p_value` / CI even when the matrices are stubs. Do not reconstruct inference from `e(b)` and `e(V)` yourself.
- Matrices arrive as `values: null` + a `matrix://` ref by default — call `get_matrix` only when you need the raw numbers.
- `result.outputs` lists the files the run wrote. Read it instead of shelling out to find the `esttab` table you just produced.
- Graphs default to refs, not base64 bytes. `include_graphs: "inline"` now returns real image content blocks (viewable), capped at 4 per response.

## 6. Token-economy defaults — keep responses small

`stata-code` is already aggressive about this; do not undo its work:

- **Do not** pass `include_full_log: true` unless the user asked for the full log or the head/tail clearly miss the relevant content.
- **Do not** pass `include_graphs: "inline"` unless the agent needs the bytes (rare; usually surface the `ref`).
- **Do not** read `get_log(ref)` proactively; prefer `search_log(ref, pattern)` to pull just the lines you need, and only fall back to `get_log` for the full transcript.
- **Do not** pass `include_results: "full"` reflexively. `results.estimation` already has the typed table with real standard errors; `"full"` roughly doubles the envelope for a wide model and hands you the same numbers three more times.
- **Do** reach for `include_estimation: "summary"` or `max_coefficients` when a model has dozens of fixed-effect terms you are not going to report.
- **Do** quote specific numbers from `results.estimation` / `results.e.scalars` rather than dumping JSON.
- **Do** send genuinely long jobs to `run_in_background` on their own `session_id` instead of blocking a foreground call for minutes.

## 7. The typed-error taxonomy (34 kinds)

On failure, the `error` block looks like:

```jsonc
{
  "kind": "varname_not_found",      // ← branch on this, not on rc or message
  "rc": 111,
  "rc_label": "variable not found",
  "message": "variable mpgg not found",
  "command": "summarize mpgg",
  "line": 3,                        // line within source_file, else within submitted code
  "source_file": null,              // set when the failure was inside a `do`/`run` script
  "context": {"before": ["use auto"], "failing": "summarize mpgg", "after": []},
  "commands_executed": 1,
  "varname": "mpgg",               // populated for varname_* / file_* / name_* kinds
  "suggestions": [{"action": "Did you mean `mpg`?", "command": "describe"}],
  "recovery": {"category": "user_code", "retriable": false,
               "needs_code_change": true, "needs_user_input": false}
}
```

When you submit `do "analysis.do"` and it fails, `line` and `context` point **inside
`analysis.do`** and `source_file` names it — you do not need to re-read the script to
find the offending line. A failed run also still carries a full `log`, so
`search_log(ref, …)` works on failures.

Kinds you will see most often:

- `varname_not_found` (rc 111) — `varname` is filled; check `dataset.variables` for the right name.
- `syntax` (rc 100–103/121–127/130/132/197/198) — usually a typo; inspect `context.failing`.
- `command_not_found` (rc 199) — often a community package: `install_package(name=...)`.
- `file_not_found` / `file_exists` / `file_corrupt` (rc 601/602/610/688) — `path` is filled.
- `not_sorted` (rc 5) — prepend `sort <var>`.
- `name_conflict` (rc 110) — use `replace` or pick a fresh name.
- `convergence` / `infeasible` (rc 430/491) — model issue, not a typo; do not loop on it.
- `no_estimation_results` (rc 301) — likely `predict`/`margins` before any `regress`.
- `log_state` (rc 604/606) — a log handle is in the wrong state, almost always left by an earlier aborted run. Fix is `capture log close _all`, **not** a code change; `recovery.retriable` is true. `auto_close_logs` prevents most of these.
- `session_busy` (−5) — that session's Stata process is still running an earlier request. **Nothing ran**; the code is fine. Wait, raise `timeout_ms`, use a different `session_id`, or send the long job to the background.
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
    policy_blocked       → the code used a blocked OS-escape command (shell/erase/rmdir/!);
                           do the task with a native Stata command instead, do NOT retry as-is
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
- Do not use OS-escape / file-deletion commands (`shell`, `winexec`, `erase`, `rm`, `rmdir`, `!`) inside `stata_run`. They are blocked by the command-safety policy and return `policy_blocked`. Stay within native Stata data commands (`save` / `use` / `copy`); if a file genuinely must be deleted, ask the user to do it or to relax the policy.
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
