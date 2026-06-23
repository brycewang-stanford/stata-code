# Structured results — branch on typed fields, not log prose

`stata-code` returns a typed `RunResult`. The whole point is that you **never
parse the log** to learn what happened: success, errors, coefficients, and
recovery advice are all structured fields. This file is the consumer's guide to
the agent-native parts most people otherwise re-derive by hand.

Read this when: interpreting an estimation result, deciding how to react to an
error, or assembling a reproducible / replication artifact.

## 1. `results.estimation` — the typed coefficient table

After any estimation command, `RunResult.results.estimation` is a typed table
(`null` for non-estimation runs). Prefer it over reading `e(b)` / the log.

```jsonc
"estimation": {
  "command": "ivreghdfe",
  "command_family": "iv",          // ols | iv | gmm | panel | count | binary | limited | did
  "depvar": "lnwage",
  "n_obs": 3010,
  "df_resid": 3005,
  "statistic_kind": "t",           // "t" or "z" — what `statistic` holds
  "source": "r_table",             // see below
  "ci_level": 95.0,
  "coefficients": [
    {"term": "educ", "b": 0.107, "se": 0.021, "statistic": 5.1,
     "p_value": 0.000, "ci_low": 0.066, "ci_high": 0.148}
  ],
  "model_stats": {"N": 3010, "r2": 0.31, "F": 42.1},
  "diagnostics": {"weak_id_F": 23.5, "overid_j": 1.8, "overid_j_df": 2}
}
```

- **`source` tells you how much to trust the numbers.**
  - `"r_table"` — copied verbatim from Stata's own `r(table)` (what the command
    *displayed*). Referee-grade; quote these directly.
  - `"e_b_v"` — computed from `e(b)`/`e(V)` because no matching `r(table)` was in
    scope. `se`/`statistic`/`p_value`/CI use a **normal approximation**
    (`statistic_kind` is `"z"`). Fine for point estimates; flag the inference
    method if precision matters.
- **Null cells are honest.** If `e(V)` was unavailable, `se`/`p_value`/CI are
  `null` rather than guessed. Don't invent them.
- **`command_family`** lets you branch on the *kind* of model without a command
  lookup (e.g. apply IV-specific checks when `family == "iv"`).

### `diagnostics` — the identification / spec tests you must report

`diagnostics` surfaces the command-specific `e()` scalars economists are
expected to report. Only scalars actually present are included (never
fabricated). High-value checks:

| family / command | key | what to check |
| --- | --- | --- |
| IV (`ivreg2`, `ivreghdfe`) | `weak_id_F` | weak instruments if ≲ 10 (Stock–Yogo). Report it. |
| IV | `overid_j`, `overid_j_df` | Hansen J overidentification; a low p-value rejects instrument validity. |
| GMM (`xtabond2`) | `ar2_p` | AR(2) must **not** reject (p > 0.10) or the dynamic-panel GMM is invalid. |
| GMM | `hansen_p` | overidentification; beware p ≈ 1.00 (too many instruments). |
| `reghdfe` | `r2_within`, `n_absorbed_fe_dims` | within-R² and how many FE dimensions were absorbed. |
| `xtreg` | `rho` | fraction of variance from the panel effect. |

If a diagnostic you need is absent from `diagnostics`, it wasn't in `e()` — run
the appropriate post-estimation command (e.g. `estat firststage`,
`estat overid`) and read the next result.

## 2. `error.recovery` + `error.rc_label` — what to do on failure

On `ok == false`, beyond `error.kind`, `error.message`, and `error.suggestions`
(human hints), two fields drive your *next move*:

- **`error.rc_label`** — Stata's canonical short message for the rc (e.g. `r(111)`
  → `"variable not found"`). A stable, transcript-independent label to log,
  group, or branch on even when the message is truncated.
- **`error.recovery`** — the machine-readable verdict:

```jsonc
"recovery": {
  "category": "environment",   // user_code | data | model | resource | environment | internal | unknown
  "retriable": true,           // re-running the IDENTICAL code may succeed
  "needs_code_change": false,  // the Stata code must change to succeed
  "needs_user_input": true     // needs a human / out-of-band action
}
```

Decision rule for an autonomous loop:

1. `needs_code_change` → fix the code (use `error.suggestions` as hints), rerun.
   This covers `user_code`, `data`, and `model` categories (syntax, varname,
   not-sorted, no-observations, convergence, …).
2. `retriable` and **not** `needs_code_change` → retry the same code (optionally
   after a short backoff). Covers transient `network` and producer-side
   `timeout` / `adapter_crash`.
3. `needs_user_input` → stop and ask the user. Covers `permission`,
   `file_corrupt`, and `resource` (`out_of_memory`, `stata_limit` → upgrade
   edition / `set maxvar`).
4. `category == "internal"` with `cancelled` → the run was cancelled on purpose;
   do not auto-retry.

Never apply a code fix automatically unless the user asked you to repair and
rerun (see SKILL.md). The full rc → kind table is in `references/error-codes.md`.

## 3. Reproducibility helpers (Python API)

When using the Python package (not the MCP transport), `stata_code` exposes
pure helpers that turn a `RunResult` + the original code into reproducible
artifacts:

- `build_reproducible_do(result, code, seed=...)` → a self-contained `.do` that
  pins `version`, sets `more off`, and re-sets the RNG seed, then runs the code
  verbatim. Hand this to a referee and they can re-run it.
- `extract_package_installs(code)` → the `ssc`/`net install` dependencies the
  script declares (name, source, `from()` URL), de-duplicated.
- `build_provenance(result, seed=..., code=...)` → a typed `Provenance` envelope
  (Stata + stata-code + schema versions, timestamp, estimation command, seed,
  package dependencies).
- `build_submission_package(result, code, seed=..., title=...)` → a dict of
  `{filename: content}` for a replication/journal-submission bundle:
  `analysis.do` + `PROVENANCE.json` + a `README.md` manifest. Write it to a
  directory (or zip) for submission.

```python
from stata_code import run, build_submission_package
r = run(code, persist_log_files=True)
files = build_submission_package(r, code, seed=12345, title="My DiD paper")
for name, content in files.items():
    (out_dir / name).write_text(content)
```

## 4. `verify_dataset` — validate a data-MCP handoff (Python API)

After importing data fetched by an external data MCP (FRED, World Bank, Census),
confirm it matches the provider's metadata before estimating — the executable
companion to `references/data-mcp-handoff.md`:

```python
from stata_code import run, verify_dataset
r = run("import delimited fred_gdp.csv, clear")
check = verify_dataset(
    r.dataset,
    n_obs=312,                       # rows the provider reported
    required_vars=["date", "gdp"],   # columns the analysis needs
)
if not check.ok:
    # check.issues lists each failed expectation, e.g.
    # "expected 312 observations, found 308"
    ...
```

`verify_dataset` checks `n_obs` / `min_obs` / `max_obs`, `n_vars`, and
`required_vars` (the last needs `include_dataset_variables=True`, the default).
A failed check is a hard stop: estimating on the wrong rows is worse than not
estimating.
