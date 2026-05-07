# 02 — Differences-in-differences (Card-Krueger style)

> **Goal:** walk through a multi-turn DiD analysis and show how typed errors let the agent recover from a typo without dragging the user into Stata's English error text.

## Setup

The dataset for this entry is **illustrative**: a synthetic NJ/PA fast-food panel inspired by Card & Krueger (1994). Two waves (Feb 1992 = pre, Nov 1992 = post), two states (NJ = treated, PA = control), one outcome (`emp` = full-time-equivalent employment), one store identifier (`store`).

Assume the file `ck_synth.dta` is on the agent's working directory. Variables:

| name    | type  | description                                       |
| ------- | ----- | ------------------------------------------------- |
| `store` | long  | store id (panel unit)                             |
| `state` | byte  | 0 = PA, 1 = NJ                                    |
| `wave`  | byte  | 0 = Feb 1992, 1 = Nov 1992                        |
| `emp`   | float | full-time-equivalent employees                    |

`reghdfe` is assumed installed (`ssc install reghdfe`).

## Walkthrough

### Turn 1: load and inspect

**Agent calls:**

```json
{
  "tool": "stata_run",
  "arguments": {
    "code": "use ck_synth.dta, clear\ndescribe"
  }
}
```

**Server returns** (abbreviated):

```jsonc
{
  "ok": true, "rc": 0, "session_id": "main",
  "request_id": "01HX...A1",
  "log": {
    "head": "Contains data from ck_synth.dta\n  Observations: 820\n     Variables: 4\n...",
    "tail": "store  long   %12.0g    store id\nstate  byte   %8.0g     0=PA 1=NJ\nwave   byte   %8.0g     0=pre 1=post\nemp    float  %9.0g     FTE employment\n",
    "lines_total": 18, "bytes_total": 720,
    "truncated": false, "ref": null
  },
  "results": { "r": {"scalars": {}, "macros": {}, "matrices": {}},
               "e": {"scalars": {}, "macros": {}, "matrices": {}},
               "last_estimation_cmd": null },
  "dataset": {
    "frame": "default", "n_obs": 820, "n_vars": 4,
    "changed": false, "filename": "ck_synth.dta",
    "variables": [
      {"name":"store","type":"long","label":"store id"},
      {"name":"state","type":"byte","label":"0=PA 1=NJ"},
      {"name":"wave","type":"byte","label":"0=pre 1=post"},
      {"name":"emp","type":"float","label":"FTE employment"}
    ]
  },
  "graphs": [], "warnings": [], "error": null,
  "schema_version": "1.0",
  "capabilities": ["log_truncation","graph_ref","matrix_ref","multi_session"]
}
```

The agent now knows the variables — including that the outcome is `emp`, not `emp_fte`.

### Turn 2: build `post`, `treat`, and an interaction

**Agent calls:**

```json
{
  "tool": "stata_run",
  "arguments": {
    "code": "gen post  = wave == 1\ngen treat = state == 1\nlabel define post 0 \"Pre\" 1 \"Post\"\nlabel values post post\nsummarize post treat"
  }
}
```

**Server returns** (very abbreviated):

```jsonc
{
  "ok": true, "rc": 0,
  "log": {
    "head": ". gen post  = wave == 1\n. gen treat = state == 1\n. summarize post treat\n\n    Variable |  Obs  Mean  ...\n        post |  820  .500\n       treat |  820  .619\n",
    "tail": "", "lines_total": 9, "bytes_total": 290,
    "truncated": false, "ref": null
  },
  "results": {
    "r": {
      "scalars": { "N": 820, "mean": 0.619, "Var": 0.236 },
      "macros": {}, "matrices": {}
    },
    "e": { "scalars": {}, "macros": {}, "matrices": {} },
    "last_estimation_cmd": null
  },
  "dataset": { "frame":"default", "n_obs":820, "n_vars":6, "changed":true,
               "filename":"ck_synth.dta", "variables":[ /* now 6 entries */ ] },
  "graphs": [], "warnings": [], "error": null
}
```

`dataset.changed: true` flags the new variables; `dataset.n_vars` ticked from 4 to 6.

### Turn 3: parallel-trends visual

The agent draws state-by-wave means.

**Agent calls:**

```json
{
  "tool": "stata_run",
  "arguments": {
    "code": "preserve\ncollapse (mean) emp, by(state wave)\ntwoway (line emp wave if state==0, lcolor(navy)) (line emp wave if state==1, lcolor(cranberry)), legend(order(1 \"PA\" 2 \"NJ\")) title(\"Mean FTE employment, by state\") xlabel(0 \"Pre\" 1 \"Post\")\nrestore"
  }
}
```

**Server returns** (the interesting bit):

```jsonc
{
  "ok": true, "rc": 0,
  "log": { /* twoway log lines, ~15 of them */ },
  "results": { "r": {"scalars": {}, "macros": {}, "matrices": {}},
               "e": {"scalars": {}, "macros": {}, "matrices": {}},
               "last_estimation_cmd": null },
  "graphs": [
    {
      "ref":            "graph://01HX...C3/0",
      "name":           "Graph",
      "format":         "png",
      "width":          800,
      "height":         600,
      "source_command": "twoway (line emp wave if state==0, lcolor(navy)) ...",
      "source_line":    3,
      "inline":         null
    }
  ],
  "warnings": [], "error": null
}
```

The agent now knows a graph exists at `graph://01HX...C3/0`. **It does not yet have the bytes** — see [03-graphs.md](./03-graphs.md). It can choose to call `get_graph(ref)` only if the user asks to see the figure.

### Turn 4: DiD regression — typo, and the typed-error recovery

The agent fat-fingers the outcome variable as `emp_fte` (which it half-remembers from Card & Krueger's actual paper, where the variable was called `EMPFT`).

**Agent calls:**

```json
{
  "tool": "stata_run",
  "arguments": {
    "code": "reghdfe emp_fte i.post##i.treat, absorb(store) vce(cluster store)"
  }
}
```

**Server returns** (this is the win — typed error, structured suggestion):

```jsonc
{
  "ok": false,
  "rc": 111,
  "session_id": "main",
  "request_id": "01HX...D4",
  "log": {
    "head": ". reghdfe emp_fte i.post##i.treat, absorb(store) vce(cluster store)\nvariable emp_fte not found\nr(111);",
    "tail": "",
    "lines_total": 3, "bytes_total": 92,
    "truncated": false,
    "error_window": "reghdfe emp_fte i.post##i.treat, absorb(store) vce(cluster store)\nvariable emp_fte not found\nr(111);",
    "ref": null
  },
  "results": { "r": {"scalars": {}, "macros": {}, "matrices": {}},
               "e": {"scalars": {}, "macros": {}, "matrices": {}},
               "last_estimation_cmd": null },
  "dataset": { /* unchanged from turn 2 */ },
  "graphs": [], "warnings": [],

  "error": {
    "kind":              "varname_not_found",
    "rc":                111,
    "rc_label":          "variable not found",
    "message":           "variable emp_fte not found",
    "command":           "reghdfe emp_fte i.post##i.treat, absorb(store) vce(cluster store)",
    "line":              1,
    "context": {
      "before":  [],
      "failing": "reghdfe emp_fte i.post##i.treat, absorb(store) vce(cluster store)",
      "after":   []
    },
    "commands_executed": 0,
    "varname":           "emp_fte",
    "path":              null,
    "name":              null,
    "suggestions": [
      { "action":  "Check the variable name. Did you mean `emp`?",
        "command": "describe" }
    ]
  },

  "schema_version": "1.0",
  "capabilities": ["log_truncation","graph_ref","matrix_ref","multi_session"]
}
```

The agent's host code branches on `error.kind`:

```
if (result.error?.kind === "varname_not_found") {
    // structured: try suggestion[0].action's hint
    retry(result.error.suggestions[0].action);  // "Did you mean `emp`?"
}
```

No regex on `"variable .* not found"`. No re-tokenizing English. The `varname` field is also surfaced separately (`"emp_fte"`) so the agent can compare against `dataset.variables` programmatically.

### Turn 5: DiD regression — corrected

**Agent calls:**

```json
{
  "tool": "stata_run",
  "arguments": {
    "code": "reghdfe emp i.post##i.treat, absorb(store) vce(cluster store)"
  }
}
```

**Server returns** (abbreviated; the DiD coefficient is what matters):

```jsonc
{
  "ok": true, "rc": 0,
  "log": { /* head+tail of reghdfe output, truncated, with ref */ },
  "results": {
    "r": { "scalars": {}, "macros": {}, "matrices": {} },
    "e": {
      "scalars": {
        "N":      820,
        "r2":     0.952,    // includes store FEs
        "r2_a":   0.928,
        "F":      8.41,
        "df_m":   3,
        "N_clust": 410
      },
      "macros": {
        "cmd":      "reghdfe",
        "depvar":   "emp",
        "absvars":  "store",
        "vce":      "cluster",
        "vcetype":  "Robust"
      },
      "matrices": {
        "b": {
          "rows": ["emp"],
          "cols": ["1.post", "1.treat", "1.post#1.treat", "_cons"],
          "values": [[ -2.13, 0.00, 2.75, 23.4 ]],
          "ref":  null
        }
        // V matrix omitted from this view
      }
    },
    "last_estimation_cmd": "reghdfe"
  },
  "graphs": [], "warnings": [], "error": null
}
```

The DiD effect is `e.matrices.b.values[0][2] = 2.75`: a 2.75-FTE bump in NJ relative to PA after the wage hike. (Synthetic dataset; real Card-Krueger numbers differ.)

### Turn 6: marginal effect via `margins`

```json
{
  "tool": "stata_run",
  "arguments": {
    "code": "margins, dydx(treat) at(post=(0 1))"
  }
}
```

The response carries `e()` results from `margins` (which writes to both `r()` and `e()`); the agent can pull point estimates and standard errors directly from `e.matrices.b` and `e.matrices.V`.

## Why this is agent-native

- The agent **never parsed `"variable .* not found"`**. It branched on a stable, documented enum value (`error.kind == "varname_not_found"`).
- `dataset.variables` from turn 1 was reusable context for turn 4's recovery — the agent could have *prevented* the typo by checking the var list, and even after the failure, the suggestion text was structured.
- `last_estimation_cmd` flips between `null`, `"reghdfe"`, and `"margins"` across turns — the agent always knows what `e()` reflects.
- Multi-turn state (the `gen post`, `gen treat` from turn 2) persists in `session_id="main"` without any explicit reattach.

## Token economy

|                                  | `stata_code`                                                | typical "dump-everything" MCP server                                  | savings (estimate, ~4 chars/token) |
| -------------------------------- | ----------------------------------------------------------- | --------------------------------------------------------------------- | ---------------------------------- |
| Turn 4 error response            | typed envelope, ~250 tokens (`error.kind`, `varname`, suggestion) | full traceback + log + 200-line dump of dataset state, ~1,500 tokens | ~1,250 tokens                      |
| Turn 5 `reghdfe` log             | head+tail ~600 tokens, full log ~6,000 tokens behind `log://` ref | full log inline ~6,000 tokens                                  | ~5,400 tokens                      |
| Coefficient retrieval (turn 5)   | `e.matrices.b.values` — no extra call                       | `display _b[1.post#1.treat]` — extra round trip                      | 1 fewer round trip                 |
| Graph in turn 3                  | `graph://` ref, ~120 tokens                                 | inline base64 PNG, ~50,000 tokens (30 KB)                             | ~49,800 tokens                     |
| **6-turn workflow total**        | **~3 – 4k tokens of agent context burned**                  | **~65 – 75k tokens**                                                  | **~15× smaller**                   |
