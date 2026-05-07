# 03 — Graphs: refs, not base64

> **Goal:** show that graph bytes are **not** inlined by default; the agent gets a `graph://` ref and decides whether the user actually needs to see the picture before fetching it.

## Setup

`auto.dta` is enough for this one. Default `include_graphs="ref"`, default `graph_format="png"`.

## Walkthrough

### Turn 1: produce a scatter plot

**Agent calls:**

```json
{
  "tool": "stata_run",
  "arguments": {
    "code": "sysuse auto, clear\ntwoway (scatter mpg weight) (lfit mpg weight), title(\"MPG vs. weight\")"
  }
}
```

**Server returns** (abbreviated; the graph bookkeeping is the point):

```jsonc
{
  "ok": true, "rc": 0,
  "session_id": "main",
  "request_id": "01HX...G7",
  "log": { /* head+tail of the twoway log */ },
  "results": { "r": {"scalars": {}, "macros": {}, "matrices": {}},
               "e": {"scalars": {}, "macros": {}, "matrices": {}},
               "last_estimation_cmd": null },
  "dataset": { /* auto.dta, n_obs=74, n_vars=12 */ },
  "graphs": [
    {
      "ref":            "graph://01HX...G7/0",
      "name":           "Graph",
      "format":         "png",
      "width":          800,
      "height":         600,
      "source_command": "twoway (scatter mpg weight) (lfit mpg weight), title(\"MPG vs. weight\")",
      "source_line":    2,
      "inline":         null
    }
  ],
  "warnings": [], "error": null
}
```

Critically, `inline: null`. The PNG bytes live on the server side, addressable by `graph://01HX...G7/0`, until the agent asks for them.

### Turn 2 (one path): user asks "what's the slope?"

The agent reads `e.scalars.r2` (well, `r2` is on the `regress` that generated `lfit`'s line; for an inline `lfit` overlay you would re-run `regress mpg weight` to capture the slope) — **the agent never fetches the graph**. The user wanted a number, not a picture. The 30 KB PNG stays out of the agent's window entirely.

### Turn 2 (other path): user asks "show me the chart"

Now the agent fetches.

**Agent calls:**

```json
{
  "tool": "get_graph",
  "arguments": { "ref": "graph://01HX...G7/0" }
}
```

**Server returns** an MCP `ImageContent`:

```jsonc
{
  "type":     "image",
  "data":     "<base64-encoded PNG, ~40,000 chars for a 30 KB graph>",
  "mimeType": "image/png"
}
```

For vision-capable MCP clients (Claude Desktop, Claude Code with vision, Cursor) the image renders inline. The bytes only entered the agent's context once and only because the user actually wanted to see them.

### Turn 3 (alternative): notebook frontend wants the bytes up front

A Jupyter cell knows it'll display the figure no matter what. It opts into inlining.

**Agent calls** (or the kernel calls):

```json
{
  "tool": "stata_run",
  "arguments": {
    "code": "twoway (scatter mpg weight) (lfit mpg weight)",
    "include_graphs":  "inline",
    "graph_format":    "svg"
  }
}
```

**Server returns:**

```jsonc
{
  "graphs": [
    {
      "ref":     "graph://01HX...G8/0",
      "name":    "Graph",
      "format":  "svg",
      "width":   800,
      "height":  600,
      "source_command": "twoway (scatter mpg weight) (lfit mpg weight)",
      "source_line":    1,
      "inline":  "PHN2ZyB4bWxucz0i...<base64 SVG>...PC9zdmc+"
    }
  ]
  // ... rest of envelope
}
```

`inline` is populated, `ref` is still there for cross-call references.

### Turn 4 (alternative): suppress graphs entirely

A long simulation that produces 200 intermediate graphs the agent doesn't care about.

```json
{
  "tool": "stata_run",
  "arguments": {
    "code": "forvalues i = 1/200 { qui scatter y x if rep == `i' }",
    "include_graphs": "none"
  }
}
```

`graphs: []` regardless of how many `graph` commands fired. No capture overhead, no ref store growth.

## Why this is agent-native

- The default (`include_graphs="ref"`) optimizes for the **modal MCP path**: an agent doing analysis, occasionally rendering. Fetching is one extra call, easy to authorize per-render.
- The opt-in (`include_graphs="inline"`) supports **notebook frontends** that always render, in one round trip, without sacrificing the schema.
- The opt-out (`include_graphs="none"`) supports **batch / silent runs** without paying graph-capture cost.
- Stata's native `.gph` is **never on the wire**. The producer always converts to `png` / `svg` / `pdf` at capture time. Consumers never need a Stata install to render results.

## Token economy

A typical Stata PNG export is ~30 KB. Base64 inflates by 4/3, so ~40,000 chars ≈ **~10,000 tokens** at `~4 chars/token`. (One PNG. Two graphs in one response = 20,000 tokens. A `coefplot` over a dozen specifications = 100,000+ tokens.)

|                       | `stata_code` (default)                                  | typical "dump-everything" MCP server                | savings (estimate)         |
| --------------------- | ------------------------------------------------------- | --------------------------------------------------- | -------------------------- |
| `graphs[]` payload    | ref-only entry, ~120 tokens                             | inline base64 PNG, ~10,000 tokens per graph         | **~10,000 tokens / graph** |
| Decision to fetch     | agent decides per-render                                | agent has no choice — bytes already burned          | bytes stay free for analysis |
| Notebook frontend     | opt-in `include_graphs="inline"`                        | hard-coded inline                                   | tied (same 10k tokens)     |

For a session with 5 graphs where the user actually wants to see 1: `stata_code` burns ~10,600 tokens (5 refs + 1 fetched image); a dump-everything server burns ~50,600 tokens (5 inline images). That is the gap that lets the agent keep ~40,000 tokens in its window for actual reasoning.
