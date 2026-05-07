# 04 — Multi-session: two analyses in parallel

> **Goal:** show how `session_id` lets an agent juggle two independent datasets without one clobbering the other.

## Setup

The agent is comparing two datasets — Card-Krueger's NJ panel (`study_a`) and a placebo PA-only panel (`study_b`) — and wants to keep their state isolated. Under the hood, `stata_code` maps `session_id` to a Stata **frame**: `session_id="main"` is the master `default` frame; any other id creates (or routes to) a same-named frame. Frames isolate **data**; `r()` / `e()` are global to Stata so the *last* call's estimation results are what `results.e` reflects.

## Walkthrough

### Turn 1: load NJ data into `study_a`

**Agent calls:**

```json
{
  "tool": "stata_run",
  "arguments": {
    "code": "use ck_nj.dta, clear\ngen post = wave == 1\ncount if state == 1 & post == 1",
    "session_id": "study_a"
  }
}
```

**Server returns** (abbreviated):

```jsonc
{
  "ok": true, "rc": 0,
  "session_id": "study_a",
  "request_id": "01HX...A1",
  "results": {
    "r": { "scalars": { "N": 410 }, "macros": {}, "matrices": {} },
    "e": { "scalars": {}, "macros": {}, "matrices": {} },
    "last_estimation_cmd": null
  },
  "dataset": {
    "frame":    "study_a",
    "n_obs":    820,
    "n_vars":   5,
    "changed":  true,
    "filename": "ck_nj.dta",
    "variables": [ /* ... */ ]
  },
  "graphs": [], "warnings": [], "error": null
}
```

Note `dataset.frame: "study_a"` — that's the Stata frame name, equal here to the session id.

### Turn 2: load PA data into `study_b` (independently)

**Agent calls:**

```json
{
  "tool": "stata_run",
  "arguments": {
    "code": "use ck_pa.dta, clear\ngen post = wave == 1\ncount if post == 1",
    "session_id": "study_b"
  }
}
```

**Server returns:**

```jsonc
{
  "ok": true, "rc": 0,
  "session_id": "study_b",
  "results": { "r": { "scalars": { "N": 220 }, "macros": {}, "matrices": {} },
               "e": { "scalars": {}, "macros": {}, "matrices": {} },
               "last_estimation_cmd": null },
  "dataset": { "frame": "study_b", "n_obs": 440, "n_vars": 5,
               "changed": true, "filename": "ck_pa.dta",
               "variables": [ /* ... */ ] },
  "graphs": [], "warnings": [], "error": null
}
```

`study_a`'s data is untouched. The `use ... clear` here cleared `study_b`'s frame, not `study_a`'s.

### Turn 3: run a regression on `study_a` (state preserved across calls)

**Agent calls:**

```json
{
  "tool": "stata_run",
  "arguments": {
    "code": "regress emp i.post if state == 1",
    "session_id": "study_a"
  }
}
```

**Server returns:**

```jsonc
{
  "ok": true, "rc": 0,
  "session_id": "study_a",
  "results": {
    "r": { "scalars": {}, "macros": {}, "matrices": {} },
    "e": {
      "scalars": { "N": 410, "r2": 0.034, "F": 14.7 },
      "macros":  { "cmd": "regress", "depvar": "emp" },
      "matrices": {
        "b": { "rows": ["emp"], "cols": ["1.post","_cons"],
               "values": [[ 0.59, 20.4 ]], "ref": null }
      }
    },
    "last_estimation_cmd": "regress"
  },
  "dataset": { "frame": "study_a", "n_obs": 820, "n_vars": 5,
               "changed": false, "filename": "ck_nj.dta",
               "variables": [ /* ... */ ] }
  // ...
}
```

The `gen post = wave == 1` from turn 1 is still in scope inside `study_a`. No reload required.

### Turn 4: enumerate live sessions

**Agent calls:**

```json
{
  "tool": "list_sessions",
  "arguments": {}
}
```

**Server returns** (the wire format is `TextContent` whose text is the JSON below):

```jsonc
[
  { "session_id": "main",    "frame": "default",  "n_obs": 0   },
  { "session_id": "study_a", "frame": "study_a",  "n_obs": 820 },
  { "session_id": "study_b", "frame": "study_b",  "n_obs": 440 }
]
```

`main` is always present (it's the `default` frame). `study_a` and `study_b` show up because the prior calls created them.

### Turn 5: tear down `study_b` when done

**Agent calls:**

```json
{
  "tool": "reset_session",
  "arguments": { "session_id": "study_b" }
}
```

**Server returns** a `RunResult` representing the cleared state. The Stata frame `study_b` is dropped; any `graph://` / `log://` / `matrix://` refs that were scoped to `study_b` are invalidated.

A subsequent `list_sessions` would show only `main` and `study_a`.

## Why this is agent-native

- A single agent can drive **two analyses in parallel** without resorting to spinning up a second MCP server process.
- Frame isolation is what `stata_code` exposes; the agent doesn't have to know the `frame put` / `frame change` Stata commands. The session id *is* the frame name.
- `list_sessions` is enough to recover after a process restart on the agent side — the host can re-bind known sessions without the user re-loading data.
- `reset_session` is a clean teardown — both data and refs go away in one call. (Refs are connection-scoped anyway, per SCHEMA.md §3.3.)

## Caveats (worth surfacing to the agent / user)

- **`r()` and `e()` are global**, not per-frame. After turn 3, calling `display e(r2)` from a `session_id="study_b"` request would still see `study_a`'s `r2 = 0.034`. The schema mitigates this by always returning `results.e` *as captured immediately after the call's last command*, so the agent should rely on that snapshot rather than re-querying `e()` later.
- **`session_id` allowed pattern**: `[A-Za-z0-9_-]+`. Colons are reserved for v2 remote-prefixing.
- **`reset_session("main")`** does an in-place `clear all` (the master frame can't be dropped); other ids drop the frame outright.

## Token economy

Multi-session itself isn't a per-call token saver — the win is **avoiding load/save thrash**. Without `session_id`, an agent comparing two datasets would have to:

1. Save state A to a temp `.dta` (1 round trip)
2. Load state B (1 round trip)
3. Do the analysis on B
4. Reload state A (1 round trip)
5. Do the analysis on A

…producing five `stata_run` calls, each with their own log/dataset envelope (~500 tokens per call ≈ 2,500 tokens of just plumbing). With `session_id`, the same analysis is **two calls**, ~1,000 tokens of plumbing — and zero risk that an `if state==1` filter from one analysis leaks into the other.

| Workflow                          | calls without `session_id` | calls with `session_id` | plumbing tokens saved (estimate) |
| --------------------------------- | -------------------------- | ----------------------- | -------------------------------- |
| Two-dataset comparison, 1 step ea.| 5                          | 2                       | ~1,500 tokens                    |
| Two-dataset comparison, 5 steps ea.| 13                        | 10                      | ~1,500 tokens (constant)         |
| Above + accidental data clobber   | + 1 debugging round trip   | n/a                     | ~500 tokens + frustration         |
