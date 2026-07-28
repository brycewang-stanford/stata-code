# Changelog

All notable changes to `stata-code` are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the project adheres
to semver-major.minor for the result schema (see `SCHEMA.md` §6).

## [Unreleased]

## 0.12.0 — 2026-07-28

Adds a resident session daemon so the CLI can keep data in memory across
invocations, and fixes a VS Code regression introduced by 0.11.0's payload
defaults.

### Added

#### Resident session daemon — state across CLI invocations

The subprocess pool already kept one Stata worker alive per session, but a plain
`stata-code run` owned that pool and tore it down on exit, so two consecutive CLI
calls could not share data in memory. The new `daemon` subcommand holds the pool
in a resident process behind a Unix socket:

```bash
stata-code run --daemon -e 'sysuse auto, clear' -e 'gen z = price/1000'
stata-code run --daemon -e 'summarize z'      # new process, z is still loaded
```

- **`stata-code run --daemon`** routes the run through the daemon, starting it on
  demand. `--socket` overrides the path. Graph refs are resolved back over the
  socket, so `--graphs DIR` keeps working.
- **`stata-code daemon start | stop | restart | status`** for explicit control;
  `status --json` is machine-readable.
- **Idle retirement** after 30 minutes by default (`--idle-timeout`, `0` disables)
  so a forgotten daemon does not hold a Stata license slot.
- **Unix socket only, never TCP** — mode 0600 inside a mode-0700 directory. The
  command-safety guard still applies to everything the daemon runs.
- **Requires the pystata backend.** `--backend console` is stateless batch
  execution, so combining it with `--daemon` is a hard error rather than a silent
  no-op.
- Falls back to a short hashed `/tmp` socket path when the natural location would
  exceed the platform's ~104-byte `sun_path` limit; every entry point derives the
  same fallback, so clients still find the daemon.

#### VS Code Outputs panel now lists what every run wrote

The panel read only `log.files.output_paths` — the archived copies inside a run
bundle — so it stayed empty unless the caller had opted into
`persist_log_files`. It now also reads `result.outputs`, the files a run wrote
in its working directory (reported on every run since 0.11.0), shown with their
size and listed *ahead* of bundle copies, since the working-directory file is
the one you want to open. Nodes carry an `origin` of `workdir` or `bundle`, and
an overwritten file says so in its tooltip.

### Fixed

- **VS Code: r()/e() matrices displayed as `0x0` and opened empty.** 0.11.0
  made `include_results: "scalars"` the server default, which returns each
  matrix as a `matrix://` stub with `rows` / `cols` elided (the true shape moved
  to `n_rows` / `n_cols`). The extension's sidebar derives its dimension label
  from `rows.length` / `cols.length` and renders `matrix.values` directly, so
  every matrix showed `0x0` and exported an empty table. The extension now
  requests `include_results: "full"` — an editor is a human surface with no
  token budget to defend, matching what the Jupyter kernel already did. The
  argument builder moved to a `vscode`-free `runArgs.ts` so the behaviour is
  covered by unit tests.
- Documentation: the error taxonomy was described as "32 kinds" in `README.md`,
  `README.en.md`, and `SCHEMA.md` while `ErrorKind` has had 34 members (the
  English README's prose already said 34, contradicting its own table).
- Documentation: both READMEs listed "Stata 17+" as a hard install requirement,
  which contradicted the console backend's documented Stata 13+ support.
- `README.md` was missing the command-line, client-setup, and command-safety
  sections that `README.en.md` has had since 0.11.0.
- Documentation: `SKILL.md` and the agent reference library still described the
  pre-0.11 surface — 18 tools, 31 error kinds, no mention of matrix stubs,
  background runs, `result.outputs`, or `error.source_file`. Counts in every doc
  are now checked against the code.

### Internal

- `_collect_returns` no longer takes a `request_id` or creates `matrix://` refs.
  Nothing passed it — neither the production path nor any test — and ref
  creation belongs to `_project_returns`, which owns the payload budget.
- `test_varname_not_found_emits_did_you_mean` failed once and could not be
  reproduced. Its fuzzy match draws candidates from `dataset.variables`, so a
  lost `auto.dta` in the shared in-process Stata degrades the suggestion for
  reasons unrelated to the matching logic. The assertion was **split**, not
  loosened: a precondition check now reports which of the two it was.

## 0.11.0 — 2026-07-28

An agent-ergonomics release, driven by a report from an agent that used the MCP
server to write a full empirical paper. Each item below is a cost that agent
actually paid.

**Minor, not patch:** `include_results` defaults to `"scalars"`, so `r()` / `e()`
matrices now arrive as `matrix://` stubs instead of inline values. Pass
`include_results="full"` for the previous shape. `results.estimation` is
unaffected — and is now *more* accurate, since two silent numeric bugs in it are
fixed. The Jupyter kernel and the `stata-code run` CLI keep `"full"`.

### Agent-ergonomics pass

#### Result payloads are now bounded (and correct)

- **`include_results`** (`"none" | "scalars" | "full"`, default **`"scalars"`**).
  A single estimation used to encode the same numbers four times over — `e(b)`,
  `e(V)`'s row/column labels, `e(beta)`, and `r(table)` — on top of
  `results.estimation`. Under the new default, scalars and macros stay inline
  and every matrix becomes a `matrix://` stub carrying only its shape
  (`n_rows` / `n_cols`); values and labels remain retrievable with
  `get_matrix(ref)`. A 123-term regression's envelope drops from ~57 KB to
  ~28 KB. **This is a behaviour change** — pass `include_results="full"` for
  the previous shape. The Jupyter kernel keeps `"full"`, since a notebook has
  no token budget to defend.
- **`include_estimation`** (`"none" | "summary" | "full"`, default `"full"`)
  and **`max_coefficients`**. `"summary"` keeps the model-level block and
  diagnostics but drops per-term rows — the right setting for specifications
  dominated by `i.year i.firm` nuisance terms. `estimation.n_coefficients`
  always reports the model's true term count and
  `estimation.coefficients_truncated` flags any cut, so trimmed output can
  never be mistaken for a smaller model.

#### Fixed: two silent numeric-correctness bugs

- **Standard errors were blanked whenever `e(V)` was returned by reference.**
  Any model whose VCV exceeded the inline cell cap (roughly >100 terms) came
  back with `se`, `statistic`, `p_value` and both CI bounds `null` for *every*
  coefficient, while still reporting `source: "e_b_v"` as though inference had
  been computed. The estimation builder now resolves `matrix://` refs, so the
  wire representation no longer degrades the numbers. `e(b)` by reference is
  resolved too, where it previously suppressed the estimation block entirely.
- **`r()` did not survive to the next call.** The runner's own probes are all
  r-class (`graph dir` before a run, `graph export` after), so the canonical
  two-call pattern — `summarize price`, then `display r(mean)` — read back a
  missing value. All internal housekeeping now runs inside a Stata
  `_return hold` / `_return restore` pair. Pre-existing; the new `log query`
  probe would have widened it to runs with `include_graphs="none"`.
- **Stata's missing values leaked as `8.988e+307`.** `sfi` returns system
  missing (`.`) and extended missings (`.a`–`.z`) as ordinary doubles at or
  above `2^1023`. These reached the wire unconverted in `r()`/`e()` scalars and
  in every matrix cell — an omitted base level's standard error arrived as a
  number an agent would format into a table. All numerics now normalize to
  `null`, matching what SCHEMA.md already required of scalars.

#### Errors inside `do`-files are now located

- `error.line` and `error.context` are resolved **inside the invoked script**.
  A failing `do "analysis.do"` previously produced `line: null`, empty
  before/after context, and `message: "end of do-file"` — Stata's epilogue
  rather than the diagnosis. The transcript parser now skips structural
  boilerplate (`end of do-file`, `--Break--`), folds `///` continuation
  fragments back into one logical command, and resolves the failing command
  against the script on disk. New `error.source_file` names the file that
  `error.line` indexes into.
- **A failed run now produces a log.** pystata raises the whole transcript as
  its exception message and leaves stdout empty, so failures used to come back
  with no `log.head`, no `log://` ref and nothing for `search_log` to search —
  exactly when the transcript matters most.

#### A failed run can no longer poison its session

- New **`auto_close_logs`** (default `true`) closes log handles that a *failed*
  run opened. A script aborting between `log using` and `log close` previously
  left the handle dangling, and every subsequent run in that session died with
  r(604) — an error about a different run, with no suggestions attached.
  Handles opened by earlier runs are left alone; a `log_closed` warning records
  what was closed.
- r(604) and r(606) are now the typed `ErrorKind.LOG_STATE` with Stata's
  official labels and rc-specific fixes (`capture log close _all` for 604,
  `capture log close` guarding for 606), and `recovery.retriable = true`.

#### Long runs no longer block the caller

- **`timeout_ms` is an advertised `stata_run` argument.** It was enforced but
  not exposed, so passing it was an input-validation error.
- **`run_in_background`** returns a `job_id` immediately; poll with the new
  **`stata_run_status`** tool (`wait_ms` blocks up to 60 s instead of
  busy-polling) and enumerate with **`list_background_runs`**. Intended for
  bootstraps, permutation tests and grouped estimation loops.
- **Fixed: the worker lock was acquired without a deadline**, so a call queued
  behind a long run blocked indefinitely no matter what `timeout_ms` said.
  `timeout_ms` now budgets the whole call, queueing included, and contention
  returns the new `rc=-5` / `error.kind="session_busy"` — distinct from a
  timeout because nothing was submitted to Stata and the healthy worker is
  *not* killed.

#### Inline graphs are visible again

- `include_graphs: "inline"` now delivers graph bytes as MCP `ImageContent`
  blocks instead of base64 inside a JSON string. The old form was unreadable to
  a vision-capable client and was pure token cost. `graphs[].inline` is cleared
  from the structured body so the bytes cross the wire once, and
  `inline_delivered` records the outcome. At most 4 images per response (the
  rest stay reachable via `get_graph(ref)`, reported as an
  `inline_graphs_truncated` warning); `pdf` is never sent as an image block.

#### Generated files are reported

- New top-level **`outputs`** lists files a run created or modified in its
  working directory (`esttab` tables, exported graphs, saved `.dta`), with
  `path` / `bytes` / `created`. Previously this required
  `persist_log_files=true` *and* `origin_path` — a dependency that was not
  obvious from `persist_generated_files` defaulting to `true`, so the field was
  always `null` in practice. Controlled by the new `track_output_files`
  (default `true`); working directories above 5,000 files skip detection and
  say so with an `output_tracking_skipped` warning rather than reporting a
  partial answer.

### Added

- The VS Code extension now accepts Stata's familiar `Ctrl+D` shortcut
  (`Cmd+D` on macOS) for running the selection or current line, while retaining
  `Cmd/Ctrl+Enter`. The extension README also documents how to customize the
  `stataCode.runSelection` keybinding and resolve conflicts.

## 0.10.0 — 2026-07-24

Surface-coverage and safety additions so every Stata usage scenario — MCP,
Jupyter, VS Code, and now a plain Bash/terminal — routes through the same typed
engine, and so an autonomous agent loop cannot escape to the OS. Also broadens
execution to **Stata 13+ without pystata** (console backend) and adds a
zero-Python standalone binary and one-click VS Code onboarding. The default
README is now Chinese (`README.md`); the English README moves to `README.en.md`.

### Added

- **Command-safety guard** (`stata_code.core.policy`). OS-escape / file-deletion
  commands (`shell`, `winexec`, `erase`, `rm`, `rmdir`, and the `!` shell escape)
  are screened out of submitted code *before* Stata runs, at both the
  subprocess-pool boundary and the in-process runner. A blocked run returns
  `ok=false`, `rc=-4`, `error.kind="policy_blocked"` (a new 32nd `ErrorKind`)
  without touching Stata. Configurable from the environment so it crosses the
  worker boundary: `STATA_CODE_COMMAND_POLICY` (`enforce` default / `warn` /
  `off`), `STATA_CODE_POLICY_ALLOW`, `STATA_CODE_POLICY_BLOCK`. It is a guard
  rail, not a sandbox.
- **`stata-code run`** — execute a `.do` file, one or more `-e` snippets, or
  stdin through the subprocess pool and print the `RunResult` (text summary or
  `--json`), with `--session`, `--timeout-ms`, `--full-log`, and `--graphs DIR`.
  Exit code is `0` on success, `1` on a Stata/adapter error. This is the
  Bash / plain-terminal surface: any agent that can shell out gets the full
  structured error loop without MCP.
- **`stata-code setup`** — opt-in, config-mutating counterpart to `doctor`.
  Writes the `stata-code` MCP server entry into Claude Code (`.mcp.json`),
  Cursor (`.cursor/mcp.json`), and VS Code (`.vscode/mcp.json`), preserving other
  servers and backing up any file it overwrites (`--dry-run`, `--python`,
  `--json`). Codex (TOML) and Claude Desktop are emitted as copy-paste snippets.
- **Static do-file linting** (`stata_code.core.lint`), exposed as the `lint_do`
  MCP tool (19th tool) and `stata-code lint`. A Stata-free syntactic check —
  unbalanced braces, a `program` / `mata` / `python` block with no `end`, a stray
  `end`, and a dangling `///` — so an agent can catch a class of mistakes before
  spending a run. Advisory: a clean result is not a correctness guarantee.
- Public API: `lint_code`, `LintFinding`, `CommandPolicy`, `Violation`,
  `policy_from_env`.
- **Console (batch) backend** (`stata_code.core.console`, `Backend.CONSOLE`).
  Drives the Stata command-line executable in batch mode and parses the log plus
  a marker-delimited results dump into the same v1.0 `RunResult` — typed
  `r()`/`e()` scalars and macros, the estimation matrices (`e(b)`/`e(V)`/
  `r(table)`), the error taxonomy, warnings, and dataset metadata. Works with
  **Stata 13+** and **no pystata**. Exposed as `stata_code.run_console()`,
  `stata-code run --backend {auto,pystata,console}`, and a `doctor` check. Stateless
  per call; graphs not captured yet. Public API adds `run_console`,
  `console_available`, `find_stata_cli`, `ConsoleNotAvailable`.
- **Zero-Python standalone binary** — `scripts/build_standalone.py` (PyInstaller)
  and a CI workflow template (`packaging/standalone.github-workflow.yml`, copy to
  `.github/workflows/`) build a self-contained `stata-code` executable per OS.
  Paired with `--backend console` it needs no Python on the target machine.
- **One-click VS Code onboarding** — when the MCP server is missing, the extension
  offers to create a workspace `.venv` and install the package; also available as
  the command-palette entry "Stata: Set Up MCP Server (create .venv)".
- **Benchmark harness** (`benchmarks/`) — a tool-agnostic scaffold measuring
  iterations-to-correct and tokens for typed vs. raw-log execution, with an offline
  mock runner wired end-to-end and CI-covered.
- Chinese quickstart ([docs/quickstart.zh.md](docs/quickstart.zh.md)).

## 0.9.1 — 2026-07-22

Quality-hardening pass: correctness fixes from an adversarial code review,
a large offline test expansion, and a documentation accuracy audit. No new
features, no API or schema changes.

A second review round found that three of the first round's fixes were
incomplete or had introduced a regression of their own; those are the first
four entries below. The common failure was testing a fix in isolation while
the real call path stayed uncovered — the new tests go through `SessionPool`
rather than `WorkerProcess` wherever the pool is what carries the bug, and
each was confirmed to fail against the pre-fix source.

### Fixed

- **A read-only status query no longer kills a healthy mid-run worker.**
  Once `send_simple_op` learned to bail out rather than block on the worker
  lock, that bail-out surfaced as a `_WorkerTimeout` — and
  `SessionPool.stata_info` treated "busy" and "dead" identically: it
  SIGTERMed the worker and dropped it from the pool. Calling the
  `stata_info` tool during a long `bootstrap`/`reghdfe` therefore returned
  the user's run as `adapter_crash` and wiped the session's loaded data, and
  because the MCP layer swallows the exception the kill was invisible in the
  tool response. Busy is now a distinct `_WorkerBusy` signal (a
  `_WorkerTimeout` subclass, so warn-only callers are unaffected) that never
  kills.
- **A timed-out read no longer steals the next request's response.** Each
  request spawned its own `readline()` thread and abandoned it on deadline
  overrun; the orphan stayed blocked on the pipe and consumed the *following*
  response. The next `execute()` then burned its entire timeout (default
  600 s) waiting for a reply that had already been eaten, after which the
  pool killed a perfectly healthy worker. Reads now come off one long-lived
  pump thread per worker, which cannot orphan, and responses carrying a
  stale request id are discarded rather than mis-matched.
- **`send_simple_op` honors its full timeout budget.** The read deadline was
  computed *after* acquiring the worker lock, so a call that spent most of
  its budget waiting for the lock got a second full budget for the read —
  worst case 2x `timeout_ms`, and `list_session_info_detailed` iterates
  workers serially, so N busy workers stalled the status tool for
  `2 * timeout * N`. The deadline is now fixed up front.
- **Repeated identical notes are no longer double-counted.** The previous
  de-dup short-circuited before recording the matched span, so the second and
  later copies of an identical `note:` line went unclaimed and were picked up
  again by the generic-note pass — the exact double-count the fix was meant
  to remove, reachable from any `foreach` loop re-running the same
  regression.
- **`stata_info`'s worker eviction is identity-checked.** It popped the
  session's worker unconditionally, so a concurrent `execute()` that had
  already noticed the death and registered a replacement got *its* worker
  evicted — leaking a live pystata subprocess that `request_cancel` /
  `kill_session` / `shutdown` could no longer reach.
- **`kill()`'s already-dead fast path is identity-checked** too, matching its
  own slow path: it could otherwise clear a handle a concurrent `execute()`
  had just respawned into, stranding a live subprocess mid-request.
- **`TypeError` is no longer blanket-classified as a caller error.** The
  guard wrapped the whole `execute()` call, not just argument binding, so a
  `TypeError` raised deep inside result collection told the agent its
  arguments were wrong when nothing was — and routed the failure out of the
  `adapter_crash` path that would have recycled the wedged worker. Malformed
  options are now caught by signature binding before the call; deeper
  `TypeError`s stay in the `worker_error` bucket.
- **Session ids longer than Stata's 32-character name cap are mapped.** They
  were passed through verbatim as a frame name, so `frame create` failed with
  rc 198; they now route through the hashed mapped-frame path.

- **Worker stderr pipe is now drained continuously.** The subprocess pool
  only read a worker's stderr after death, so a worker whose cumulative
  stderr output (pystata banners, Stata C-side messages, warnings) exceeded
  the ~64 KB OS pipe buffer blocked mid-write and never responded — the
  parent then misreported the healthy worker as a `timeout` and killed it
  (or hung forever with `timeout_ms=None`). A per-worker daemon thread now
  drains stderr into a bounded tail buffer, which also enriches crash
  messages.
- **A worker-reported failure no longer destroys the session.** A live
  worker answering `{ok:false, error_kind:"worker_error"}` was handled like
  a dead process: killed and dropped from the pool, wiping the session's
  loaded dataset and `r()`/`e()` state — reachable from a mere argument
  type typo. Such failures now surface as structured errors while the
  worker (and the session's data) survives; the worker also classifies
  `TypeError` as `invalid_request`.
- **LRU eviction skips busy workers.** `last_used` is stale for a mid-run
  worker, so capacity pressure from an unrelated new session preferred the
  longest-running request as its eviction victim (SIGTERM mid-run →
  `adapter_crash`, session data lost). Eviction now skips in-flight
  workers, bumps `last_used` at request start, and kills victims outside
  the pool lock; timeout/error cleanup no longer removes a newer worker
  handle registered in the interim.
- **Status queries no longer hang behind a long run.** `send_simple_op`
  blocked unboundedly on the worker lock held by an in-flight `execute()`;
  lock acquisition now counts against the same timeout budget and surfaces
  as a per-session "busy" warning.
- **`rc` is taken from the last `r(NNN);` in a failure transcript.** An
  earlier successful command echoing a literal `r(NNN);` (display string,
  help output) previously hijacked the return code — and therefore the
  `ErrorKind` classification, suggestions, and recovery contract.
- **Indented notes are no longer double-counted as warnings.** An indented
  `note: … omitted because of collinearity` line produced both an
  `omitted_collinear` and a generic `note` warning.
- **A session literally named `"default"` no longer aliases `"main"`.** In
  in-process mode it silently shared `main`'s Stata frame (one dataset for
  two nominally distinct sessions); it now routes through the private
  mapped-frame path.
- **Matrices with missing sfi row/col names are no longer dropped.**
  Positional names (`r1…`/`c1…`) are synthesized instead of letting the
  shape validator silently discard successfully read values.
- **`get_graph(format=…)` is no longer a silent no-op.** Requesting a
  format different from the stored one now returns `invalid_request`
  instead of returning mismatched bytes.
- **Post-run file snapshots survive escaping symlinks.** A symlink inside
  the working dir pointing outside it crashed `changed_output_files` with
  an uncaught `ValueError`; it is now skipped.

### Added

- **≈300 new offline tests** (467 → 724 passing without Stata); coverage
  73% → 90% overall (`mcp/server` 74→99%, `kernel` 73→98%, `_pool` 68→96%,
  `_refs` 68→100%, `log_artifacts` 76→96%, `runner` 29→72%). The package is
  now fully mypy-clean, including the previously unchecked top-level
  modules.
- **Community health files**: `CONTRIBUTING.md` (dev setup + the exact CI
  gates), `CODE_OF_CONDUCT.md` (Contributor Covenant 2.1), `SECURITY.md`,
  and GitHub issue templates that ask for `stata-code doctor` output.
- **Second-round regression tests** for the eight fixes listed above, each
  verified to fail against the pre-fix source (823 passing locally with
  Stata). They drive real subprocess workers through `SessionPool` — a
  status query racing a live run, a worker that drops its first request, a
  worker that releases the lock partway — rather than asserting on a mocked
  seam, which is what let the first round's regressions through.

### Docs

- Regenerated the skill's `rc → kind` reference from `errors.py` — it still
  documented the pre-0.9.0 mappings the taxonomy audit had corrected; fixed
  the "32 kinds" claims (the enum has 31).
- `plugin.json` install verify no longer uses `stata-code-mcp --help`
  (which hangs); it runs `stata-code doctor --no-stata-probe`.
- PUBLISHING.md / AGENTS.md version-bump lists now cover all eight version
  literals the release gate checks; removed the stale claim that a built
  `.vsix` ships in the repo.
- SCHEMA.md documents `EstimationResult.command_family` and `.diagnostics`;
  status lines updated v0.8 → v0.9; README.zh.md re-synced with the English
  README (plugin-marketplace install, per-client MCP config table, Open VSX
  and first-activation notes, cell/section conventions).

## 0.9.0 — 2026-06-23

### Fixed

- **Error-taxonomy correctness.** Audited the `_rc` → `ErrorKind` table against
  StataCorp's `[P] error` manual (Stata 19) and corrected several
  misclassifications: `not_sorted` is now `r(5)` (was the unrelated `r(119)`
  "statement out of context" / `r(459)` "data is not…"); numlist errors
  `r(122)`/`r(123)` are now `syntax` (were `invalid_name`); `r(322)` and
  `r(1400)` map to `estimation_failure` (was `file_not_found` /
  `estimation_sample_empty`); `r(480)` maps to `infeasible` (was
  `out_of_memory`); local I/O `r(691)`–`r(693)` map to `file_io` (were
  `network`). Misleading mappings for `r(9)`/`r(604)`/`r(615)`/`r(616)` were
  removed (they fall through to `unknown` rather than assert a wrong kind).
- **Command "did you mean?" now fires.** The `command_not_found` (rc 199) name
  extractor expected `"<X> unrecognized command"`, but Stata's actual message is
  `"command <X> is unrecognized"` — so the fuzzy suggestion never matched in
  practice (synthetic unit tests passed the name in directly and hid it). Fixed
  the regex and added a real-Stata integration test so a typo like `regresss`
  now surfaces "Did you mean `regress`?".

### Added

- **Typed estimation contract.** `RunResult.results.estimation` now exposes a
  frontend-neutral coefficient table derived from verified `r(table)` when
  possible, or from inline `e(b)` / `e(V)` as a clearly marked fallback. New
  public helpers `build_estimation_result()` and
  `build_estimation_from_returns()` keep the contract unit-testable without
  Stata. The contract also carries a coarse `command_family`
  (ols/iv/gmm/panel/count/did/…) and command-aware `diagnostics` — identification
  and specification tests surfaced from `e()` for the commands economists must
  report (`ivreg2`/`ivreghdfe` weak-ID F and Hansen J, `xtabond2` AR(2)/Hansen,
  `reghdfe` within-R²/absorbed FE, `xtreg` rho). Only scalars actually present in
  `e()` are surfaced — never fabricated.
- **Machine-readable recovery contract.** `error.recovery` now classifies each
  `ErrorKind` by failure domain and tells agents whether an unchanged retry,
  code edit, or user/out-of-band action is likely needed. Synthetic timeout,
  cancellation, and adapter-crash errors carry the same recovery metadata as
  ordinary Stata errors.
- **Reproducibility provenance helpers.** New `Provenance`,
  `build_provenance()`, and `build_reproducible_do()` helpers turn a completed
  `RunResult` plus original code into a runtime provenance envelope and a
  re-runnable `.do` script preamble with Stata `version`, `set more off`, and an
  optional `set seed`. Provenance now also records **per-package dependencies**
  parsed from the script (`extract_package_installs()` →
  `Provenance.packages`: `ssc`/`net install` name, source, and `from()` URL),
  and `build_submission_package()` assembles a self-contained
  replication/journal-submission bundle (`analysis.do` + `PROVENANCE.json` +
  a `README.md` manifest listing runtime, seed, and required community packages).
- **Data-MCP handoff verifier.** New `verify_dataset()` and `DatasetCheck`
  helpers validate imported datasets against provider metadata such as expected
  row count, variable count, observation bounds, and required variables.
- **`error.rc_label` is now populated for real Stata errors.** New
  `RC_LABEL` table and `label_for_rc()` (public API) supply Stata's canonical
  short message (e.g. `r(111)` → "variable not found") so agents have a stable,
  transcript-independent descriptor to branch and group on. Unverified codes
  yield an empty label rather than a guess.
- **More return codes classified** (shrinking `unknown`): real network codes
  `r(2)`/`r(631)`/`r(672)`/`r(677)` → `network`; `r(688)` → `file_corrupt`;
  `r(907)` → `stata_limit`; `r(950)` → `out_of_memory`; numlist `r(124)`–`r(127)`
  → `syntax`.
- **Remediation suggestions for more error kinds.** `suggestions_for()` now
  emits actionable hints for `network`, `infeasible`, `type_mismatch`,
  `file_io`, `file_corrupt`, `permission`, `estimation_failure`, and
  `matrix_missing`, so nearly every common failure ships a recovery hint.

## 0.8.1 — 2026-06-20

### Changed

- **README & metadata refresh.** Documented the VS Code extension's
  seven-view sidebar (added the **Data** variables browser and the
  **Outputs** table/export-artifact panel), corrected the error taxonomy
  count to 31 kinds, and sharpened the Claude Code plugin / VS Code
  Marketplace descriptions to lead with the empirical-economics workflow
  (DiD/IV/RDD, publication tables, StatsPAI cross-validation).
- **Partner module.** Added a Stanford REAP × CoPaper.AI partner block
  (logos, QR, links) to both the English and Chinese README, with the logo
  assets bundled under `branding/partners/`.

## 0.8.0 — 2026-06-20

### Added

- **Economist workflow coordination and roadmap.** Added
  `AGENT_COORDINATION.md` for concurrent-agent lanes and
  `docs/industry-leader-roadmap.md` for the one-month product plan: workflow
  intelligence, parity audits, data-MCP handoff, editor/artifact polish, and
  distribution diagnostics.
- **Cross-stack and data-MCP workflow references.** The `stata-code` skill now
  includes `references/parity-audit.md` and
  `references/data-mcp-handoff.md`, plus cookbook examples for cross-stack
  parity audits and external-data-MCP handoff into Stata.
- **Modern empirical-economics package notes.** Added package references for
  `csdid`, `drdid`, `did_imputation`, `eventstudyinteract`,
  `did_multiplegt_dyn`, `rdrobust`, `ivreg2`, `ivreghdfe`, `boottest`, and
  `outreg2`, and wired them into the skill routing table.
- **MCP prompt discoverability for economist workflows.** Added
  `plan_cross_stack_parity_audit`, `data_mcp_to_stata_handoff`,
  `did_event_study`, `iv_2sls`, `rdd`, `publication_table`, and
  `cross_validate_did` prompts so clients can discover the new protocols and
  turnkey empirical recipes directly through MCP.
- **Read-only installation diagnostics.** Added the top-level `stata-code`
  console script with `doctor` / `verify` commands. The diagnostic reports
  package/Python version, MCP and kernel extras, `pystata` discovery, console
  scripts on `PATH`, client/VS Code hints, and an optional live Stata
  version/edition probe without mutating user configuration.

## 0.7.2 — 2026-06-20

### Added

- **Three convenience MCP tools** raise the tool surface from 15 to 18:
  - `install_package(name, source?, url?, replace?, session_id?)` — installs a
    community package via `ssc install` / `net install` without the agent
    having to remember the syntax, then verifies it resolves with `which`.
    Package names and URLs are validated to keep them out of the generated
    command line; failures surface the typed `error` block (e.g. `network`).
  - `search_log(ref, pattern, is_regex?, ignore_case?, context?, max_matches?)`
    — greps within a truncated `log://` payload and returns only the matching
    lines (with optional context), so a long log can be inspected without
    pulling the whole transcript back through `get_log`.
  - `inspect_data(varlist?, detail?, session_id?)` — runs `describe` +
    `codebook` and returns the structured `dataset` block plus the codebook
    log: a one-call "what's in this dataset" the agent doesn't have to spell out.
- **On-demand Stata reference library** under `skills/stata-code/references/`
  (~4,200 lines): topic files for core syntax, data management, econometrics,
  causal inference, panel/time series, graphics, and table export; load-bearing
  `error-codes.md` (the full `rc → kind → fix` table + self-repair loop, aligned
  with the typed-error taxonomy) and `defensive-coding.md`; and per-package notes
  for `reghdfe`, `coefplot`, `estout`, and `gtools`. `SKILL.md` gained a routing
  table (read 1–3 files on demand) and a live-vs-offline execution-mode section.
- **`scripts/build_skill_zip.py`** packages the skill into a deterministic
  `build/stata-code-skill.zip` for upload as Claude.ai project knowledge.

## 0.7.1 — 2026-06-19

### Fixed

- **Jupyter kernel: graphs after the first cell now display.** Graph capture
  detected new graphs by diffing in-memory graph names before/after a run.
  Because Stata keeps only one graph per name and every unnamed graph command
  overwrites the default `Graph` in place, the second and later cells of a
  persistent session produced no name delta and their graphs were silently
  dropped — only the first cell's graph ever rendered. Capture now also
  re-exports any graph the cell's own source shows it (re)drew (every
  `name(...)` target, plus the default `Graph` for any unnamed graph command),
  so in-place redraws surface every time. The same fix covers repeated MCP
  `stata_run` calls in one session. The graph-command detector was tightened
  to distinguish drawing commands from `graph` utility subcommands (`export`,
  `display`, `dir`, `drop`, …) so a utility-only cell no longer re-surfaces a
  stale graph.
- **Jupyter kernel: no more duplicated code echo in cell output.** pystata
  runs a multi-line cell as a temporary do-file, and Stata echoes every
  submitted command (`. cmd` / `> continuation`) regardless of `echo=False`
  (which only suppresses echo for a single inline command). For a cell with no
  textual output (e.g. a graph) that echo was the *only* thing shown — a
  useless repeat of the source already visible in the input cell. The kernel
  now strips command-echo lines before streaming, keeping genuine command
  output. The full log (with echo) is unchanged in `RunResult.log` for MCP /
  agent consumers.

### Changed

- **VS Code extension now ships a Marketplace icon** (coef-plot mark, Anthropic
  palette on white) so the listing and Extensions sidebar render branded
  artwork instead of the default placeholder.

## 0.7.0 — 2026-05-30

### Added

- **Schema-compatible public session ids end to end.** Runner, pool, MCP,
  and VS Code now accept the schema's `[A-Za-z0-9_-]+` session ids. Values
  that Stata cannot use as frame names, such as `model-a` or `9abc`, are
  mapped to deterministic private frame names internally while results and
  session listings keep echoing the public id.
- **Graph source attribution.** Captured graphs now receive best-effort
  `source_command` / `source_line` metadata from the submitted code for
  named graphs and unambiguous unnamed graph creation.
- **VS Code pure formatter/session helpers.** Log/data-preview/matrix
  formatting and session-id rules moved out of `extension.ts` into small
  tested modules, reducing the monolithic extension entrypoint.
- **Run-index pagination.** `list_runs` now accepts `offset` alongside
  `limit` so agents can page through long run-bundle histories.

### Fixed

- **Release-version drift guard.** `scripts/check_versions.py` now checks
  `vscode/package-lock.json` plus the Claude plugin marketplace manifests,
  and has regression tests for those release surfaces.
- **Pool invalid-request classification.** Worker-side `ValueError` and
  `NotImplementedError` now propagate as caller errors instead of being
  wrapped as `adapter_crash`.
- **VS Code RunResult type drift.** The hand-written TypeScript type now
  includes `origin` and nullable schema fields such as `stata.version` and
  `stata_elapsed_ms`.
- **Run-index `since` filtering.** Date-only and seconds-only `since`
  values are normalized to canonical millisecond UTC before comparison, and
  malformed values now raise a typed `since_invalid` error.
- **Run-bundle manifest writes.** Manifest creation and post-run artifact
  rewrites now use temp-file-and-rename writes with fsync so concurrent
  `list_runs` readers do not observe torn JSON.
- **Notebook repair hardening.** Cell edits now retain a compact summary of
  outputs they clear, abort if the notebook is deleted between read and write,
  repair fully-id'd pre-4.5 metadata, and expose malformed raw cell indices in
  `notebook_outline`.

## 0.6.5 — 2026-05-22

### Fixed

- **OpenAI tool-schema compatibility.** `notebook_locate` and
  `notebook_insert_cell` no longer advertise top-level `oneOf` constraints in
  their MCP input schemas. OpenAI rejects those schemas during tool
  registration, while the server-side runtime guards still enforce the
  "exactly one query/anchor" rules.

## 0.6.4 — 2026-05-21

### Added

- **Claude Code plugin marketplace manifest.** `.claude-plugin/marketplace.json` +
  `.claude-plugin/plugin.json` expose the repo as a single-plugin
  marketplace, so users can install everything (MCP server config + agent
  skill) with `claude plugin marketplace add brycewang-stanford/stata-code`
  followed by `claude plugin install stata-code`.
- **`stata-code` agent skill.** `skills/stata-code/SKILL.md` teaches Claude
  the v1.0 RunResult schema, the 15 MCP tools, the token-economy defaults,
  the 32-kind error taxonomy, and the diagnose-only vs. fix-and-rerun
  workflows. The plugin manifest auto-installs it alongside the MCP
  server.
- **VS Code install-hint probe.** On activation the extension now resolves
  the configured `stata-code-mcp` candidate list against `PATH` and any
  workspace `.venv` / `venv`; if nothing matches, a one-time notification
  offers to copy the `pip install "stata-code[mcp]"` command or open the
  install docs. "Don't show again" pins the dismissal to the installed
  extension version. Backed by a new pure module `serverProbe.ts` with
  unit tests.
- **README multi-client section.** Cursor, Claude Desktop, Cline,
  Continue, Windsurf, and Antigravity now have their config-file paths
  spelled out next to the shared `stata-code-mcp` JSON snippet, plus
  guidance for project-venv absolute paths and `uvx` setups.
- **README cell + section reference.** Documents that the VS Code
  extension recognizes both `* %%` Jupyter-style cell markers and
  `**#` … `**######` six-level section headings in `.do` files, and how
  each interacts with the code-lens and Outline view.
- **Open VSX publish step.** `vscode-release.yml` now publishes the VSIX
  to Open VSX on every `vscode-v*` tag. The step is gated on
  `OVSX_PAT` being set at runtime and runs with `continue-on-error: true`,
  so a missing or expired token never blocks the primary VS Code
  Marketplace publish.

### Fixed

- **Subprocess worker JSON protocol hardening.** Worker processes now keep
  their private JSON protocol fds separate from real stdin/stdout and
  redirect the real fd 0/1 pair to `os.devnull` before importing the
  runner. The parent reader also ignores blank protocol noise while still
  failing on non-empty non-JSON output. This prevents a pystata/Stata
  initialization newline from surfacing as
  `adapter_crash: worker emitted non-JSON: '\n'`.

## 0.6.3 — 2026-05-10

### Added

- **`RefNotFound` exception** — `get_log` / `get_graph` / `get_matrix` now
  raise a typed `RefNotFound` (subclass of `KeyError`) carrying the bad
  ref and a stable `kind` token (`unknown_log_ref` / `unknown_graph_ref` /
  `unknown_matrix_ref`). MCP dispatch maps it to a typed error response
  without string-parsing.
- **`NotebookError.kind` / `RunIndexError.kind`** — both classes now
  expose a `kind` property so callers don't have to slice the message
  prefix manually.
- **`SessionPool.list_session_info_detailed`** — partial-failure-aware
  variant of `list_session_info()` that surfaces per-worker `warnings`
  alongside the aggregated `sessions` list. The MCP `list_sessions`
  tool's output schema now includes the optional `warnings` field.
- **`list_runs.requested_limit`** — echoes the original requested limit
  when the server clamps it to `_LIMIT_MAX`, so a caller asking for
  `limit=1000` can distinguish "scan was capped at 500 rows" from "the
  manifest dir really has more than 500 rows".
- **Server capabilities resource now includes prompts.** Reading
  `stata://server/capabilities` returns tools, resource templates, AND
  prompts in a single document — clients no longer need a follow-up
  `list_prompts` round-trip just to enumerate the full surface.

### Changed

- **Test hygiene** — a `tests/conftest.py` snapshot/restores `_refs._store`
  per test and shuts the default subprocess pool down at session end.
  Closes a class of intermittent cross-test failures caused by ref-store
  pollution from earlier MCP / pool tests.
- **Subprocess cancellation race** — `SessionPool.execute()` now
  re-checks `_cancel_pending` after `_get_or_spawn` so a `request_cancel`
  that lands during worker spawn fires on the in-flight call instead of
  the next one.
- **`stata_info` edition casing is now consistent** — the top-level
  `edition` field mirrors `stata.edition` (the enum value, e.g. `MP`)
  verbatim. Previously the pool path lowercased it to `mp` while the
  in-process path emitted `MP`, so the same payload could disagree with
  itself.
- **All MCP tool input schemas now declare `additionalProperties: false`.**
  Typos like `originPath` (camelCase) or misspelled `log_lin_head` are
  now rejected up-front with a typed validation error instead of silently
  producing a wrong run.
- **`cancel_session` and `reset_session` output schemas split.** Each
  tool now advertises only the fields it actually returns; the previous
  shared schema overpromised on one side and underpromised on the other.
- **`list_resources` caps ref-backed entries at 256.** Long-lived
  servers no longer return thousands of stale ref resources from
  forgotten sessions — the most recently used 256 win.
- **Pystata edition init now preserves the full error trail.** When all
  three editions (MP / SE / BE) fail, the `PystataNotAvailable` message
  lists each attempt's error rather than collapsing to the last one.

### Fixed

- **Jupyter kernel `implementation_version`** is now derived from
  `stata_code.__version__` rather than a separate `0.2.0` literal.
- **Jupyter kernel mypy `Invalid base class`** error — the dynamic
  `Kernel if _HAS_IPYKERNEL else object` base class confused mypy.
  Hidden behind a `TYPE_CHECKING` gate now; runtime behavior unchanged.
- **`do_execute` / `do_inspect` signatures** are now LSP-compatible
  with the latest `ipykernel.kernelbase.Kernel` (accepts `cell_meta`,
  `cell_id`, `omit_sections`).
- **Schema `$id` URL** corrected from `stata_code` to `stata-code`
  (matches the actual GitHub repository name).
- **VS Code MCP handshake** version is now read from `package.json`
  via `resolveJsonModule`, removing the former fifth sync site.
  `check_versions.py` still validates the literal form
  when present, so reverting to a hardcoded string fails CI.
- **Release tag glob tightened** — `v[0-9]*.[0-9]*.[0-9]*` instead of
  `v[0-9]*` so stray tags without semver-style dot separators no longer
  trigger the release workflow.
- **Release pipeline now gates on ruff** so a direct push to `main`
  cannot ship un-linted code.
- **Mypy CI now covers `stata_code/mcp` and `stata_code/kernel`** in
  addition to `core`, with the optional `mcp` / `kernel` extras
  installed so `Server`, `Tool`, and `Kernel` symbols resolve.
- **`COMMON_STATA_COMMANDS` deduplicated** — dropped the short forms
  (`cap` / `qui` / `noi` / `mat` / `di`) that were producing
  near-duplicate "did you mean" hits from `difflib`. The long forms
  cover the same fuzzy-match neighbourhood.
- **`_unique_dir` / `_unique_file`** fall back to a UUID suffix after
  998 collisions instead of raising `FileExistsError`. The original
  behaviour blocked the run on conditions that almost always indicate a
  filesystem issue rather than a name clash.
- **Dead code removed** — `_info_payload` (orphaned helper) in the MCP
  server, `_truncate` in `notebook.py`, and the unused `index` / `source`
  parameters on `_ensure_native_id`.

## 0.6.2 — 2026-05-08

Aggregated from the prior `Unreleased` section; covers 0.6.1 and 0.6.2.

### Added

- **Release version guard.** `scripts/check_versions.py` and the CI /
  release workflows now verify that the Python package, MCP server, VS Code
  package, VS Code MCP handshake, and release tag all use the same version.
- **VS Code MCP launch tests.** The extension's server-launch candidate
  builder is now a pure tested module covering local `.venv` / `venv`
  discovery, configured Python interpreters, inline command parsing, and
  environment construction.
- **VS Code packaging smoke test.** The default CI now builds a VSIX artifact
  with the same local `vsce` package command used by release packaging, so
  package-manifest and `.vscodeignore` mistakes fail before release day.
- **VS Code extension bundling.** The extension now bundles its TypeScript
  entrypoint with `esbuild`, excluding `node_modules` and test/build support
  files from the shipped VSIX.
- **VS Code bundled dependency notices.** `THIRD_PARTY_NOTICES.md` now lists
  the npm packages included in the bundled extension output and their license
  terms.
- **PyPI publish verification.** The release workflow now polls PyPI's
  per-version JSON endpoint after the official publish job, so trusted
  publisher failures surface as a failed release run instead of being hidden
  by `continue-on-error`.

### Changed

- **Package-level Python API is subprocess-backed.** `stata_code.run()`,
  `execute()`, and `is_available()` now go through the same worker pool as
  the MCP server, preserving hard timeout behavior and avoiding caller-process
  stdout redirection by `pystata`.
- **CI treats core type errors as blocking.** `mypy stata_code/core` is now a
  hard failure, and the default test workflow also compiles and tests the VS
  Code extension.
- **VS Code npm installs are locked.** The extension now ships
  `package-lock.json`, and CI / release workflows use `npm ci` for reproducible
  dependency installs.
- **VS Code bundle target matches the extension host floor.** The esbuild
  target is `node18`, keeping the published bundle aligned with the current
  `engines.vscode` lower bound.
- **Python 3.13 is in the support matrix.** CI now runs the no-Stata test
  suite on Python 3.13, and package metadata advertises the 3.13 classifier.

### Fixed

- **Cancelled in-flight runs report incomplete logs.** If a live worker is
  killed by `cancel_session`, the synthetic cancelled result now marks
  `log.complete=false`, matching timeout and adapter-crash behavior.

## [0.6.0] — 2026-05-08

### Added

- **Notebook navigation tools.** New MCP tools `notebook_outline` and
  `notebook_get_cell` let agents inspect a `.ipynb` without pulling the whole
  file into context. `notebook_outline` returns a per-cell index (cell id,
  type, source preview, line/char counts, execution_count, has-error flag);
  `notebook_get_cell` returns one cell's full source plus a token-economic
  outputs summary (head/tail of stream/text outputs, error ename/evalue,
  truncated traceback, image presence flag). Cell identity follows nbformat
  4.5+; pre-4.5 cells get a synthesised `synth-<index>-<hash>` id flagged via
  `id_synthesized`. Read-only.
- **Notebook search.** `notebook_locate` finds cells by literal `snippet`
  (whitespace-tolerant fallback), `regex` (Python regex, multiline), or
  pasted `error_text` (longest code-like line is used as a fingerprint).
  Returns ranked candidates with `cell_id`, `line_in_cell`, and a small
  preview. Read-only.
- **Atomic notebook edits.** New `notebook_edit_cell`,
  `notebook_insert_cell`, and `notebook_delete_cell` mutate cells via a
  temp-file + `os.replace` write so the on-disk `.ipynb` is never partially
  written. Edits preserve `cell.id` and metadata; for code cells they clear
  outputs and `execution_count`. Optional `expected_source` is an
  optimistic-concurrency guard that fails with `edit_source_drift` /
  `delete_source_drift` if the user changed the cell between the agent's
  read and write. Insertion against a pre-4.5 synth id auto-upgrades the
  anchor to a real UUID so its id stays valid after the index shift.
- **Origin echo on `RunResult`.** New optional `origin_cell_id` input
  joins the existing `origin_path` / `origin_kind` / `origin_label` and
  is round-tripped on `result.origin` plus the run-bundle manifest. The
  execution path stays cell-agnostic: the runner does not interpret these
  fields, only forwards them, so agents can correlate `stata_run` calls
  with notebook cells without the protocol becoming notebook-aware.
- **Run-bundle index.** New MCP tool `list_runs` queries the on-disk
  `manifest.json` files written by `persist_log_files=true` runs. Filters
  compose: `cell_id`, `origin_path`, `session_id`, `ok`, `since` (ISO 8601
  UTC, lexicographic compare, inclusive). Returns newest-first compact
  summaries with `directory`, `manifest_path`, and `log_path` so callers
  read the full manifest from disk if needed. Read-only.
- **Notebook MCP prompts.** New `run_notebook_cell_and_report` and
  `fix_and_rerun_notebook_cell` prompts wire the per-cell repair recipe
  (read → run with `origin_cell_id` → on failure, edit with
  `expected_source` guard → rerun → recommend kernel restart after the
  retry budget is exhausted) so users can `/mcp prompts` it directly.
- **Capability advertising.** `stata_info.capabilities` now lists
  `notebook_navigation`, `notebook_search`, `notebook_edit`, `run_index`,
  and `origin_echo` so clients can feature-detect the Phase 1-3 surface.
- **Schema-level mutex constraints.** `notebook_locate` and
  `notebook_insert_cell` inputSchemas now use `oneOf` to express the
  "exactly one of snippet / regex / error_text" and "exactly one anchor"
  rules so strict MCP clients catch them before dispatch.
- **VSCode language layer.** The extension now ships Stata TextMate syntax
  highlighting and language configuration, plus an Outline provider for
  `**#` hierarchical sections and `program define` blocks.
- **VSCode section ergonomics.** `Stata: Run Current Section`,
  `Cmd/Ctrl+Shift+Enter`, and `▶ Run Section` code lenses run from the
  current heading to the next equal/higher heading. Existing `Cmd/Ctrl+Enter`
  also runs a section when the cursor is on a section heading.
- **VSCode editing aids.** Added Stata command/function/variable completion,
  configured custom command completions, conservative F2 variable rename
  (skips line/block comments, string literals, and ``` `macro' ``` references),
  `Stata: Open Help for Selection`, and `Stata: Insert Line Continuation`
  for `///` blocks.
- **Runtime discovery.** `pystata` discovery now honors
  `STATA_CODE_PYSTATA_PATH`, `PYSTATA_PATH`, `STATA_HOME`, `STATA_PATH`,
  and `STATA_CLI`, and includes Stata 19 / StataNow default locations.
- **Capability advertising.** `stata_info` now lists `subprocess_timeout`
  alongside the existing capability strings so clients can detect that the
  server isolates Stata in a worker process and enforces hard wall-clock
  timeouts.
- **Agent-native MCP surface.** The MCP server now advertises tool
  `outputSchema` metadata, returns `structuredContent` alongside JSON text for
  compatibility, exposes RunResult/log/graph/matrix/session resources, and
  ships workflow prompts for validation, debugging, repair loops, replication
  audits, and estimation summaries.

### Changed

- **`stata_info` payload is richer.** It now returns a nested `stata`
  object with version/edition/backend plus the supported capabilities list,
  while retaining the older flat aliases for compatibility. Operational
  failures (worker timeout / crash) now report `available: false` together
  with an `error` field so callers can tell them apart from genuine
  "Stata not installed".
- **Jupyter completions inspect live context.** The kernel now completes
  variables from the last result's dataset and `do_inspect` reports variable
  type/label metadata when available.
- **`_summarise_outputs` is now streaming.** A cell with many large stream
  outputs no longer materialises the full concatenation in memory before
  truncating to 4 KB; we accumulate `text_chars_total` as we go but stop
  appending to `text_preview` once the budget is hit.

### Fixed

- **`_pool._utc_iso_ms` race across the second boundary.** The fallback
  pool helper that builds `started_at` for synthetic timeout / crash
  results called `datetime.now()` twice; if the two calls straddled a
  second boundary it could produce timestamps like `T23:59:59.000Z`
  (correct seconds, wrong milliseconds) and silently break lexicographic
  compare in `list_runs`'s `since` filter. Captured `now` once.
- **`limit=True` accepted as `limit=1` in `list_runs`.** Python booleans
  are a subclass of `int`; the `isinstance(limit, int)` check was passing
  through `True` / `False`. Both `list_runs` and the MCP dispatcher now
  reject `bool` explicitly with `limit_invalid`.

## [0.5.0] — 2026-05-08

### Added

- **Bundled Jupyter kernel logos.** `stata-code-kernel install --user`
  now copies `stata_code/kernel/assets/{logo-32x32.png,logo-64x64.png,
  logo-svg.svg}` into the kernelspec source dir before
  `KernelSpecManager().install_kernel_spec` runs. VS Code's Jupyter
  extension filters out kernelspecs that lack logo files, so prior
  releases were invisible in its kernel picker; v0.5 fixes that without
  affecting JupyterLab or classic Jupyter (which both already worked).
- **TestPyPI publishing step in `release.yml`.** Tag `v*` now publishes
  to TestPyPI (via OIDC trusted publishing, environment `testpypi`)
  before publishing to PyPI proper. `continue-on-error: true` keeps
  PyPI + GitHub Release on the happy path even when TestPyPI is
  misconfigured. Setup mirrors the PyPI trusted publisher and is
  documented in [CLAUDE.md](CLAUDE.md).

### Changed

- **`stata_run` tool description and README** clarify the boundary
  between non-mutating execution and the optional agent "fix and
  rerun" repair loop. The tool itself never rewrites your `.do` file
  — but the submitted Stata code can still produce logs, graphs, and
  output files as usual. Repair loops require explicit user opt-in;
  failed runs are diagnostics first, not automatic rewrite permission.
- **VSCode MCP-client handshake version aligned to 0.5.0** (was a
  stale 0.3.2 since the v0.3.2 release).

### Fixed

- **`install_kernel` no longer `.resolve()`s `sys.executable`.** On
  macOS Homebrew venvs (and other layouts that use a `python` symlink
  outside the venv's `bin/` to a Cellar-style real interpreter),
  resolving the symlink pointed Jupyter at an interpreter that
  couldn't import `stata_code`. The kernelspec now keeps the
  unresolved `sys.executable`, so the venv's `python` (with
  `stata_code` on its `sys.path`) launches the kernel.

## [0.4.0] — 2026-05-07

### Added

- **Persistent per-run log bundles.** When a `.do` file path is supplied as
  `origin_path`, the runner writes an immutable `log-files/<run>/` directory
  next to the source file containing:
  - `<run>.log` and `<run>.smcl` — Stata's textual and SMCL logs
  - `manifest.json` — run metadata (elapsed_ms, rc, session, Stata edition)
  - `submitted.do` — a snapshot of the code that was executed
  - `graphs/` — captured graph files materialized from graph refs
  - `outputs/` — newly created or modified table/export files copied from
    the run's working directory

  The directory name encodes UTC timestamp, session, and request IDs so
  parallel runs and reruns are never ambiguous.

- **Working-directory defaults from `origin_path`.** Before running,
  Stata `cd`s to the `.do` file's parent so relative `graph export`,
  `putexcel`, `esttab using`, `collect export`, etc. output next to the
  source. Toggle with `use_origin_workdir` / `useDoFileDirectory` setting.
  Explicit `working_dir` overrides this.

- **Schema extensions.** `LogInfo.files` (`LogFileInfo`) carries the
  bundle paths and derived `graphs_dir`/`outputs_dir`; `GraphInfo.file_path`
  records where a graph was materialized; two new capabilities
  `log_files` and `run_artifacts` signal support.

- **MCP tool options.** `stata_run` gains `persist_log_files`,
  `persist_generated_files`, `origin_path`, `origin_kind`,
  `origin_label`, `use_origin_workdir`, `working_dir`.

- **VS Code settings.** Three new configuration options:
  `stataCode.persistLogFiles` (default `true`),
  `stataCode.persistGeneratedFiles` (default `true`),
  `stataCode.useDoFileDirectory` (default `true`).

- **VS Code tree views.** The Last Result tree now shows "saved" and
  "N outputs" badges on the log node when artifacts are present; the
  output log header prints `working_dir:`, `log_file:`, `smcl_file:`,
  `graphs_dir:`, `outputs_dir:` for each run.

### Changed

- **VSCode MCP startup.** The extension now expands common macOS Python
  script directories before spawning `stata-code-mcp`, tries workspace
  `.venv` and `python -m stata_code.mcp` fallbacks for the default command,
  and writes child-process stderr to the `stata-code` output channel so
  missing PATH / missing dependency failures are actionable.
- **VSCode toolbar ordering.** Run-all and run-selection now share the same
  ordinary `editor/title` toolbar sequence, with ordering moved later in the
  `navigation` group to reduce interleaving from other extensions.

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

- **MCP server tool count is now 8** (added `get_matrix`,
  `cancel_session`).

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
