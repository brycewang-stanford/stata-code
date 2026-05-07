# stata_code Result Schema (v1.0)

> The contract every frontend (core API, MCP server, Jupyter kernel, VSCode extension) must obey.

This document defines the shape of every value returned by `stata_code.run()`, regardless of which Stata backend produced it (`pystata` or console fallback) and which frontend the user is using. The schema is **the** project's load-bearing artifact: if it is right, frontends are thin; if it is wrong, every frontend grows hacks.

This Markdown document is **normative**. Generated artifacts (Pydantic models, JSON Schema, TypeScript types) are derived; when they disagree with this document, the generator is the bug.

---

## 1. Design principles

These principles drive every field choice below. When in doubt, return to them.

1. **Agent-native, not human-native.** The primary consumer is an LLM with a token budget, not a human reading a terminal. Optimize for *parseability* and *token economy* before *prettiness*.

2. **Deterministic over conversational.** Errors are typed (`kind`, `rc`), not English sentences. Status is a boolean (`ok`), not a string to grep. Common error remediations are surfaced as structured `suggestions`, not buried in prose.

3. **Token-efficient by default, full-fidelity on demand.** The default response carries summaries and references; full payloads (long logs, large matrices, graph bytes) are fetched by a follow-up call only when the agent actually wants them.

4. **Native types, not stringified.** Stata scalars are JSON numbers, not strings. Matrices are 2-D arrays with named axes. The agent should never need to `parseFloat`.

5. **Multi-session is first-class.** Every result names the session that produced it. Single-session use just defaults `session_id` to `"main"`.

6. **Stable across backends.** A `pystata` result and a console-fallback result for the same code are structurally identical. Backend differences live behind the schema, not in front of it.

7. **No per-frontend special cases.** The Jupyter kernel and the MCP server transform the *same* result for their respective transports. Neither produces or consumes a different shape.

---

## 2. The envelope

Every successful or failed Stata execution returns one result object:

```json
{
  "ok": true,
  "rc": 0,
  "session_id": "main",
  "request_id": "01HXJ2K4Q9V8P3F7N6M5R2T1B0",
  "started_at": "2026-04-30T14:22:08.123Z",
  "elapsed_ms": 234,
  "stata_elapsed_ms": 198,

  "stata": {
    "version": "18.0",
    "edition": "MP",
    "backend": "pystata"
  },

  "log": {
    "head": "(1 variable, 74 observations)\n...",
    "tail": "       _cons      6.165698   0.5497  ...\n",
    "lines_total": 42,
    "bytes_total": 2380,
    "truncated": true,
    "complete": true,
    "error_window": null,
    "ref": "log://run-7f3a9b"
  },

  "results": {
    "r": {
      "scalars": {"mean": 21.297, "N": 74, "Var": 33.472},
      "macros":  {},
      "matrices": {}
    },
    "e": {
      "scalars": {"N": 74, "df_m": 1, "r2": 0.219},
      "macros":  {"cmd": "regress", "depvar": "mpg"},
      "matrices": {
        "b": {
          "rows": ["mpg"],
          "cols": ["weight", "_cons"],
          "values": [[-0.006, 39.44]],
          "ref": null
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
      {"name": "make",   "type": "str18",  "label": "Make and Model"},
      {"name": "price",  "type": "int",    "label": "Price"},
      {"name": "mpg",    "type": "int",    "label": "Mileage (mpg)"}
    ]
  },

  "graphs": [
    {
      "ref": "graph://7f3a9b/0",
      "name": "Graph",
      "format": "png",
      "width": 800,
      "height": 600,
      "source_command": "scatter price mpg",
      "source_line": 5,
      "inline": null
    }
  ],

  "warnings": [],
  "error": null,

  "schema_version": "1.0",
  "capabilities": ["log_truncation", "graph_ref", "matrix_ref", "multi_session"]
}
```

A failed execution sets `ok: false`, `rc != 0`, and populates `error`:

```json
{
  "ok": false,
  "rc": 111,
  "session_id": "main",
  "request_id": "01HXJ2K4Q9V8P3F7N6M5R2T1B1",
  "started_at": "2026-04-30T14:22:09.456Z",
  "elapsed_ms": 12,
  "stata_elapsed_ms": 8,
  "stata": { "version": "18.0", "edition": "MP", "backend": "pystata" },

  "log": {
    "head": "use auto, clear\nsummarize mpgg\nvariable mpgg not found\nr(111);",
    "tail": "",
    "lines_total": 4,
    "bytes_total": 60,
    "truncated": false,
    "complete": true,
    "error_window": "summarize mpgg\nvariable mpgg not found\nr(111);",
    "ref": null
  },

  "results": { "r": {"scalars": {}, "macros": {}, "matrices": {}},
               "e": {"scalars": {}, "macros": {}, "matrices": {}},
               "last_estimation_cmd": null },

  "dataset": { "frame": "default", "n_obs": 74, "n_vars": 12, "changed": false,
               "filename": "auto.dta", "variables": null },

  "graphs": [],
  "warnings": [],

  "error": {
    "kind": "varname_not_found",
    "rc": 111,
    "rc_label": "variable not found",
    "message": "variable mpgg not found",
    "command": "summarize mpgg",
    "line": 2,
    "context": {
      "before": ["use auto, clear"],
      "failing": "summarize mpgg",
      "after": []
    },
    "commands_executed": 1,
    "varname": "mpgg",
    "path": null,
    "name": null,
    "suggestions": [
      {"action": "Check the variable name. Did you mean `mpg`?",
       "command": "describe"}
    ]
  },

  "schema_version": "1.0",
  "capabilities": ["log_truncation", "graph_ref", "matrix_ref", "multi_session"]
}
```

---

## 3. Field reference

### 3.1 Top-level

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `ok` | `bool` | yes | The authoritative success bit. Producers MUST keep `ok`, `rc`, and `error`-presence consistent. Consumers branch on `ok` first. |
| `rc` | `int` | yes | Stata's `_rc` after the last user-submitted command (after any `capture` masking). `0` on success. Synthetic codes are negative: `-1` adapter crash, `-2` timeout, `-3` cancellation. |
| `session_id` | `string` | yes | Defaults to `"main"`. MUST match `[A-Za-z0-9_:-]+`. The character `:` is reserved for future remote-prefixing (e.g., `host-7:main`), so v1 producers MUST NOT emit colons. |
| `request_id` | `string` | yes | Producer-generated, unique per call. Recommended format: ULID or UUIDv7 (sortable). Consumers use this for log correlation and `ref` lookup. |
| `started_at` | `string` (ISO 8601 UTC) | yes | Timestamp at which the producer began handling the call, e.g. `"2026-04-30T14:22:08.123Z"`. Always UTC, always with millisecond precision. |
| `elapsed_ms` | `int` | yes | Wall-clock duration of the call, in milliseconds. Minimum reported value is `1`; sub-millisecond calls round up. |
| `stata_elapsed_ms` | `int \| null` | yes | Time spent in Stata only (excluding adapter/IPC overhead), when measurable. `null` when the backend cannot separate it. |
| `stata` | `object` | yes | Backend identity; see §3.2. |
| `log` | `object` | yes | Log envelope; see §3.3. |
| `results` | `object` | yes | Stata `r()` and `e()` returns; see §3.4. Always present, may be empty. |
| `dataset` | `object` | yes | Snapshot of the active frame; see §3.5. |
| `graphs` | `array` | yes | Captured graphs; see §3.6. May be empty. |
| `warnings` | `array<Warning>` | yes | Non-fatal advisories. See §3.8. De-duplicated by `(kind, message)`. |
| `error` | `object \| null` | yes | `null` iff `ok: true`. See §3.7. |
| `schema_version` | `string` | yes | Semver-major + minor. v1.0 producers emit `"1.0"`. See §6. |
| `capabilities` | `array<string>` | yes | Optional features the producer supports beyond v1.0 baseline. See §6 for the registry. |

**Producer consistency.** When `ok: true`, the producer MUST set `error: null` and `rc: 0`. When `ok: false`, the producer MUST set `error` to a non-null object whose `rc` equals the top-level `rc`. If a consumer encounters inconsistency, it MUST treat the result as failed.

**Synthetic rcs and `error.rc`.** When `rc < 0` (adapter crash, timeout, cancellation), `error.rc` mirrors that synthetic code. The corresponding `error.kind` is `adapter_crash`, `timeout`, or `cancelled`.

**Numeric encoding.** All JSON numbers in this schema are IEEE-754 doubles. Producers MUST emit them with sufficient precision to roundtrip (typically 17 significant digits for doubles). Consumers MUST treat them as doubles. Stata's system missing (`.`) is encoded as JSON `null`. Stata's *extended* missing values (`.a`–`.z`) are lost in this representation — agents needing them must request via `r(missing_class)` ad-hoc commands. Stata does not emit `Inf`/`NaN` in normal operation; if encountered, producers encode them as `null` and emit a warning of kind `non_finite`.

### 3.2 `stata`

| Field | Type | Notes |
| --- | --- | --- |
| `version` | `string \| null` | E.g. `"18.0"`, `"17.5"`. `null` when the producer cannot determine it. |
| `edition` | `"MP" \| "SE" \| "IC" \| "BE" \| "unknown"` | Stata 17+ shipped `BE` in place of `IC`; both values may be observed depending on which Stata is installed. Agents reasoning about edition limits (e.g., `BE` = 2,048 vars) MUST also check `version`. |
| `backend` | `"pystata" \| "console"` | Which adapter executed the code. Open enum: future backends may add values. |

### 3.3 `log`

The single biggest token-economy decision in the schema. Default response carries `head` + `tail` + `error_window` + a `ref`; the full log is fetched separately if the agent asks.

| Field | Type | Notes |
| --- | --- | --- |
| `head` | `string` | First N lines of the log, where N defaults to 20. ANSI escapes stripped. When `truncated: false`, this contains the entire log. |
| `tail` | `string` | Last N lines. **Empty string when `truncated: false`** (avoiding overlap ambiguity). |
| `lines_total` | `int` | Line count of the full log, after `\r\n → \n` normalization. A trailing empty line is not counted. |
| `bytes_total` | `int` | UTF-8 byte count of the full log *after* ANSI stripping (i.e., the bytes a `get_log(ref)` call would return). |
| `truncated` | `bool` | `true` iff `head` does not contain the entire log. When `true`, the producer MUST also set `ref` and MUST implement `get_log`. |
| `complete` | `bool` | Reserved for v2 streaming. Always `true` in v1. v2 may emit interim results with `complete: false`. |
| `error_window` | `string \| null` | When `error` is non-null, the ~10 log lines immediately surrounding the failing emission (regardless of `head`/`tail` window). Cheap for the producer to compute; saves agents from bumping `log_lines` or fetching the full log just to see "what did Stata say right when it broke." `null` on success or when not computable. |
| `ref` | `string \| null` | Opaque reference for `get_log`. Required when `truncated: true`; may be set when `truncated: false` for caller convenience; `null` is allowed when full log is in `head`. |

**ANSI handling.** All log views (`head`, `tail`, `error_window`, the payload returned by `get_log(ref)`) are ANSI-escape-stripped, consistently.

**Output streams.** In v1, the `log` object captures all backend output text — Stata stdout plus any output from embedded `python:` or `mata:` blocks — concatenated in emission order. v2 may surface streams separately under `log.streams`; producers MUST NOT use that name for any v1 field.

**Ref lifetime.** Refs are valid only within the same client/server connection (or process, for in-process backends). Producers MUST invalidate refs on `reset_session`, process exit, or after a documented TTL. Consumers MUST NOT persist refs across sessions.

**Defaults.** `head=20`, `tail=20`. Configurable per call via `log_lines_head` / `log_lines_tail` (see §4). If `lines_total ≤ head+tail`, the producer MUST set `truncated: false`, place the full log in `head`, set `tail: ""`, and set `ref: null`.

### 3.4 `results`

Stata's `r()` and `e()` return dictionaries, structurally separated. Each follows the same shape:

```json
{
  "scalars":  { "<name>": <number | null>, ... },
  "macros":   { "<name>": "<string>", ... },
  "matrices": { "<name>": Matrix, ... }
}
```

| Sub-field | Type | Notes |
| --- | --- | --- |
| `scalars` | `dict<str, number \| null>` | Native floats / ints. Stata's system missing (`.`) → JSON `null`. Extended missings (`.a`–`.z`) → `null` with information loss. |
| `macros` | `dict<str, string>` | Stata macro values verbatim. |
| `matrices` | `dict<str, Matrix>` | See `Matrix` below. |

**`Matrix` shape:**

```json
{
  "rows":   ["<label>", ...],
  "cols":   ["<label>", ...],
  "values": [[<number | null>, ...], ...],
  "ref":    "matrix://..." | null
}
```

- `values` is row-major: `values.length == rows.length`, every inner array has `cols.length`. Producers MUST NOT flatten.
- For `e(b)`: `cols` are coefficient names, `rows` are equation names. Single-equation models populate `rows` with the depvar name (or Stata's placeholder `"y1"`); multi-equation models (`mlogit`, `sureg`, `gsem`) populate them with real equation names.
- For large matrices, the producer MAY emit `values: null` and `ref: "matrix://..."` to be fetched via `get_matrix(ref)`. Producers SHOULD do this when a matrix would inline more than ~10,000 cells. `values: null` and `ref: null` together are forbidden.

**Top-level convenience field:**

| Field | Type | Notes |
| --- | --- | --- |
| `last_estimation_cmd` | `string \| null` | Mirrors `e(cmd)` for callers who don't want to dig into `e.macros`. After multi-command code, this reflects the *last* command that wrote to `e()`. `null` if no estimation has been performed. |

**Empty is empty.** Sub-dicts are `{}` when Stata returned nothing — never absent, never `null`.

**`e(sample)` and `s()` are intentionally not surfaced** in v1. `e(sample)` is a potentially huge indicator vector, and `s()` is rarely used outside of parser-internal commands. Agents needing them must run explicit Stata commands.

### 3.5 `dataset`

A summary of the active Stata frame *after* the command ran. Always populated.

| Field | Type | Notes |
| --- | --- | --- |
| `frame` | `string` | Active frame name. Stata's master frame is named `"default"`. ⚠ Note this is unrelated to `session_id == "main"`. |
| `n_obs` | `int` | `_N`. |
| `n_vars` | `int` | `c(k)`. |
| `changed` | `bool` | `c(changed)`. ⚠ Stata sets this on *any* dataset-touching command, including no-op replaces — treat as a "may be dirty" hint, not a guarantee. |
| `filename` | `string \| null` | `c(filename)`. `null` if no file backs the frame (e.g., after `clear` or for in-memory frames). |
| `variables` | `array<VariableInfo> \| null` | Variable list with types and labels. May be `null` if `include_dataset_variables: false` was requested or if the frame is empty. |

**`VariableInfo`:**

```json
{ "name": "mpg", "type": "int", "label": "Mileage (mpg)" }
```

`type` is Stata's storage type (`byte`, `int`, `long`, `float`, `double`, `str#`, `strL`). `label` is the variable label string, or `""` if none.

When `n_vars` is large (default cap: 200), the producer truncates `variables` to the first 200 entries and emits a warning of kind `dataset_variables_truncated`. Agents wanting all variables should call `describe` directly.

### 3.6 `graphs`

Each entry describes one captured graph. By default the bytes are **not** inlined; the agent fetches them via `ref`.

| Field | Type | Notes |
| --- | --- | --- |
| `ref` | `string` | The canonical handle. Resolvable via `get_graph(ref)`. Unique within the result. Use array index to refer to "the second graph"; use `ref` for cross-call references. |
| `name` | `string` | Stata's graph name (`graph display Graph` etc.) when known. Not unique within the result — Stata's default name is just `"Graph"`. |
| `format` | `"png" \| "svg" \| "pdf"` | The format actually produced. Producers MUST convert Stata-native `.gph` to one of these at capture time. Default is `"png"`. |
| `width` | `int \| null` | CSS pixels (96 dpi convention). For raster: actual pixel width. For vector (`svg`/`pdf`): width at 1× / nominal. |
| `height` | `int \| null` | CSS pixels, same convention. |
| `source_command` | `string \| null` | The user-submitted command line that produced this graph, when isolatable. |
| `source_line` | `int \| null` | 1-indexed line within the submitted code that produced this graph. |
| `inline` | `string \| null` | Base64-encoded bytes when the caller explicitly asked for inline (`include_graphs: "inline"`); else `null`. |

### 3.7 `error`

Populated iff `ok: false`. The schema's most important contribution to agent UX: a *typed* error with structured remediation hints.

| Field | Type | Notes |
| --- | --- | --- |
| `kind` | `string` | Semantic class. Drawn from the closed enum below. The taxonomy, not the rc, is what an agent should branch on. Consumers MUST treat unrecognized values as `unknown`. |
| `rc` | `int` | The numeric `_rc` (mirrors top-level `rc`). For synthetic codes (`-1`, `-2`, `-3`), set to that value. |
| `rc_label` | `string` | Stata's official short label for that rc when known; else a producer-supplied descriptor. |
| `message` | `string` | Human-readable, single line. Truncated to 4,096 characters; truncation indicator `…` appended if cut. |
| `command` | `string \| null` | The specific command line that failed, if isolatable. Truncated to 1,024 characters. |
| `line` | `int \| null` | 1-indexed line within the *top-level submitted code*. Errors inside nested `do`-files set `line: null` (and `path` to the script if known). |
| `context` | `object` | Surrounding-code window; see below. |
| `commands_executed` | `int \| null` | Number of commands that ran before the failure, if isolatable. The state in `results` and `dataset` reflects this post-failure state, not a pre-failure rollback. |
| `path` | `string \| null` | For `file_*` kinds, the file path at issue. |
| `varname` | `string \| null` | For `varname_not_found` and related, the variable name at issue. |
| `name` | `string \| null` | For `name_conflict` and `invalid_name`, the conflicting/invalid name. |
| `suggestions` | `array<Suggestion>` | Producer-supplied remediation hints. Empty when none apply. See below. |

**`context` shape:**

```json
{
  "before": ["<line>", ...],   // up to 3 lines before the failing command
  "failing": "<line>",         // the failing command itself
  "after":  ["<line>", ...]    // up to 1 line after
}
```

**`Suggestion` shape:**

```json
{
  "action":  "Check the variable name. Did you mean `mpg`?",
  "command": "describe"          // optional concrete command to run, or null
}
```

Suggestions are best-effort; agents should treat them as hints, not directives. The `kind` enum below documents what suggestions are typically populated.

**`kind` enum (v1.0):**

| `kind` | Typical rc(s) | Notes / suggestion seed |
| --- | --- | --- |
| `syntax` | 9, 100, 101, 102, 103, 121, 130, 132, 197, 198 | Generic parser failure. No automatic suggestion. |
| `command_not_found` | 199 | Often resolved by `ssc install` or `net install`; suggestions populated when Stata reports a likely package name. |
| `varname_not_found` | 111 | `varname` populated. Suggestions may include similar varnames from `dataset.variables`. |
| `invalid_name` | 122, 123 | `name` populated. |
| `type_mismatch` | 109, 408 | |
| `name_conflict` | 110 | `name` populated. Suggestion typically: `replace`. |
| `not_sorted` | 119, 459 | Suggestion: `sort <varlist>`. |
| `convergence` | 430 | |
| `infeasible` | 491 | Distinct from convergence: starting values not feasible. |
| `estimation_sample_empty` | 1400, 2000 (in estimation context) | |
| `estimation_failure` | 1401, 1402 | |
| `no_estimation_results` | 301 | Common when calling `predict`/`margins` without prior estimation. |
| `no_observations` | 2000, 2001 | |
| `data_in_memory` | 4 | Suggestion: `clear`. |
| `matrix_singular` | 506, 508 | Matrix not positive definite / not invertible. |
| `matrix_conformability` | 503, 507 | Dimension mismatch. |
| `matrix_missing` | 504 | Matrix has missing values. |
| `file_not_found` | 322, 601 | `path` populated. |
| `file_exists` | 602 | `path` populated. Suggestion: pass `replace` option. |
| `file_corrupt` | 604, 610 | `path` populated. Often "not a Stata file." |
| `file_io` | 603, 691 (local) | `path` populated. Catch-all for open/read/write failures not otherwise classified. |
| `network` | 691 (network), 692, 693 | URL fetches, network reads. |
| `permission` | 608 | `path` populated. Includes Stata-license-limit errors (615/616 family that surface as permission denials). |
| `encoding` | 615, 616 | Unicode / encoding-conversion failures. |
| `stata_limit` | 901, 902, 903 | Edition / matsize / similar Stata-imposed caps. Distinct from OS OOM. Suggestion: `set maxvar` or upgrade edition. |
| `out_of_memory` | 480, 909 | OS-level memory exhaustion. |
| `interrupt` | 1 | User Break / Ctrl-C from a frontend. |
| `cancelled` | (synthetic, future) | Reserved for v2 cooperative cancellation. v1 producers MUST NOT emit. |
| `timeout` | (synthetic `rc: -2`) | Adapter-imposed time limit exceeded. |
| `adapter_crash` | (synthetic `rc: -1`) | Producer-side failure (pystata exception, IPC death). |
| `unknown` | any unmapped rc | Catch-all. Agents fall back to `message`. We aim to shrink this over time. |

The rc-to-kind table is approximate and lives in code (`stata_code.core.errors`), not in this document. Discrepancies between the enum and a specific rc are bugs in the table, not in the schema. New rcs default to `unknown`.

### 3.8 `Warning`

```json
{ "kind": "convergence", "message": "convergence not achieved at iter 100" }
```

| Field | Type | Notes |
| --- | --- | --- |
| `kind` | `string` | Open enum. Common values: `convergence`, `singular`, `boundary`, `omitted_collinear`, `non_finite`, `dataset_variables_truncated`, `unknown`. |
| `message` | `string` | Human-readable, single line. Truncated to 1,024 characters. |

Warnings are de-duplicated by `(kind, message)`.

---

## 4. Request-side options

The schema also dictates what callers may *ask for*. Every frontend exposes the same options under the same names:

| Option | Type | Default | Effect |
| --- | --- | --- | --- |
| `code` | `string` | — | The Stata code to run. |
| `session_id` | `string` | `"main"` | Routes to a named persistent session. Pattern: `[A-Za-z0-9_-]+` (no colons in v1). |
| `log_lines_head` | `int` | `20` | Lines to retain at the start of `log.head`. `0` disables. |
| `log_lines_tail` | `int` | `20` | Lines to retain at the end of `log.tail`. `0` disables. |
| `include_full_log` | `bool` | `false` | If `true`, the full log is placed inline in `log.head` regardless of size; `truncated: false`, `ref: null`. Use when token budget is generous and follow-up calls are expensive. |
| `include_graphs` | `"ref" \| "inline" \| "none"` | `"ref"` | `"none"` skips graph capture entirely (cheapest); `"ref"` captures and returns refs; `"inline"` base64-encodes bytes into `inline`. |
| `graph_format` | `"png" \| "svg" \| "pdf"` | `"png"` | Render format. |
| `include_dataset_variables` | `bool` | `true` | Set `false` to omit `dataset.variables`. |
| `timeout_ms` | `int \| null` | `600000` (10 min) | Hard timeout. `null` disables. On expiry, returns `ok: false`, `error.kind: "timeout"`, `rc: -2`. Frontends MAY override the default if their use case demands. |

Frontends translate their native idiom (MCP `inputSchema`, Jupyter kernel options, VSCode commands) into these names without renaming.

---

## 5. Auxiliary tools (companion calls)

The schema implies a small set of follow-up calls. Frontends expose them under consistent names:

| Tool / method | Purpose | Returns |
| --- | --- | --- |
| `get_log(ref)` | Fetch the full log behind a `log.ref`. **Mandatory** when any `run()` may emit `truncated: true`. | `{text: string, lines_total: int, bytes_total: int}` |
| `get_graph(ref, format?)` | Fetch graph bytes (default returns the captured format; can request a re-render to png/svg/pdf). | `{format: string, bytes_b64: string, width: int, height: int}` |
| `get_matrix(ref)` | Fetch a matrix's `values` when the producer omitted them inline. **Mandatory** when any `run()` may emit `matrices[*].ref != null`. | `{rows: [...], cols: [...], values: [[...]]}` |
| `list_sessions()` | Enumerate live sessions. | `[{session_id, started_at, last_used_at, n_obs}, ...]` |
| `reset_session(session_id?)` | Hard-reset a session (`clear all`). Invalidates all refs scoped to it. | `Result` with the cleared state. |
| `stata_info()` | Report installed Stata. | `{stata: {...}, available: bool, capabilities: [...]}` |

These are *additions* to `run()`. A minimal client only needs `run()` plus whichever auxiliaries match the truncation/ref behavior the producer can emit.

---

## 6. Versioning

`schema_version` follows semver-major.minor (currently `"1.0"`). The major bumps on breaking changes; the minor bumps on additive changes that consumers may want to detect.

**Breaking changes (major bump, e.g., `"1.0"` → `"2.0"`):**

- Removing a field
- Renaming a field, *including renaming an `error.kind` value*
- Changing a field's type or required-ness
- Tightening an enum (removing a value)

**Additive changes (minor bump, e.g., `"1.0"` → `"1.1"`):**

- Adding optional fields
- Adding new `error.kind` values (consumers MUST treat unknown as `unknown`)
- Adding new `Warning.kind` values
- Adding new auxiliary tools

**Non-bumping changes:**

- Implementation changes that don't alter the wire shape
- Documentation clarifications
- Adding entries to `capabilities`

**`capabilities` registry (v1.0):**

| Capability | Meaning |
| --- | --- |
| `log_truncation` | Producer can emit `truncated: true` and supports `get_log`. |
| `graph_ref` | Producer captures graphs and supports `get_graph`. |
| `matrix_ref` | Producer can emit large matrices as refs and supports `get_matrix`. |
| `multi_session` | Producer supports `session_id != "main"` and `list_sessions`. |
| `inline_graphs` | Producer supports `include_graphs: "inline"`. |

Consumers detect optional features via `capabilities`, not by parsing `schema_version`. Producers may add entries; agents MUST treat unknown capability names as opaque.

**Forward-compatibility contract.** Consumers (agents, kernels, frontends) MUST:

- Treat unknown `error.kind` values as `unknown`.
- Treat unknown `Warning.kind` values as `unknown`.
- Tolerate additional unknown top-level fields (do not error on them).
- Not persist `ref` strings across sessions.

When v2 ships, v1 is supported by frontends for at least 6 months. Servers MAY emit v1 to v1-clients and v2 to v2-clients (negotiated on connection or via `schema_version` requested in the call).

---

## 7. Out of scope (v1)

Explicitly *not* in this version, to keep the surface small:

- **Streaming logs.** All output is batched at end-of-call. `log.complete: false` is reserved for this in v2. Streams (Stata vs Python vs Mata) may be separated under a future `log.streams` field.
- **Cooperative cancellation.** Only `timeout_ms` for now. `error.kind: "cancelled"` is reserved.
- **Distributed / remote sessions.** Sessions are per-process. `session_id` reserves `:` for future host-prefixing.
- **Authentication / authorization.** Local trusted environment is assumed.
- **Mata internals.** Mata code runs (`stata.run("mata: ...")`) but Mata-specific return values aren't surfaced beyond what `r()` carries.
- **Frame-level dataset diffs.** `dataset.changed` is a single bit, not a diff.
- **`s()` returns.** Rarely useful outside parser-internals.
- **`e(sample)`.** Potentially huge indicator vector; not surfaced.
- **Embedded Python / Mata stdout separation.** Their output merges into `log` in emission order rather than being separately surfaced.

---

## 8. What the current scaffold doesn't yet implement

Cross-reference for the next refactor pass; tracked separately, not normative for this document:

- `log.head` / `log.tail` / `truncated` / `complete` / `error_window` / `ref` (current scaffold returns full log as one string).
- `results.r` / `results.e` separation (current scaffold flattens with `r_` / `e_` prefixes and stringifies all values via text-parsing of `return list`).
- `results.last_estimation_cmd`.
- `dataset` block (not collected).
- `graphs[].ref` and graph format restriction to png/svg/pdf.
- Structured `error` (kind taxonomy, suggestions, varname/path/name extraction, structured context).
- `request_id`, `started_at`, `stata_elapsed_ms`, `capabilities`.
- `session_id` (current scaffold is single-session only).
- `Warning` structure (current scaffold is `array<string>`).
- Request-side options `log_lines_head`, `log_lines_tail`, `include_full_log`, `graph_format`, `include_dataset_variables`.
- All auxiliary tools (`get_log`, `get_graph`, `get_matrix`, `list_sessions`, `reset_session`, `stata_info`).
- Matrix size cap and `values: null` + `ref` pattern.

The refactor will land these in a sequence that keeps `core` releasable at each step. First step is a `stata_code.core.schema` module that locks the wire types in code with tests.

---

## 9. Naming derivation

Field names in this schema were chosen from public sources only:

- Anthropic MCP specification (top-level transport shape: `content`, `inputSchema`)
- Stata Corporation documentation: `r()`, `e()`, `_rc`, `c()` system values, frame names, error code list
- Jupyter kernel protocol (status / error reply shape)
- General software conventions (`ok`, `elapsed_ms`, `truncated`, `ref`, semver, ULID)
- POSIX-shell convention (`command_not_found`)

No AGPL/GPL Stata project's source was consulted in the design of this schema. See `LICENSE-POLICY.md` for the project's clean-room policy.
