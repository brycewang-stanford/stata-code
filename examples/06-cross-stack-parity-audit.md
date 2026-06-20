# 06 — Cross-stack parity audit

> **Goal:** show how an agent should use `stata-code` for the Stata leg of a
> Stata/R/Python robustness audit without pretending that one tool owns every
> runtime.

This example is intentionally protocol-first. The exact R/Python calls depend
on which external MCP servers or local runtimes the user has installed. The
Stata leg is concrete and traceable through `stata_run`.

## Step 1: freeze the common sample

**Agent calls:**

```json
{
  "tool": "stata_run",
  "arguments": {
    "code": "use data/panel.dta, clear\negen unit_id = group(firm_id), label\negen time_id = group(year), label\ngen byte audit_sample = !missing(y, first_treat, unit_id, time_id, x1, x2)\nkeep if audit_sample\nisid unit_id time_id\ncompress\ndatasignature set, reset\nsave data/derived/parity_sample.dta, replace\nexport delimited using data/derived/parity_sample.csv, replace",
    "origin_path": "/abs/project/analysis/00_freeze_parity_sample.do",
    "origin_kind": "file",
    "persist_log_files": true
  }
}
```

**Agent reads:**

- `ok`, `rc`, and any typed error.
- `dataset.n_obs` and `dataset.n_vars`.
- `log.files.directory` for the run bundle.
- generated files copied into `outputs/` when persistence is enabled.

The CSV is the handoff file for R/Python tools. The DTA is the Stata source for
the Stata estimators. Do not let every package define its own missing-value
sample.

## Step 2: run the Stata estimator

**Agent calls:**

```json
{
  "tool": "stata_run",
  "arguments": {
    "code": "use data/derived/parity_sample.dta, clear\ncsdid y x1 x2, ivar(unit_id) time(time_id) gvar(first_treat) method(dripw)\nestat simple\nestat event\ncsdid_plot",
    "session_id": "stata_csdid",
    "origin_path": "/abs/project/analysis/01_stata_csdid.do",
    "origin_kind": "file",
    "persist_log_files": true
  }
}
```

**Agent reads:**

- `results.e.scalars` for `N` and available fit/ATT scalars.
- `results.e.matrices` for coefficient and VCE payloads.
- `graphs[0].ref` for the event-study plot.
- `warnings` and `log.error_window` for dropped cohorts or estimator refusal.

If `csdid` is missing, the repair loop may call:

```json
{"tool": "install_package", "arguments": {"name": "csdid"}}
```

and, if needed:

```json
{"tool": "install_package", "arguments": {"name": "drdid"}}
```

## Step 3: run external legs with their own tools

The agent should hand `data/derived/parity_sample.csv` plus the written parity
contract to the R/Python tools that are actually available. `stata-code` should
not claim those estimates. It should record their package versions, options,
sample `N`, warnings/refusals, and output files in the comparison table.

## Step 4: compare only like with like

| Stack | Package | Target | N | Estimate | SE | Warning/refusal |
| --- | --- | --- | ---: | ---: | ---: | --- |
| Stata | `csdid` | overall ATT from `estat simple` | from `results.e` | from `e(b)`/scalar | from `e(V)` | from `warnings` |
| R | external | same target | external | external | external | external |
| Python | external | same target | external | external | external | external |

Do not compare an overall ATT to an event-time coefficient. Do not hide package
refusals. If sample `N` differs, stop and fix the sample before interpreting
coefficient differences.

## Step 5: report conservatively

Use language like:

- "The Stata `csdid` leg ran on the frozen sample and produced ..."
- "The R/Python legs were run by external tools; stata-code only coordinated the
  handoff and Stata audit trail."
- "The estimates agree within the predeclared tolerance" or "they diverge, with
  the likely source being sample/default/failure differences."
