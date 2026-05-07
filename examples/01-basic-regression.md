# 01 — Basic regression (Hello World)

> **Goal:** show what a single `stata_run` MCP tool call returns for a textbook OLS, and how the default token-economy choices compare to a "dump-everything" server.

## Setup

No prior state. The agent calls `stata_run` cold; `session_id` defaults to `"main"`.

## Walkthrough

### Turn 1: load `auto.dta` and run the regression

**Agent calls:**

```json
{
  "tool": "stata_run",
  "arguments": {
    "code": "sysuse auto, clear\nregress mpg weight"
  }
}
```

**Server returns** (one `TextContent` whose text is the full `RunResult` JSON; abbreviated where marked):

```jsonc
{
  "ok": true,
  "rc": 0,
  "session_id": "main",
  "request_id": "01HXJ2K4Q9V8P3F7N6M5R2T1B0",
  "started_at": "2026-05-07T14:22:08.123Z",
  "elapsed_ms": 234,
  "stata_elapsed_ms": 198,

  "stata": { "version": "18.0", "edition": "MP", "backend": "pystata" },

  "log": {
    "head": "(1978 automobile data)\n\n      Source |       SS           df       MS      Number of obs   =        74\n-------------+----------------------------------   F(1, 72)        =    134.62\n       Model |  1591.9902         1   1591.9902   Prob > F        =    0.0000\n    Residual |  851.469256        72  11.8259619   R-squared       =    0.6515\n-------------+----------------------------------   Adj R-squared   =    0.6467\n       Total |  2443.45946        73  33.4720474   Root MSE        =     3.4389\n",
    "tail": "------------------------------------------------------------------------------\n         mpg | Coefficient  Std. err.      t    P>|t|     [95% conf. interval]\n-------------+----------------------------------------------------------------\n      weight |  -.0060087   .0005179   -11.60   0.000    -.0070411   -.0049763\n       _cons |   39.44028   1.614003    24.44   0.000     36.22283    42.65774\n------------------------------------------------------------------------------\n",
    "lines_total": 42,
    "bytes_total": 2380,
    "truncated": true,
    "complete": true,
    "error_window": null,
    "ref": "log://01HXJ2K4Q9V8P3F7N6M5R2T1B0"
  },

  "results": {
    "r": { "scalars": {}, "macros": {}, "matrices": {} },
    "e": {
      "scalars": {
        "N":        74,
        "df_m":     1,
        "df_r":     72,
        "F":        134.6193,
        "r2":       0.6515,
        "rmse":     3.4389,
        "mss":      1591.9902,
        "rss":      851.4692,
        "r2_a":     0.6467,
        "ll":       -195.2 // ... abbreviated; ~20 scalars typical for `regress`
      },
      "macros": {
        "cmd":          "regress",
        "depvar":       "mpg",
        "model":        "ols",
        "vce":          "ols",
        "title":        "Linear regression",
        "marginsok":    "XB default",
        "predict":      "regres_p"
      },
      "matrices": {
        "b": {
          "rows":   ["mpg"],
          "cols":   ["weight", "_cons"],
          "values": [[-0.0060087, 39.44028]],
          "ref":    null
        },
        "V": {
          "rows":   ["weight", "_cons"],
          "cols":   ["weight", "_cons"],
          "values": [[2.682e-07, -0.000824], [-0.000824, 2.605]],
          "ref":    null
        }
      }
    },
    "last_estimation_cmd": "regress"
  },

  "dataset": {
    "frame": "default",
    "n_obs": 74,
    "n_vars": 12,
    "changed": false,
    "filename": "auto.dta",
    "variables": [
      { "name": "make",     "type": "str18", "label": "Make and model" },
      { "name": "price",    "type": "int",   "label": "Price" },
      { "name": "mpg",      "type": "int",   "label": "Mileage (mpg)" },
      { "name": "weight",   "type": "int",   "label": "Weight (lbs.)" }
      // ... 8 more, all 12 listed in practice (cap is 200)
    ]
  },

  "graphs": [],
  "warnings": [],
  "error": null,

  "schema_version": "1.0",
  "capabilities": ["log_truncation", "graph_ref", "matrix_ref", "multi_session"]
}
```

**What the agent learns** without parsing a single line of human-readable Stata output:

- The run succeeded (`ok: true`, `rc: 0`).
- The estimated model is OLS on `mpg` ~ `weight`, N = 74, R² ≈ 0.6515.
- The slope on `weight` is `-0.0060087` (read from `e.matrices.b.values[0][0]` — a native float, no `display _b[weight]` parse).
- The full log is 42 lines / 2,380 bytes; only head + tail came back inline. To see the rest, the agent calls `get_log("log://01HXJ2K4Q9V8P3F7N6M5R2T1B0")`.

### Turn 2 (optional): user asks "what was the F statistic again?"

The agent already has it — `results.e.scalars.F = 134.6193`. No tool call needed. This is a frequent win versus log-only servers, where the agent would have to re-parse the regression header to recover a single scalar.

### Turn 3 (optional): user asks "show me the full regression output"

The agent fetches the full log instead of re-running the regression.

**Agent calls:**

```json
{
  "tool": "get_log",
  "arguments": { "ref": "log://01HXJ2K4Q9V8P3F7N6M5R2T1B0" }
}
```

**Server returns:**

```json
{
  "text":        "<full 2,380-byte log here>",
  "lines_total": 42,
  "bytes_total": 2380
}
```

The full log only enters the agent's context window when the agent (or user) asked for it.

## Why this is agent-native

- `e.scalars` and `e.matrices` are **native JSON numbers and arrays**. The agent never `parseFloat`s a Stata display string.
- The default response carries **summaries plus references**, not full payloads. The full log is one tool call away when needed.
- `last_estimation_cmd: "regress"` is a top-level convenience field — the agent doesn't have to dig into `e.macros.cmd` for the common case.
- `dataset.variables` lets the agent reason about the active frame for the *next* call (e.g., "the user asked about `mileage`; I see `mpg` in the variable list, that's likely it") without an extra `describe`.

## Token economy

Token counts are estimates using `~4 chars per token`. Numbers come from a typical `regress mpg weight` on `auto.dta`.

|                       | `stata_code` default                                            | typical "dump-everything" MCP server                          | savings       |
| --------------------- | --------------------------------------------------------------- | ------------------------------------------------------------- | ------------- |
| Log payload           | 20-line head + 20-line tail ≈ 1,400 chars / **~350 tokens**     | full 2,380-char transcript / **~600 tokens**                  | ~250 tokens   |
| Coefficients access   | `e.matrices.b.values[0][0]` → `-0.0060087` (no extra call)      | parse log line `"weight  | -.0060087   .0005179 ..."`         | + reliability |
| Scalars (R², F, RMSE) | already in `e.scalars` (1 JSON dict, **~60 tokens**)            | not present — agent must run `display e(r2)` ... per scalar   | 1+ extra call |
| Graphs                | `graphs: []` (nothing produced)                                 | `graphs: []` (same)                                           | tied          |
| **Per-call total**    | **~700 tokens** (envelope + log preview + structured returns)   | **~1,400 – 2,000 tokens** (full log + ad-hoc display lines)   | **~2× saved** |

The `log_truncation` win on a 42-line `regress` output is modest. On a long `bayes:` block or a `tabstat` over many groups (300–600 lines is common), the same default returns ~600 tokens vs. ~6,000 — that is where the headline "~10× smaller" claim earns its keep.
