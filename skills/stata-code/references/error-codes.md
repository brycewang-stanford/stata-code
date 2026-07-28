# Stata return codes & the self-repair loop

*Read this when a `stata_run` came back `ok: false` and you need to classify the failure and decide a fix.*

**Do not grep the English message.** `stata-code` already classifies every
failure into a typed `error` block. Branch on `error.kind` (a stable enum),
fall back to `error.rc` (Stata's `_rc`), and only read `error.message` /
`error.context.failing` for human display. The fix you apply should be driven
by `kind`, not by string-matching the log.

## The error block

```jsonc
{
  "kind": "varname_not_found",   // ← branch on this
  "rc": 111,                     // Stata _rc (or synthetic; see below)
  "rc_label": "variable not found",
  "message": "variable mpgg not found",
  "command": "summarize mpgg",
  "line": 3,                     // 1-based line within `source_file`, else within submitted code
  "source_file": null,           // set when the failure happened inside a `do`/`run` script
  "context": {"before": [...], "failing": "summarize mpgg", "after": [...]},
  "commands_executed": 1,
  "varname": "mpgg",             // typed extra field — see table
  "path": null,
  "name": null,
  "suggestions": [{"action": "Did you mean `mpg`?", "command": "describe"}],
  "recovery": {"category": "user_code", "retriable": false,
               "needs_code_change": true, "needs_user_input": false}
}
```

**Failures inside a `do`-file are localized.** Submit `do "analysis.do"`, and when
it fails `line` and `context` refer to the line *inside `analysis.do`*, with
`source_file` naming the script. `context.failing` is the whole logical command,
with `///` continuations folded back together. You do not need to re-read the
script to find the offending line, and `message` is Stata's diagnosis rather
than its `end of do-file` epilogue.

`suggestions` are **hints**, not commands to run blindly. Apply a fix
automatically only when the user asked you to repair-and-rerun; otherwise
report `kind` + `line` + `context.failing` + the suggestions and stop.

## rc → kind → fix

| rc | `error.kind` | What it means | First fix to try |
|----|--------------|---------------|------------------|
| 100–103, 121–127, 130, 132, 197, 198 | `syntax` | Parser rejected the line (incl. numlist errors 121–127) | Fix the typo on `error.line`; check `context.failing` |
| 199 | `command_not_found` | Command/program unknown | If a community pkg: `install_package(name=...)` (`ssc install`); else fuzzy-match a builtin (see `suggestions`) |
| 111 | `varname_not_found` | Variable not in memory | Use `error.varname` + `dataset.variables` to pick the closest real name |
| — | `invalid_name` | Illegal name (no dedicated rc — Stata folds it into r(198); kind set from the message) | Rename to `[A-Za-z_][A-Za-z0-9_]{0,31}`, not starting with a digit |
| 110 | `name_conflict` | Object already exists | Add `replace`, or `drop`/`capture drop` the object first |
| 109, 408 | `type_mismatch` | String vs numeric op | `destring`/`tostring`/`encode`; check the operand types |
| 5 | `not_sorted` | Command needs sorted data | Prepend `sort <byvars>` (or `bysort`) |
| 430 | `convergence` | Optimizer didn't converge | `iterate()`, relax `nrtolerance()`, try `technique(bfgs)`; this is a model issue, not a typo |
| 480, 491 | `infeasible` | No feasible solution / invalid starting values | Respecify the model; check perfect prediction / collinearity |
| 301 | `no_estimation_results` | `predict`/`margins`/`test` before any estimation | Run a model first; check `results.last_estimation_cmd` |
| — | `estimation_sample_empty` | Sample empty after `if`/missing exclusions (no dedicated rc; kind set from the message) | `count if <cond>`; `misstable summarize` |
| 322, 1400, 1401, 1402 | `estimation_failure` | Estimation aborted (incl. numerical overflow) | Inspect data/spec; often collinearity or singletons |
| 2000, 2001 | `no_observations` | No / too few obs match | `count if <cond>`; widen or drop the `if`/`in` |
| 4 | `data_in_memory` | Unsaved data would be lost | `clear` (or save first) |
| 503, 507 | `matrix_conformability` | Non-conformable matrices (507: `matrix post` name conflict) | Check shapes with `rowsof()` / `colsof()` |
| 504 | `matrix_missing` | Matrix has missing entries | Inspect inputs; drop/handle missings first |
| 506, 508 | `matrix_singular` | Singular / not pos-def | Find collinearity (`corr`, `estat vif`); consider `noconst` |
| 601 | `file_not_found` | Path doesn't resolve | Check `pwd`/`ls`; fix path or add `.dta`/`.do`; in stata-code pass `origin_path`/`working_dir` |
| 602 | `file_exists` | Output file exists | Add `replace` |
| 603, 691–693 | `file_io` | Local read/write failed | Check permissions / disk |
| 610, 688 | `file_corrupt` | File unreadable/wrong format | Verify it's a valid `.dta`/correct version |
| 2, 631, 672, 677 | `network` | Download/SSC/net failed | Retry; check connectivity; `install_package` may need a proxy |
| 608 | `permission` | Permission denied | Fix file/dir permissions or target a writable dir |
| 604, 606 | `log_state` | A log is already open (604), or none is open when one was expected (606) | `capture log close _all`, then re-run **unchanged** — `recovery.retriable` is true. Usually residue from an earlier run that aborted between `log using` and `log close`; `auto_close_logs` (on by default) prevents most of these. Start long scripts with `capture log close _all` to make them re-runnable. |
| — | `encoding` | Encoding problem (no dedicated rc; kind set from the message) | Set `encoding()` on import; check the source file's charset |
| 901–903, 907 | `stata_limit` | Edition/`maxvar`/width limit | `set maxvar`; reduce vars; a bigger edition (SE→MP) raises the ceiling |
| 909, 950 | `out_of_memory` | Out of memory | `compress`; drop unneeded vars/obs; bigger edition |
| 1 | `interrupt` | User/system interrupt | Re-run if intended |
| — | `unknown` | rc not yet mapped (e.g. 9, 119, 459, 615/616) | Read `message`/`context`; treat conservatively |

### Synthetic codes (set by stata-code, not by Stata)

| rc | `error.kind` | Meaning | Action |
|----|--------------|---------|--------|
| −1 | `adapter_crash` | The pystata adapter/worker crashed | System-level — do **not** retry blindly; report it |
| −2 | `timeout` | Hit the run timeout | The op is genuinely slow or hung; do not just resubmit unchanged |
| −3 | `cancelled` | A `cancel_session` killed the run | Expected after an explicit stop |
| −4 | `policy_blocked` | The command-safety policy rejected the code before Stata ran (`shell`, `erase`, `!`, …) | Drop the OS-escape command; a human must relax the policy to allow it |
| −5 | `session_busy` | That session's Stata process was still running an earlier request when your `timeout_ms` elapsed | **Nothing ran and nothing is wrong with the code.** Wait and retry, raise `timeout_ms`, use a different `session_id`, or submit with `run_in_background` |

## Typed extra fields by kind

These are populated so you can fix without re-parsing the message:

- `varname` — `varname_not_found`, `invalid_name` (the bad name).
- `path` — `file_not_found`, `file_exists`, `file_corrupt`, `file_io` (the offending path).
- `name` — `name_conflict` (the conflicting object name).

## The self-repair loop

Only run this when the user asked you to fix-and-rerun. Otherwise: diagnose and stop.

```text
loop (cap at ~5 iterations):
  result = stata_run(current_code)
  if result.ok: break
  e = result.error
  switch e.kind:
    command_not_found    → if community pkg: install_package(name); else fix spelling
    varname_not_found    → closest match from dataset.variables / e.varname
    syntax               → fix the line at e.line (e.context.failing)
    not_sorted           → prepend `sort <byvars>`
    name_conflict        → add `replace` or drop e.name first
    file_not_found       → fix e.path or generate the missing file
    no_estimation_results→ run the estimation command first
    convergence/infeasible→ this is a MODEL issue: change the spec, don't loop on it
    adapter_crash/timeout/cancelled → STOP; surface to the user
  apply the minimal edit to the .do file / notebook cell, then re-run
  if the same kind+line repeats unchanged twice → STOP and summarize
```

Guardrails: never resubmit a failing command unchanged; never loop on a model
problem (`convergence`, `infeasible`, `estimation_failure`) as if it were a
typo; bail with a summary after ~5 iterations.

## Inspecting a long failing log

A failed run carries a full log, same as a successful one — pystata raises the
transcript as its exception, and stata-code adopts it as `log`. So
`log.error_window`, the `log://` ref and `search_log` all work on failures.

When `log.truncated` is true, the failure window is usually already in
`log.error_window`. If you need more, don't pull the whole log — use
`search_log(ref, pattern)` to grep the `log://` ref for the rc number or the
command name, then `get_log(ref)` only if you truly need the full transcript.

## Log handles and re-runnable scripts

A script that aborts between `log using` and `log close` leaves the handle open.
`stata_run` closes handles that a *failed* run opened (`auto_close_logs`, on by
default) and reports it as a `log_closed` warning, so one failure cannot make
every later run in that session die with r(604). Handles opened by *earlier*
runs are deliberately left alone — they are the caller's to manage. When writing
a script that may be re-run, still open with `capture log close _all` first.

## See also

- `defensive-coding.md` — how to write Stata that fails loudly and early instead of producing these silently.
- The live mapping table is `stata_code/core/errors.py` (`RC_TO_KIND`); it is the source of truth and tightens over time.
