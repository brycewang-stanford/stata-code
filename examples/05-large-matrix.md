# 05 — Large matrices: `matrix://` refs

> **Goal:** show the `matrix://` ref pattern (landed in v0.2, commit `d7bc2ac`): when a Stata matrix exceeds the inline cell cap (default 10,000 cells), the producer drops `values` and emits a ref instead. The agent fetches values via `get_matrix(ref)` only when it actually needs them.

## Setup

A wide dataset with 150 numeric variables (`var1`–`var150`). Could be a survey instrument, a battery of biomarkers, or simulated factor exposures. The agent wants the full correlation matrix.

```text
correlate var1-var150  →  e(C) is 150×150 = 22,500 cells
                          22,500 > MATRIX_INLINE_CELL_CAP (10,000)
                          → values: null, ref: "matrix://..."
```

## Walkthrough

### Turn 1: run the correlation

**Agent calls:**

```json
{
  "tool": "stata_run",
  "arguments": {
    "code": "use wide_panel.dta, clear\ncorrelate var1-var150"
  }
}
```

**Server returns** (the matrix-handling lines are what matter):

```jsonc
{
  "ok": true, "rc": 0,
  "session_id": "main",
  "request_id": "01HX...M9",
  "log": {
    "head": "(correlation matrix, 150 vars)\n             | var1   var2   ...\n        var1 | 1.0000\n        var2 | 0.123  1.0000\n...",
    "tail": "       var149 |  ... 1.0000\n       var150 |  ... 0.045  1.0000\n",
    "lines_total": 162,
    "bytes_total": 24800,
    "truncated": true,
    "complete": true,
    "error_window": null,
    "ref": "log://01HX...M9"
  },
  "results": {
    "r": {
      "scalars": { "N": 5000 },
      "macros":  {},
      "matrices": {
        "C": {
          "rows":   ["var1", "var2", "var3", /* ... */ "var150"],
          "cols":   ["var1", "var2", "var3", /* ... */ "var150"],
          "values": null,
          "ref":    "matrix://01HX...M9/r/C"
        }
      }
    },
    "e": { "scalars": {}, "macros": {}, "matrices": {} },
    "last_estimation_cmd": null
  },
  "dataset": { "frame": "default", "n_obs": 5000, "n_vars": 150,
               "changed": false, "filename": "wide_panel.dta",
               "variables": [ /* truncated to 200 entries */ ] },
  "graphs": [], "warnings": [], "error": null,
  "schema_version": "1.0",
  "capabilities": ["log_truncation","graph_ref","matrix_ref","multi_session"]
}
```

Two things to notice:

- `results.r.matrices.C.values` is `null`.
- `results.r.matrices.C.ref` is `matrix://01HX...M9/r/C`.

The ref encodes the request id, the slot (`r` for `r()`, `e` for `e()`), and the matrix name. `rows` and `cols` **are still inlined** — they're cheap (300 short strings), and the agent typically wants axis labels for plotting / labelling even when it doesn't pull every cell.

### Turn 2 (one path): user asks "what's the strongest correlation in row `var17`?"

The agent **doesn't need the full matrix** — it can ask Stata directly:

```json
{
  "tool": "stata_run",
  "arguments": { "code": "matrix list r(C), format(%6.3f) noheader" }
}
```

…or ask for a specific row via `mata`. But the cleanest path: fetch only the row(s) of interest by re-issuing a more targeted `correlate var17 var1-var150`. The **point** is that the agent didn't have to push 22,500 floats through its context to answer a row-level question.

### Turn 2 (other path): user asks for the full matrix (heatmap, export, etc.)

**Agent calls:**

```json
{
  "tool": "get_matrix",
  "arguments": { "ref": "matrix://01HX...M9/r/C" }
}
```

**Server returns:**

```jsonc
{
  "rows":   ["var1", "var2", /* ... */ "var150"],
  "cols":   ["var1", "var2", /* ... */ "var150"],
  "values": [
    [1.000, 0.123, 0.045, /* ... */],
    [0.123, 1.000, 0.211, /* ... */],
    /* ... 148 more rows ... */
    [/* ... */, 0.045, 1.000]
  ]
}
```

The full 22,500-cell matrix arrives **only** when the agent decided it actually wanted it.

## Why this is agent-native

- The producer's threshold (`MATRIX_INLINE_CELL_CAP = 10000`, configurable) is a clean rule: small matrices like `e.matrices.b` and `e.matrices.V` for `regress mpg weight` (2×2) come back inline; only the heavy ones turn into refs.
- The schema **forbids** `values: null` and `ref: null` together — if `values` is absent, there's always a ref (SCHEMA.md §3.4). The agent doesn't have to handle a "lost matrix" case.
- `rows` and `cols` are always inline — the agent can already display "the correlation matrix is 150×150 over `var1`–`var150`" without fetching.
- Pairs naturally with `get_log(ref)` and `get_graph(ref)`: same lazy-fetch pattern, three different payload types, identical agent ergonomics.

## Token economy

A single double formatted as `%6.3f` (e.g. `"-0.123"`) is ~6 chars; the JSON wrapping adds 1–2 more per cell. Estimate ~8 chars per inlined cell.

| Matrix size | Inlined cells | Inlined chars (est.) | Inlined tokens (~4 chars/token, est.) |
| ----------- | ------------- | -------------------- | ------------------------------------- |
|   50 × 50   |     2,500     |        20,000        |          **~5,000 tokens**            |
|  100 × 100  |    10,000     |        80,000        |         **~20,000 tokens**            |
|  150 × 150  |    22,500     |       180,000        |         **~45,000 tokens**            |
|  300 × 300  |    90,000     |       720,000        |        **~180,000 tokens**            |

The 10,000-cell cap roughly corresponds to ~20k tokens — the elbow where one matrix would otherwise dominate the agent's window. Beyond it, returning a `matrix://` ref keeps the per-call envelope under ~1,500 tokens regardless of matrix size. The agent then pays the full cost **once, deliberately**, via `get_matrix(ref)`.

For the 150 × 150 case in this example: ~45,000 tokens saved per call when the agent doesn't need the full matrix; same 45,000 tokens spent when it does — but spent on its own terms, not by default.
